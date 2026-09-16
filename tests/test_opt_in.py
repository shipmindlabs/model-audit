"""Recording is opt-in: nothing is audited until a model is registered."""

from __future__ import annotations

from model_audit import (
    MISSING,
    UNKNOWN,
    Action,
    Actor,
    actor_context,
    is_registered,
    register,
    registered_models,
    subscribe,
    unregister,
    unsubscribe,
)
from tests.models import Customer, Note


def test_an_unaudited_model_produces_nothing(records):
    note = Note.objects.create(title="Draft")

    note.title = "Release notes"
    note.save()
    note.body = "text"
    note.save(update_fields=["body"])
    Note.objects.get(pk=note.pk).save()

    assert records == []


def test_an_unaudited_model_keeps_no_state_on_its_instances():
    note = Note.objects.create(title="Draft")

    assert [name for name in vars(note) if name.startswith("_model_audit")] == []


def test_an_unaudited_model_stays_silent_next_to_an_audited_one(records):
    register(Customer)

    Note.objects.create(title="Draft")
    Customer.objects.create(name="Ada")

    (record,) = records
    assert record.model == "tests.Customer"


def test_the_same_model_records_once_it_is_registered(records):
    Note.objects.create(title="Before")
    assert records == []

    register(Note)
    Note.objects.create(title="After")

    (record,) = records
    assert record.model == "tests.Note"
    assert record.action is Action.CREATED
    assert record.changes["title"].old is MISSING


def test_registration_state_is_reported():
    assert not is_registered(Customer)
    assert registered_models() == ()

    register(Customer)

    assert is_registered(Customer)
    assert registered_models() == (Customer,)


def test_unregister_stops_the_trail(records):
    register(Customer)
    customer = Customer.objects.create(name="Ada")
    assert len(records) == 1

    unregister(Customer)
    customer.name = "Ada Lovelace"
    customer.save()

    assert len(records) == 1
    assert not is_registered(Customer)


def test_unregistering_a_model_that_was_never_registered_is_a_no_op():
    unregister(Customer)

    assert not is_registered(Customer)


def test_fields_narrows_the_trail_to_an_allow_list(records):
    register(Customer, fields=["name"])
    customer = Customer.objects.create(name="Ada", email="ada@example.com")
    records.clear()

    customer.email = "ada@lovelace.example"
    customer.save()
    assert records == []

    customer.name = "Ada Lovelace"
    customer.save()

    (record,) = records
    assert record.changes.as_dict() == {"name": ("Ada", "Ada Lovelace")}


def test_exclude_drops_a_single_field(records):
    register(Customer, exclude=["notes"])
    customer = Customer.objects.create(name="Ada")

    (created,) = records
    assert "notes" not in created.changes
    records.clear()

    customer.notes = "internal"
    customer.save()

    assert records == []


def test_update_fields_narrows_the_diff(records):
    register(Customer)
    customer = Customer.objects.create(name="Ada", email="ada@example.com")
    records.clear()

    customer.name = "Ada Lovelace"
    customer.email = "ada@lovelace.example"
    customer.save(update_fields=["email"])

    (record,) = records
    assert record.changes.as_dict() == {
        "email": ("ada@example.com", "ada@lovelace.example")
    }


def test_registering_twice_replaces_the_configuration(records):
    register(Customer, fields=["name"])
    register(Customer, fields=["email"])
    customer = Customer.objects.create(name="Ada", email="ada@example.com")
    records.clear()

    customer.name = "Ada Lovelace"
    customer.save()
    assert records == []

    customer.email = "ada@lovelace.example"
    customer.save()
    assert len(records) == 1


def test_an_instance_built_before_registration_reports_additions(records):
    customer = Customer.objects.create(name="Ada")
    register(Customer)

    customer.name = "Ada Lovelace"
    customer.save()

    (record,) = records
    assert record.action is Action.UPDATED
    assert record.changes["name"].old is MISSING
    assert record.changes["name"].new == "Ada Lovelace"


def test_a_save_without_a_bound_actor_is_recorded_as_unknown(records):
    register(Customer)

    Customer.objects.create(name="Ada")

    (record,) = records
    assert record.actor is UNKNOWN
    assert not record.actor.is_known


def test_the_ambient_actor_is_credited_with_the_change(records):
    register(Customer)

    with actor_context(Actor.service("billing-worker")):
        Customer.objects.create(name="Ada")

    (record,) = records
    assert record.actor == Actor.service("billing-worker")
    assert record.actor.is_known


def test_an_unsubscribed_receiver_stops_receiving(records):
    register(Customer)
    collected = []
    subscribe(collected.append)
    try:
        customer = Customer.objects.create(name="Ada")
    finally:
        unsubscribe(collected.append)

    customer.name = "Ada Lovelace"
    customer.save()

    assert len(collected) == 1
    assert len(records) == 2


def test_subscribe_returns_the_receiver_so_it_reads_as_a_decorator():
    def receiver(record):
        return None

    try:
        assert subscribe(receiver) is receiver
    finally:
        unsubscribe(receiver)
