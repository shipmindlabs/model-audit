"""Snapshotting on post_init, including rows read back from the database."""

from __future__ import annotations

import pytest

from model_audit import MISSING, Action, register
from tests.models import Customer


@pytest.fixture
def stored_customer() -> Customer:
    return Customer.objects.create(
        name="Ada",
        email="ada@example.com",
        phone="+31 6 1234 5678",
        notes="first",
    )


def test_reloaded_row_reports_only_the_field_that_changed(records, stored_customer):
    register(Customer)
    loaded = Customer.objects.get(pk=stored_customer.pk)

    loaded.name = "Ada Lovelace"
    loaded.save()

    (record,) = records
    assert record.action is Action.UPDATED
    assert record.pk == stored_customer.pk
    assert record.changes.as_dict() == {"name": ("Ada", "Ada Lovelace")}


def test_reloaded_row_saved_unchanged_emits_nothing(records, stored_customer):
    register(Customer)
    loaded = Customer.objects.get(pk=stored_customer.pk)

    loaded.save()

    assert records == []


def test_reloaded_row_diffs_again_after_a_second_save(records, stored_customer):
    register(Customer)
    loaded = Customer.objects.get(pk=stored_customer.pk)

    loaded.name = "Ada Lovelace"
    loaded.save()
    loaded.notes = "second"
    loaded.save()

    first, second = records
    assert first.changes.as_dict() == {"name": ("Ada", "Ada Lovelace")}
    assert second.changes.as_dict() == {"notes": ("first", "second")}


def test_creation_still_reports_every_field_as_an_addition(records):
    register(Customer)

    customer = Customer.objects.create(name="Grace", email="grace@example.com")

    (record,) = records
    assert record.action is Action.CREATED
    assert set(record.changes.fields) == {"id", "name", "email", "phone", "notes"}
    assert record.changes["name"].old is MISSING
    assert record.changes["name"].new == "Grace"
    assert record.changes["id"].new == customer.pk


def test_only_keeps_deferred_fields_out_of_snapshot_and_diff(records, stored_customer):
    register(Customer)
    loaded = Customer.objects.only("name").get(pk=stored_customer.pk)
    assert loaded.get_deferred_fields() == {"email", "phone", "notes"}

    loaded.name = "Ada Lovelace"
    loaded.save()

    (record,) = records
    assert record.changes.as_dict() == {"name": ("Ada", "Ada Lovelace")}


def test_defer_diffs_the_loaded_fields_and_skips_the_deferred_one(records, stored_customer):
    register(Customer)
    loaded = Customer.objects.defer("notes").get(pk=stored_customer.pk)

    loaded.email = "ada@lovelace.example"
    loaded.save()

    (record,) = records
    assert record.changes.as_dict() == {
        "email": ("ada@example.com", "ada@lovelace.example")
    }
    assert "notes" not in record.changes
