"""Test setup: a throwaway Django project holding the models in ``tests``."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import django
import pytest
from django.conf import settings


def pytest_configure() -> None:
    settings.configure(
        DEBUG=False,
        USE_TZ=True,
        SECRET_KEY="model-audit-tests",
        DATABASES={
            "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
        },
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "tests",
        ],
        DEFAULT_AUTO_FIELD="django.db.models.AutoField",
    )
    django.setup()


@pytest.fixture(scope="session", autouse=True)
def _test_database() -> Iterator[None]:
    from django.db import connection
    from django.test.utils import setup_test_environment, teardown_test_environment

    setup_test_environment()
    previous_name = connection.settings_dict["NAME"]
    connection.creation.create_test_db(verbosity=0)
    try:
        yield
    finally:
        connection.creation.destroy_test_db(previous_name, verbosity=0)
        teardown_test_environment()


@pytest.fixture(autouse=True)
def _rollback() -> Iterator[None]:
    from django.db import transaction

    atomic = transaction.atomic()
    atomic.__enter__()
    try:
        yield
    finally:
        transaction.set_rollback(True)
        atomic.__exit__(None, None, None)


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    from model_audit import registered_models, unregister

    yield
    for model in registered_models():
        unregister(model)


@pytest.fixture
def records() -> Iterator[list[Any]]:
    from model_audit import subscribe, unsubscribe

    collected: list[Any] = []
    subscribe(collected.append)
    try:
        yield collected
    finally:
        unsubscribe(collected.append)
