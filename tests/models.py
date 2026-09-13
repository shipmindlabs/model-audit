"""Models used by the tests. ``phone`` is there to exercise redaction."""

from django.db import models


class Customer(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField(default="")
    phone = models.CharField(max_length=40, default="")
    notes = models.TextField(default="")

    class Meta:
        app_label = "tests"
