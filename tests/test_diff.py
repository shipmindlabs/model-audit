"""The framework-free diff core: what counts as a change and what does not."""

from __future__ import annotations

import pytest

from model_audit import MISSING, Changeset, FieldChange, diff


def test_identical_mappings_yield_nothing():
    changeset = diff({"title": "Draft", "views": 10}, {"title": "Draft", "views": 10})

    assert not changeset
    assert len(changeset) == 0
    assert changeset.fields == ()
    assert changeset.as_dict() == {}


def test_a_changed_field_carries_both_values():
    (change,) = diff({"title": "Draft"}, {"title": "Release"})

    assert change.field == "title"
    assert (change.old, change.new) == ("Draft", "Release")
    assert not change.is_addition
    assert not change.is_removal


def test_a_field_only_incoming_is_an_addition():
    changeset = diff({}, {"title": "Draft"})

    assert changeset["title"].old is MISSING
    assert changeset["title"].is_addition
    assert not changeset["title"].is_removal


def test_a_field_only_stored_is_a_removal():
    changeset = diff({"title": "Draft"}, {})

    assert changeset["title"].new is MISSING
    assert changeset["title"].is_removal


def test_a_field_missing_from_both_sides_is_unchanged():
    assert not diff({}, {}, fields=["title"])


def test_stored_fields_come_first_then_the_ones_only_incoming_carries():
    changeset = diff({"b": 1, "a": 1}, {"a": 2, "b": 2, "c": 2})

    assert changeset.fields == ("b", "a", "c")


def test_fields_restricts_and_orders_the_comparison():
    changeset = diff({"a": 1, "b": 1}, {"a": 2, "b": 2}, fields=["b"])

    assert changeset.fields == ("b",)


def test_exclude_drops_a_field_from_the_comparison():
    changeset = diff(
        {"title": "Draft", "views": 10},
        {"title": "Release", "views": 11},
        exclude=["views"],
    )

    assert changeset.fields == ("title",)


def test_exclude_wins_over_an_explicit_field_list():
    assert not diff({"a": 1}, {"a": 2}, fields=["a"], exclude=["a"])


def test_a_bool_replacing_a_number_is_a_change():
    assert diff({"flag": 1}, {"flag": True})
    assert diff({"flag": True}, {"flag": 1})
    assert diff({"flag": 0}, {"flag": False})


def test_equal_numbers_of_different_types_are_unchanged():
    assert not diff({"total": 1}, {"total": 1.0})


def test_none_and_the_empty_string_are_different_values():
    assert diff({"notes": None}, {"notes": ""})


def test_nested_values_compare_by_equality():
    assert not diff({"tags": [1, 2]}, {"tags": [1, 2]})
    assert diff({"tags": [1, 2]}, {"tags": [2, 1]})


def test_changeset_reads_like_a_mapping_of_changes():
    changeset = diff({"a": 1, "b": 1}, {"a": 2, "b": 2})

    assert "a" in changeset
    assert "c" not in changeset
    assert changeset["b"].new == 2
    assert [change.field for change in changeset] == ["a", "b"]
    assert changeset.as_dict() == {"a": (1, 2), "b": (1, 2)}
    with pytest.raises(KeyError):
        changeset["c"]


def test_missing_is_falsy_and_names_itself():
    assert not MISSING
    assert repr(MISSING) == "MISSING"


def test_a_changeset_can_be_built_by_hand():
    changeset = Changeset((FieldChange("title", MISSING, "Draft"),))

    assert changeset.fields == ("title",)
    assert changeset["title"].is_addition
    assert not Changeset()
