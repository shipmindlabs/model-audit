"""Deletions: every audited field of the removed row reported as a removal."""

from __future__ import annotations

from model_audit import (
    MISSING,
    REDACTED,
    Action,
    Actor,
    actor_context,
    register,
    unregister,
)
from tests.models import Customer, Note


def test_a_deletion_reports_every_audited_field_as_a_removal(records):
    register(Customer)
    customer = Customer.objects.create(name="Ada", email="ada@example.com")
    records.clear()

    customer.delete()

    (record,) = records
    assert record.action is Action.DELETED
    assert record.as_dict()["action"] == "deleted"
    assert set(record.changes.fields) == {"id", "name", "email", "phone", "notes"}
    assert record.changes["name"].old == "Ada"
    assert record.changes["name"].new is MISSING
    assert record.changes["name"].is_removal
    assert not record.changes["name"].is_addition


def test_the_record_still_names_the_row_that_was_deleted(records):
    register(Customer)
    customer = Customer.objects.create(name="Ada")
    pk = customer.pk
    records.clear()

    customer.delete()

    (record,) = records
    assert record.pk == pk
    assert record.changes["id"].old == pk
    assert record.changes["id"].new is MISSING


def test_deleting_an_unaudited_model_emits_nothing(records):
    note = Note.objects.create(title="Draft")
    note.delete()
    Note.objects.create(title="Release notes").delete()

    assert records == []


def test_an_unaudited_model_stays_silent_next_to_an_audited_deletion(records):
    register(Customer)
    Note.objects.create(title="Draft").delete()
    Customer.objects.create(name="Ada").delete()

    assert [record.action for record in records] == [Action.CREATED, Action.DELETED]
    assert {record.model for record in records} == {"tests.Customer"}


def test_a_queryset_delete_emits_one_record_per_row(records):
    register(Customer)
    Customer.objects.create(name="Ada")
    Customer.objects.create(name="Grace")
    records.clear()

    Customer.objects.all().delete()

    assert [record.action for record in records] == [Action.DELETED, Action.DELETED]
    assert {record.changes["name"].old for record in records} == {"Ada", "Grace"}


def test_a_deletion_withholds_the_value_of_a_sensitive_field(records):
    register(Customer)
    customer = Customer.objects.create(name="Ada", phone="+31 6 1234 5678")
    records.clear()

    customer.delete()

    (record,) = records
    assert record.changes["phone"].old is REDACTED
    assert record.changes["phone"].new is MISSING
    assert record.changes["phone"].is_removal


def test_a_deletion_is_attributed_like_a_save(records):
    register(Customer)
    customer = Customer.objects.create(name="Ada")
    records.clear()

    with actor_context(Actor.service("billing-worker")):
        customer.delete()

    (record,) = records
    assert record.actor == Actor.service("billing-worker")


def test_fields_narrows_the_removal_to_the_allow_list(records):
    register(Customer, fields=["name"])
    customer = Customer.objects.create(name="Ada", email="ada@example.com")
    records.clear()

    customer.delete()

    (record,) = records
    assert record.changes.fields == ("name",)


def test_exclude_keeps_a_field_out_of_the_removal(records):
    register(Customer, exclude=["notes"])
    customer = Customer.objects.create(name="Ada", notes="internal")
    records.clear()

    customer.delete()

    (record,) = records
    assert "notes" not in record.changes
    assert "name" in record.changes


def test_unregister_stops_recording_deletions(records):
    register(Customer)
    customer = Customer.objects.create(name="Ada")
    unregister(Customer)
    records.clear()

    customer.delete()

    assert records == []
