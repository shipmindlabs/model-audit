"""Django adapter: record field changes when a model is saved.

Auditing is opt-in per model. ``register()`` connects ``post_init`` to keep a
snapshot of the row as it was loaded and ``post_save`` to diff that snapshot
against the instance that was written; the resulting record is handed to the
subscribed receivers. Models that were never registered emit nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from django.core.exceptions import FieldDoesNotExist
from django.db.models import Model
from django.db.models.signals import post_init, post_save
from django.utils import timezone

from model_audit.actor import Actor, resolve_actor
from model_audit.diff import Changeset, diff

__all__ = [
    "NOISY_FIELDS",
    "Action",
    "AuditRecord",
    "Receiver",
    "is_registered",
    "register",
    "registered_models",
    "subscribe",
    "unregister",
    "unsubscribe",
]

# Timestamps that every save touches; auditing them turns the trail into noise.
NOISY_FIELDS: tuple[str, ...] = (
    "modified",
    "modified_at",
    "updated",
    "updated_at",
    "last_seen",
)

_STATE_ATTR = "_model_audit_stored"


class Action(str, Enum):
    """What the save did to the row."""

    CREATED = "created"
    UPDATED = "updated"


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """One audited save: which row, by whom, and what changed."""

    model: str
    pk: Any
    action: Action
    actor: Actor
    changes: Changeset
    at: datetime

    def as_dict(self) -> dict[str, Any]:
        """Return a plain mapping for logging or serialization."""
        return {
            "model": self.model,
            "pk": self.pk,
            "action": self.action.value,
            "actor": self.actor.as_dict(),
            "changes": self.changes.as_dict(),
            "at": self.at.isoformat(),
        }


Receiver = Callable[[AuditRecord], None]


@dataclass(frozen=True, slots=True)
class _Registration:
    fields: frozenset[str] | None
    exclude: frozenset[str]


_registrations: dict[type[Model], _Registration] = {}
_receivers: list[Receiver] = []


def register(
    model: type[Model],
    *,
    fields: Iterable[str] | None = None,
    exclude: Iterable[str] | None = None,
    ignore_noisy: bool = True,
) -> type[Model]:
    """Start recording saves of ``model``.

    ``fields`` restricts auditing to an allow-list, ``exclude`` drops single
    fields from it. Both accept a foreign key by its name or by its column
    attribute (``author`` or ``author_id``). Registering twice replaces the
    previous configuration.
    """
    excluded = set(exclude or ())
    if ignore_noisy:
        excluded.update(NOISY_FIELDS)

    _registrations[model] = _Registration(
        fields=frozenset(fields) if fields is not None else None,
        exclude=frozenset(excluded),
    )

    uid = _dispatch_uid(model)
    post_init.connect(_on_post_init, sender=model, dispatch_uid=uid, weak=False)
    post_save.connect(_on_post_save, sender=model, dispatch_uid=uid, weak=False)
    return model


def unregister(model: type[Model]) -> None:
    """Stop recording saves of ``model``; a no-op if it was not registered."""
    if _registrations.pop(model, None) is None:
        return
    uid = _dispatch_uid(model)
    post_init.disconnect(sender=model, dispatch_uid=uid)
    post_save.disconnect(sender=model, dispatch_uid=uid)


def is_registered(model: type[Model]) -> bool:
    return model in _registrations


def registered_models() -> tuple[type[Model], ...]:
    return tuple(_registrations)


def subscribe(receiver: Receiver) -> Receiver:
    """Call ``receiver`` for every recorded save. Usable as a decorator."""
    if receiver not in _receivers:
        _receivers.append(receiver)
    return receiver


def unsubscribe(receiver: Receiver) -> None:
    if receiver in _receivers:
        _receivers.remove(receiver)


def _dispatch_uid(model: type[Model]) -> str:
    return f"model_audit:{model._meta.label_lower}"


def _audited(field: Any, registration: _Registration) -> bool:
    names = (field.name, field.attname)
    if any(name in registration.exclude for name in names):
        return False
    if registration.fields is None:
        return True
    return any(name in registration.fields for name in names)


def _snapshot(instance: Model, registration: _Registration) -> dict[str, Any]:
    deferred = instance.get_deferred_fields()
    values: dict[str, Any] = {}
    for field in instance._meta.concrete_fields:
        # attname keeps a relation as its stored id, so reading it never
        # triggers a query for the related object.
        if field.attname in deferred or not _audited(field, registration):
            continue
        values[field.attname] = getattr(instance, field.attname)
    return values


def _touched_attnames(model: type[Model], update_fields: Iterable[str]) -> set[str]:
    attnames = set()
    for name in update_fields:
        try:
            attnames.add(model._meta.get_field(name).attname)
        except FieldDoesNotExist:
            attnames.add(name)
    return attnames


def _on_post_init(sender: type[Model], instance: Model, **kwargs: Any) -> None:
    registration = _registrations.get(sender)
    if registration is None:
        return
    stored = {} if instance._state.adding else _snapshot(instance, registration)
    setattr(instance, _STATE_ATTR, stored)


def _on_post_save(
    sender: type[Model],
    instance: Model,
    created: bool,
    update_fields: Iterable[str] | None = None,
    **kwargs: Any,
) -> None:
    registration = _registrations.get(sender)
    if registration is None:
        return

    incoming = _snapshot(instance, registration)
    # An instance built before its model was registered carries no snapshot;
    # its fields are then reported as additions rather than guessed at.
    stored: dict[str, Any] = {} if created else getattr(instance, _STATE_ATTR, {})
    setattr(instance, _STATE_ATTR, incoming)

    fields = None
    if update_fields is not None and not created:
        touched = _touched_attnames(sender, update_fields)
        fields = [name for name in incoming if name in touched]

    changes = diff(stored, incoming, fields=fields)
    if not changes and not created:
        return

    record = AuditRecord(
        model=sender._meta.label,
        pk=instance.pk,
        action=Action.CREATED if created else Action.UPDATED,
        actor=resolve_actor(None, ambient=True),
        changes=changes,
        at=timezone.now(),
    )
    for receiver in tuple(_receivers):
        receiver(record)
