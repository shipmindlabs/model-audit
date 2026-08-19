# model-audit

Audit trail for Django models: field-level diffs on save, who changed what, and
declarative HTTP request logging.

## Status

Pre-alpha. The public API is not stable yet.

## Installation

```bash
pip install model-audit
```

## Requirements

- Python 3.10+
- Django 4.2+

## Field-level diffs

The diff core is framework-free: it compares two mappings of field values and
returns a typed changeset.

```python
from model_audit import diff

stored = {"title": "Draft", "views": 10, "published": False}
incoming = {"title": "Release notes", "views": 10, "published": True}

changeset = diff(stored, incoming, exclude=["views"])

bool(changeset)          # True
changeset.fields         # ('title', 'published')
changeset.as_dict()      # {'title': ('Draft', 'Release notes'), 'published': (False, True)}

for change in changeset:
    print(change.field, change.old, "->", change.new)
```

Pass `fields=[...]` to restrict and order the comparison. A field present on
only one side is reported with `MISSING` on the other, which `FieldChange`
exposes as `is_addition` and `is_removal`.

## License

MIT — see [LICENSE](LICENSE).

Maintained by [Shipmind Labs](https://shipmindlabs.com).
