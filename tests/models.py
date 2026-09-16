"""Models used by the tests.

``Customer.phone`` exercises redaction; ``Note`` is never registered, so the
tests can assert that an unaudited model produces nothing.
"""

from django.db import models


class Customer(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField(default="")
    phone = models.CharField(max_length=40, default="")
    notes = models.TextField(default="")

    class Meta:
        app_label = "tests"


class Note(models.Model):
    title = models.CharField(max_length=100)
    body = models.TextField(default="")

    class Meta:
        app_label = "tests"
