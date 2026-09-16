"""Redaction: a sensitive field keeps its place in the trail, its value does not."""

from __future__ import annotations

import json

from model_audit import (
    DEFAULT_REDACTOR,
    MISSING,
    REDACTED,
    SENSITIVE_FIELDS,
    Changeset,
    FieldChange,
    Redactor,
    diff,
    is_sensitive,
    redact,
    redact_data,
    register,
)
from tests.models import Customer


def test_names_that_read_like_a_secret_are_sensitive():
    for field in ("password", "auth_token", "customer_phone", "passport_number", "iban"):
        assert is_sensitive(field)


def test_ordinary_names_are_left_alone():
    for field in ("name", "email", "city", "title", "notes"):
        assert not is_sensitive(field)


def test_matching_ignores_case_hyphens_and_surrounding_space():
    assert is_sensitive("X-API-Key")
    assert is_sensitive("  Authorization  ")


def test_defaults_cover_the_documented_names():
    assert {"token", "password", "secret", "phone", "passport", "iban", "ssn"} <= set(
        SENSITIVE_FIELDS
    )


def test_redacted_is_a_string_with_an_identity_of_its_own():
    lookalike = "[redacted]"

    assert isinstance(REDACTED, str)
    assert REDACTED == lookalike
    assert lookalike is not REDACTED
    assert repr(REDACTED) == "REDACTED"


def test_value_withholds_only_sensitive_fields():
    assert DEFAULT_REDACTOR.value("name", "Ada") == "Ada"
    assert DEFAULT_REDACTOR.value("phone", "+31 6 1234 5678") is REDACTED
    assert DEFAULT_REDACTOR.value("phone", MISSING) is MISSING


def test_a_redacted_addition_still_reads_as_one():
    redacted = DEFAULT_REDACTOR.change(FieldChange("phone", MISSING, "+31 6 1234 5678"))

    assert redacted.old is MISSING
    assert redacted.new is REDACTED
    assert redacted.is_addition


def test_changeset_withholds_every_sensitive_field():
    changeset = Changeset(
        (FieldChange("name", "Ada", "Grace"), FieldChange("api_key", "a", "b"))
    )

    result = DEFAULT_REDACTOR.changeset(changeset)

    assert result.as_dict() == {"name": ("Ada", "Grace"), "api_key": (REDACTED, REDACTED)}
    assert result["api_key"].new is REDACTED


def test_redact_takes_a_custom_redactor():
    changeset = diff({"nickname": "ada"}, {"nickname": "lovelace"})

    assert redact(changeset).as_dict() == {"nickname": ("ada", "lovelace")}
    hidden = redact(changeset, redactor=DEFAULT_REDACTOR.extend(["nickname"]))
    assert hidden.as_dict() == {"nickname": (REDACTED, REDACTED)}


def test_allow_releases_a_field_caught_by_a_pattern():
    redactor = Redactor(allow=["document_type"])

    assert not redactor.is_sensitive("document_type")
    assert redactor.is_sensitive("document_number")


def test_extend_returns_a_new_redactor_and_leaves_the_old_one_alone():
    extended = DEFAULT_REDACTOR.extend(["nickname"])

    assert extended.is_sensitive("nickname")
    assert not DEFAULT_REDACTOR.is_sensitive("nickname")

    released = extended.extend(allow=["phone"])
    assert not released.is_sensitive("phone")
    assert released.is_sensitive("nickname")


def test_patterns_are_normalized_and_deduplicated():
    redactor = Redactor(patterns=("Phone", "phone", "API-Key"))

    assert redactor.patterns == ("phone", "api_key")


def test_data_walks_mappings_and_lists():
    payload = {
        "user": {"phone": "+31 6 1234 5678", "city": "Utrecht"},
        "entries": [{"token": "tok_live_9f3"}, {"id": 2}],
    }

    assert redact_data(payload) == {
        "user": {"phone": "[redacted]", "city": "Utrecht"},
        "entries": [{"token": "[redacted]"}, {"id": 2}],
    }


def test_a_sensitive_key_withholds_the_whole_subtree():
    result = redact_data({"credentials": {"user": "ada", "password": "hunter2"}})

    assert result["credentials"] is REDACTED


def test_data_leaves_strings_bytes_and_scalars_as_they_are():
    assert redact_data("+31 6 1234 5678") == "+31 6 1234 5678"
    assert redact_data(b"raw") == b"raw"
    assert redact_data(5) == 5
    assert redact_data(None) is None


def test_data_returns_a_list_for_any_other_sequence():
    assert redact_data(("a", {"secret": "s"})) == ["a", {"secret": "[redacted]"}]


def test_model_record_withholds_the_value_of_a_sensitive_field(records):
    register(Customer)
    customer = Customer.objects.create(name="Ada", phone="+31 6 1234 5678")

    (created,) = records
    assert created.changes["phone"].old is MISSING
    assert created.changes["phone"].new is REDACTED
    assert created.changes["name"].new == "Ada"

    records.clear()
    customer.phone = "+31 6 8765 4321"
    customer.save()

    (updated,) = records
    assert updated.changes.as_dict() == {"phone": (REDACTED, REDACTED)}
    assert updated.changes["phone"].old is REDACTED


def test_a_model_can_name_extra_sensitive_fields(records):
    register(Customer, redact=["notes"])
    customer = Customer.objects.create(name="Ada", notes="first")
    records.clear()

    customer.notes = "second"
    customer.email = "ada@example.com"
    customer.save()

    (record,) = records
    assert record.changes["notes"].new is REDACTED
    assert record.changes["email"].new == "ada@example.com"


def test_a_model_can_release_a_field_caught_by_the_defaults(records):
    register(Customer, redact=Redactor(allow=["phone"]))
    customer = Customer.objects.create(name="Ada", phone="+31 6 1234 5678")
    records.clear()

    customer.phone = "+31 6 8765 4321"
    customer.save()

    (record,) = records
    assert record.changes.as_dict() == {
        "phone": ("+31 6 1234 5678", "+31 6 8765 4321")
    }


def test_a_record_with_redacted_values_still_serializes(records):
    register(Customer)
    customer = Customer.objects.create(name="Ada", phone="+31 6 1234 5678")
    records.clear()

    customer.phone = "+31 6 8765 4321"
    customer.save()

    (record,) = records
    payload = json.loads(json.dumps(record.as_dict()))
    assert payload["changes"]["phone"] == ["[redacted]", "[redacted]"]
