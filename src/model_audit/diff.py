"""Field-level diffing of model state.

The core is framework-free: it compares two mappings of field values and
returns a typed changeset. Django integration builds on top of it.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Final

__all__ = ["MISSING", "Changeset", "FieldChange", "diff"]


class _Missing:
    """Marker for a field absent from one side of a comparison."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "MISSING"

    def __bool__(self) -> bool:
        return False


MISSING: Final[_Missing] = _Missing()


@dataclass(frozen=True, slots=True)
class FieldChange:
    """A single field whose value differs between stored and incoming state."""

    field: str
    old: Any
    new: Any

    @property
    def is_addition(self) -> bool:
        return self.old is MISSING

    @property
    def is_removal(self) -> bool:
        return self.new is MISSING


@dataclass(frozen=True, slots=True)
class Changeset:
    """An ordered collection of field changes."""

    changes: tuple[FieldChange, ...] = ()

    @property
    def fields(self) -> tuple[str, ...]:
        return tuple(change.field for change in self.changes)

    def __bool__(self) -> bool:
        return bool(self.changes)

    def __len__(self) -> int:
        return len(self.changes)

    def __iter__(self) -> Iterator[FieldChange]:
        return iter(self.changes)

    def __contains__(self, field: object) -> bool:
        return any(change.field == field for change in self.changes)

    def __getitem__(self, field: str) -> FieldChange:
        for change in self.changes:
            if change.field == field:
                return change
        raise KeyError(field)

    def as_dict(self) -> dict[str, tuple[Any, Any]]:
        """Return ``{field: (old, new)}`` for logging or serialization."""
        return {change.field: (change.old, change.new) for change in self.changes}


def _equal(old: Any, new: Any) -> bool:
    if old is new:
        return True
    if old is MISSING or new is MISSING:
        return False
    # True == 1 and False == 0, but a bool replacing a number is a real change.
    if isinstance(old, bool) is not isinstance(new, bool):
        return False
    return bool(old == new)


def diff(
    stored: Mapping[str, Any],
    incoming: Mapping[str, Any],
    *,
    fields: Iterable[str] | None = None,
    exclude: Iterable[str] | None = None,
) -> Changeset:
    """Compare stored and incoming field values.

    Without ``fields``, every key of ``stored`` is compared, followed by keys
    that only ``incoming`` carries. A field missing from one side yields
    ``MISSING`` on that side; missing from both sides counts as unchanged.
    """
    if fields is None:
        candidates = list(stored)
        candidates += [field for field in incoming if field not in stored]
    else:
        candidates = list(fields)

    skipped = frozenset(exclude or ())
    changes = []
    for field in candidates:
        if field in skipped:
            continue
        old = stored.get(field, MISSING)
        new = incoming.get(field, MISSING)
        if not _equal(old, new):
            changes.append(FieldChange(field, old, new))

    return Changeset(tuple(changes))
