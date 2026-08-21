"""Actor resolution: who made a change.

Resolution is explicit by default: the caller passes an actor, a Django user,
or a request at the call site. An ambient (context-local) actor is available
for code that cannot thread one through, but it has to be opted into.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final

__all__ = [
    "ANONYMOUS",
    "SYSTEM",
    "UNKNOWN",
    "Actor",
    "ActorKind",
    "actor_context",
    "current_actor",
    "reset_current_actor",
    "resolve_actor",
    "set_current_actor",
]


class ActorKind(str, Enum):
    """What sort of party a change is attributed to."""

    USER = "user"
    SERVICE = "service"
    ANONYMOUS = "anonymous"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Actor:
    """The party credited with a change, in a form safe to store and log."""

    kind: ActorKind
    id: str | None = None
    label: str = ""

    @property
    def is_known(self) -> bool:
        return self.kind in (ActorKind.USER, ActorKind.SERVICE)

    def as_dict(self) -> dict[str, Any]:
        """Return ``{kind, id, label}`` for logging or serialization."""
        return {"kind": self.kind.value, "id": self.id, "label": self.label}

    def __str__(self) -> str:
        if self.label:
            return self.label
        if self.id is not None:
            return f"{self.kind.value}:{self.id}"
        return self.kind.value

    @classmethod
    def user(cls, identifier: Any, label: str = "") -> Actor:
        identifier = None if identifier is None else str(identifier)
        return cls(ActorKind.USER, identifier, label or (identifier or ""))

    @classmethod
    def service(cls, name: str) -> Actor:
        return cls(ActorKind.SERVICE, name, name)


ANONYMOUS: Final[Actor] = Actor(ActorKind.ANONYMOUS, None, "anonymous")
UNKNOWN: Final[Actor] = Actor(ActorKind.UNKNOWN, None, "unknown")
SYSTEM: Final[Actor] = Actor.service("system")

_current_actor: Final[ContextVar[Actor]] = ContextVar("model_audit_actor", default=UNKNOWN)


def _actor_from_user(user: Any) -> Actor:
    if not bool(getattr(user, "is_authenticated", False)):
        return ANONYMOUS
    get_username = getattr(user, "get_username", None)
    label = get_username() if callable(get_username) else str(user)
    return Actor.user(getattr(user, "pk", None), label)


def resolve_actor(
    source: Any = None,
    *,
    ambient: bool = False,
    fallback: Actor = UNKNOWN,
) -> Actor:
    """Turn ``source`` into an :class:`Actor`.

    Accepts an ``Actor`` (returned as is), a string naming a service, a Django
    user, or anything carrying a ``user`` attribute such as an ``HttpRequest``.
    An unauthenticated user resolves to ``ANONYMOUS``; ``None`` resolves to
    ``fallback``, or to the ambient actor when ``ambient=True``.
    """
    if isinstance(source, Actor):
        return source
    if source is None:
        return current_actor() if ambient else fallback
    if isinstance(source, str):
        return Actor.service(source)

    request_user = getattr(source, "user", None)
    if request_user is not None:
        source = request_user
    if hasattr(source, "is_authenticated"):
        return _actor_from_user(source)

    raise TypeError(f"cannot resolve an actor from {type(source).__name__}")


def current_actor() -> Actor:
    """Return the ambient actor, or ``UNKNOWN`` when none is bound."""
    return _current_actor.get()


def set_current_actor(source: Any) -> Token[Actor]:
    """Bind the ambient actor and return a token for :func:`reset_current_actor`."""
    return _current_actor.set(resolve_actor(source))


def reset_current_actor(token: Token[Actor]) -> None:
    """Restore the ambient actor that was bound before ``token`` was issued."""
    _current_actor.reset(token)


@contextmanager
def actor_context(source: Any) -> Iterator[Actor]:
    """Bind the ambient actor for the duration of the block.

    The binding is context-local: it follows ``await`` within a task but is not
    inherited by threads or executor workers started inside the block, which is
    why call-site arguments remain the default.
    """
    actor = resolve_actor(source)
    token = _current_actor.set(actor)
    try:
        yield actor
    finally:
        _current_actor.reset(token)
