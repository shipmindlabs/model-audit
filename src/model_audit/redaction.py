"""Redaction of sensitive values.

An audit trail is meant to record *that* a field changed, not to become a
second copy of the data it watches. Fields whose name reads like a secret, a
phone number or an identity document keep their place in the trail, but their
values are replaced by :data:`REDACTED` before a record leaves the process.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from model_audit.diff import MISSING, Changeset, FieldChange

__all__ = [
    "DEFAULT_REDACTOR",
    "REDACTED",
    "SENSITIVE_FIELDS",
    "Redactor",
    "is_sensitive",
    "redact",
    "redact_data",
]


class _Redacted(str):
    """Stand-in for a value withheld from the audit trail."""

    __slots__ = ()

    def __new__(cls) -> _Redacted:
        return super().__new__(cls, "[redacted]")

    def __repr__(self) -> str:
        return "REDACTED"


# A str subclass, so a record still serializes without special-casing, while
# ``value is REDACTED`` separates it from a value that merely looks like one.
REDACTED: Final[_Redacted] = _Redacted()

# Substrings matched case-insensitively against a field name.
SENSITIVE_FIELDS: tuple[str, ...] = (
    "access_key",
    "api_key",
    "authorization",
    "bank_account",
    "cardholder",
    "card_number",
    "credential",
    "credit_card",
    "cvv",
    "document",
    "iban",
    "id_card",
    "otp",
    "passport",
    "password",
    "phone",
    "pin_code",
    "private_key",
    "secret",
    "security_code",
    "session_key",
    "signature",
    "ssn",
    "tax_id",
    "telephone",
    "token",
)


def _normalize(name: str) -> str:
    return name.strip().lower().replace("-", "_")


@dataclass(frozen=True, slots=True)
class Redactor:
    """Decides which field names carry values that must not be recorded."""

    patterns: tuple[str, ...] = SENSITIVE_FIELDS
    allow: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        patterns = tuple(dict.fromkeys(_normalize(pattern) for pattern in self.patterns))
        object.__setattr__(self, "patterns", patterns)
        object.__setattr__(self, "allow", frozenset(_normalize(name) for name in self.allow))

    def is_sensitive(self, field: str) -> bool:
        name = _normalize(field)
        if name in self.allow:
            return False
        return any(pattern in name for pattern in self.patterns)

    def value(self, field: str, value: Any) -> Any:
        """Return ``value`` itself, or ``REDACTED`` when ``field`` is sensitive."""
        if value is MISSING or not self.is_sensitive(field):
            return value
        return REDACTED

    def change(self, change: FieldChange) -> FieldChange:
        if not self.is_sensitive(change.field):
            return change
        # MISSING survives redaction so an addition stays readable as one.
        old = change.old if change.old is MISSING else REDACTED
        new = change.new if change.new is MISSING else REDACTED
        return FieldChange(change.field, old, new)

    def changeset(self, changeset: Changeset) -> Changeset:
        """Return ``changeset`` with the values of sensitive fields withheld."""
        return Changeset(tuple(self.change(change) for change in changeset))

    def data(self, payload: Any) -> Any:
        """Return a copy of ``payload`` with sensitive entries replaced.

        Mappings and sequences are walked recursively, which is the shape a
        request or response body has on its way to a log sink.
        """
        if isinstance(payload, Mapping):
            return {
                key: REDACTED if self.is_sensitive(str(key)) else self.data(value)
                for key, value in payload.items()
            }
        if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
            return [self.data(item) for item in payload]
        return payload

    def extend(self, patterns: Iterable[str] = (), *, allow: Iterable[str] = ()) -> Redactor:
        """Return a redactor with more patterns, or more names released."""
        return Redactor(
            patterns=self.patterns + tuple(patterns),
            allow=self.allow | frozenset(_normalize(name) for name in allow),
        )


DEFAULT_REDACTOR: Final[Redactor] = Redactor()


def is_sensitive(field: str, *, redactor: Redactor | None = None) -> bool:
    """Report whether ``field`` names a value that must not be recorded."""
    return (redactor or DEFAULT_REDACTOR).is_sensitive(field)


def redact(changeset: Changeset, *, redactor: Redactor | None = None) -> Changeset:
    """Return ``changeset`` with the values of sensitive fields withheld."""
    return (redactor or DEFAULT_REDACTOR).changeset(changeset)


def redact_data(payload: Any, *, redactor: Redactor | None = None) -> Any:
    """Return a copy of ``payload`` with sensitive entries replaced."""
    return (redactor or DEFAULT_REDACTOR).data(payload)
