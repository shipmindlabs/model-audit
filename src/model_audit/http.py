"""Django adapter: audit HTTP requests declared in a route map.

Which endpoints are audited, and how much of each request is kept, is declared
in a single :class:`RouteMap` instead of conditions spread across a middleware.
The middleware only looks the request up in that map: a path that matches
nothing, or one mapped to :meth:`AuditRoute.off`, costs the lookup and nothing
else -- no body is read, no actor is resolved, no record is built.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Flag, auto
from json import JSONDecodeError, loads
from time import perf_counter
from typing import Any, Final

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.http import (
    HttpRequest,
    HttpResponse,
    RawPostDataException,
    UnreadablePostError,
)
from django.utils import timezone

from model_audit.actor import (
    UNKNOWN,
    Actor,
    reset_current_actor,
    resolve_actor,
    set_current_actor,
)
from model_audit.redaction import DEFAULT_REDACTOR, REDACTED, Redactor

__all__ = [
    "MAX_BODY_BYTES",
    "WRITE_METHODS",
    "AuditMiddleware",
    "AuditRoute",
    "Capture",
    "RequestReceiver",
    "RequestRecord",
    "RouteMap",
    "audit_routes",
    "routes",
    "subscribe_requests",
    "unsubscribe_requests",
]

WRITE_METHODS: Final[frozenset[str]] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# A trail is an index of what happened, not a mirror of the traffic.
MAX_BODY_BYTES: Final[int] = 64 * 1024

# Header names that carry credentials but read like nothing in particular.
_HEADER_PATTERNS: Final[tuple[str, ...]] = ("cookie", "proxy_authorization")


class Capture(Flag):
    """How much of a request is kept beyond the fact that it happened."""

    NOTHING = 0
    QUERY = auto()
    BODY = auto()
    HEADERS = auto()
    RESPONSE = auto()
    FULL = QUERY | BODY | HEADERS | RESPONSE


@dataclass(frozen=True, slots=True)
class AuditRoute:
    """What auditing a matched endpoint gets.

    ``methods`` restricts the route to those verbs; ``None`` matches every
    verb. ``pattern`` is filled in by the :class:`RouteMap` that owns the
    route, so a record can name the declaration it came from.
    """

    capture: Capture = Capture.NOTHING
    methods: frozenset[str] | None = None
    label: str = ""
    redactor: Redactor | None = None
    audit: bool = True
    pattern: str = ""

    def __post_init__(self) -> None:
        if self.methods is not None:
            methods = frozenset(method.strip().upper() for method in self.methods)
            object.__setattr__(self, "methods", methods)

    @classmethod
    def off(cls) -> AuditRoute:
        """A route that is matched in order to be left out of the trail."""
        return cls(audit=False)

    @property
    def name(self) -> str:
        return self.label or self.pattern


OFF: Final[AuditRoute] = AuditRoute.off()


@dataclass(frozen=True, slots=True)
class RequestRecord:
    """One audited request: what was called, by whom, and how it ended."""

    method: str
    path: str
    route: str
    label: str
    status: int | None
    actor: Actor
    payload: dict[str, Any]
    duration_ms: float
    at: datetime
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a plain mapping for logging or serialization."""
        return {
            "method": self.method,
            "path": self.path,
            "route": self.route,
            "label": self.label,
            "status": self.status,
            "actor": self.actor.as_dict(),
            "payload": self.payload,
            "duration_ms": self.duration_ms,
            "at": self.at.isoformat(),
            "error": self.error,
        }


RequestReceiver = Callable[[RequestRecord], None]

RouteSpec = AuditRoute | Capture | bool


def _split(path: str) -> tuple[str, ...]:
    return tuple(segment for segment in path.split("/") if segment)


def _parse_pattern(pattern: str) -> tuple[frozenset[str] | None, str]:
    """Split an optional ``"POST,PUT /path"`` prefix off a pattern."""
    text = pattern.strip()
    head, separator, rest = text.partition(" ")
    if not separator or head.startswith("/"):
        return None, text
    methods = frozenset(method.strip().upper() for method in head.split(",") if method.strip())
    return methods or None, rest.strip()


def _coerce_route(spec: RouteSpec) -> AuditRoute:
    if isinstance(spec, AuditRoute):
        return spec
    if isinstance(spec, bool):
        return AuditRoute() if spec else OFF
    if isinstance(spec, Capture):
        return AuditRoute(capture=spec)
    raise TypeError(f"cannot read a route from {type(spec).__name__}")


def _match(pattern: tuple[str, ...], path: tuple[str, ...]) -> int | None:
    """Return the number of literal segments matched, or ``None``.

    ``*`` stands for one segment, ``**`` for any number of trailing ones.
    """
    if not pattern:
        return 0 if not path else None
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        for index in range(len(path) + 1):
            score = _match(rest, path[index:])
            if score is not None:
                return score
        return None
    if not path:
        return None
    if head == "*":
        return _match(rest, path[1:])
    if head != path[0]:
        return None
    score = _match(rest, path[1:])
    return None if score is None else score + 1


@dataclass(frozen=True, slots=True)
class _Rule:
    segments: tuple[str, ...]
    route: AuditRoute


class RouteMap:
    """Which endpoints are audited and how, declared in one place.

    Keys are path patterns, optionally prefixed with the verbs they apply to
    (``"POST /api/orders"``). Values are an :class:`AuditRoute`, a
    :class:`Capture` for a route that only sets capture, or ``False`` to keep
    an endpoint out of the trail.
    """

    __slots__ = ("_rules",)

    def __init__(self, declaration: Mapping[str, RouteSpec] | None = None) -> None:
        rules = []
        for pattern, spec in (declaration or {}).items():
            methods, path = _parse_pattern(pattern)
            route = _coerce_route(spec)
            rules.append(
                _Rule(
                    segments=_split(path),
                    route=replace(
                        route,
                        methods=route.methods if route.methods is not None else methods,
                        pattern=pattern.strip(),
                    ),
                )
            )
        self._rules: tuple[_Rule, ...] = tuple(rules)

    @property
    def patterns(self) -> tuple[str, ...]:
        return tuple(rule.route.pattern for rule in self._rules)

    def __len__(self) -> int:
        return len(self._rules)

    def __bool__(self) -> bool:
        return bool(self._rules)

    def __repr__(self) -> str:
        return f"RouteMap({list(self.patterns)!r})"

    def extend(self, declaration: Mapping[str, RouteSpec]) -> RouteMap:
        """Return a map with more routes declared on top of this one."""
        extended = RouteMap()
        extended._rules = self._rules + RouteMap(declaration)._rules
        return extended

    def match(self, path: str, method: str = "GET") -> AuditRoute | None:
        """Return the route auditing ``method path``, or ``None``.

        The most specific declaration wins: more literal segments first, then
        an explicit method list, then a pattern without ``**``. A broad
        ``"/api/**": False`` therefore stays overridable per endpoint.
        """
        segments = _split(path)
        verb = method.upper()
        best: AuditRoute | None = None
        best_key: tuple[int, bool, bool, int] | None = None
        for rule in self._rules:
            route = rule.route
            if route.methods is not None and verb not in route.methods:
                continue
            score = _match(rule.segments, segments)
            if score is None:
                continue
            key = (
                score,
                route.methods is not None,
                "**" not in rule.segments,
                len(rule.segments),
            )
            if best_key is None or key > best_key:
                best, best_key = route, key
        if best is None or not best.audit:
            return None
        return best


_routes: RouteMap = RouteMap()
_receivers: list[RequestReceiver] = []


def audit_routes(declaration: RouteMap | Mapping[str, RouteSpec] | None = None) -> RouteMap:
    """Declare the audited endpoints, replacing any previous declaration."""
    global _routes
    _routes = declaration if isinstance(declaration, RouteMap) else RouteMap(declaration)
    return _routes


def routes() -> RouteMap:
    """Return the route map currently in force."""
    return _routes


def subscribe_requests(receiver: RequestReceiver) -> RequestReceiver:
    """Call ``receiver`` for every audited request. Usable as a decorator."""
    if receiver not in _receivers:
        _receivers.append(receiver)
    return receiver


def unsubscribe_requests(receiver: RequestReceiver) -> None:
    if receiver in _receivers:
        _receivers.remove(receiver)


def _pairs(data: Any) -> dict[str, Any]:
    return {key: values[0] if len(values) == 1 else list(values) for key, values in data.lists()}


def _request_actor(request: HttpRequest) -> Actor:
    try:
        return resolve_actor(request)
    except TypeError:
        # No authentication middleware ahead of us; an unattributed request is
        # recorded rather than raised.
        return UNKNOWN


def _query(request: HttpRequest, redactor: Redactor) -> dict[str, Any]:
    return redactor.data(_pairs(request.GET))


def _headers(request: HttpRequest, redactor: Redactor) -> dict[str, Any]:
    header_redactor = redactor.extend(_HEADER_PATTERNS)
    return {
        name: REDACTED if header_redactor.is_sensitive(name) else value
        for name, value in request.headers.items()
    }


def _body(request: HttpRequest, redactor: Redactor) -> Any:
    content_type = (request.content_type or "").lower()
    if content_type.startswith(("multipart/form-data", "application/x-www-form-urlencoded")):
        return redactor.data(_pairs(request.POST))
    if "json" not in content_type:
        return {"omitted": "unsupported content type", "content_type": content_type}
    try:
        raw = request.body
    except (RawPostDataException, RequestDataTooBig, UnreadablePostError):
        return {"omitted": "body unavailable"}
    if not raw:
        return None
    if len(raw) > MAX_BODY_BYTES:
        return {"omitted": "body too large", "bytes": len(raw)}
    try:
        payload = loads(raw.decode(request.encoding or "utf-8"))
    except (JSONDecodeError, UnicodeDecodeError):
        return {"omitted": "unparsable body"}
    return redactor.data(payload)


def _response_body(response: HttpResponse, redactor: Redactor) -> Any:
    if getattr(response, "streaming", False):
        return {"omitted": "streaming response"}
    content = getattr(response, "content", b"")
    if not content:
        return None
    if "json" not in response.headers.get("Content-Type", "").lower():
        return {"omitted": "unsupported content type"}
    if len(content) > MAX_BODY_BYTES:
        return {"omitted": "response too large", "bytes": len(content)}
    try:
        payload = loads(content.decode("utf-8"))
    except (JSONDecodeError, UnicodeDecodeError):
        return {"omitted": "unparsable response"}
    return redactor.data(payload)


def _emit(record: RequestRecord) -> None:
    for receiver in tuple(_receivers):
        receiver(record)


class AuditMiddleware:
    """Record the requests the route map declares as audited.

    Place it after the authentication middleware: the actor of an audited
    request is bound as the ambient one for the duration of the view, so model
    records written along the way are attributed to the same party.
    """

    sync_capable = True
    async_capable = False

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response
        declared = getattr(settings, "MODEL_AUDIT_ROUTES", None)
        if declared is not None:
            audit_routes(declared)

    def __call__(self, request: HttpRequest) -> HttpResponse:
        route = _routes.match(request.path, request.method or "GET")
        if route is None:
            return self.get_response(request)

        redactor = route.redactor or DEFAULT_REDACTOR
        payload: dict[str, Any] = {}
        if route.capture & Capture.QUERY:
            payload["query"] = _query(request, redactor)
        if route.capture & Capture.BODY:
            payload["body"] = _body(request, redactor)
        if route.capture & Capture.HEADERS:
            payload["headers"] = _headers(request, redactor)

        token = set_current_actor(_request_actor(request))
        started = perf_counter()
        try:
            response = self.get_response(request)
        except Exception as exc:
            self._record(request, route, payload, started, status=None, error=type(exc).__name__)
            raise
        else:
            if route.capture & Capture.RESPONSE:
                payload["response"] = _response_body(response, redactor)
            self._record(request, route, payload, started, status=response.status_code)
            return response
        finally:
            reset_current_actor(token)

    def _record(
        self,
        request: HttpRequest,
        route: AuditRoute,
        payload: dict[str, Any],
        started: float,
        *,
        status: int | None,
        error: str | None = None,
    ) -> None:
        _emit(
            RequestRecord(
                method=(request.method or "").upper(),
                path=request.path,
                route=route.pattern,
                label=route.name,
                status=status,
                # Resolved after the view, so a login endpoint reports the
                # party it authenticated rather than the anonymous caller.
                actor=_request_actor(request),
                payload=payload,
                duration_ms=round((perf_counter() - started) * 1000, 3),
                at=timezone.now(),
                error=error,
            )
        )
