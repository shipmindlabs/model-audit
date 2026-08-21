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

## Who changed it

An actor is resolved from whatever the call site already has: a request, a
Django user, a service name, or an `Actor` built by hand.

```python
from model_audit import SYSTEM, Actor, resolve_actor

resolve_actor(request)            # Actor(kind=USER, id='42', label='ada')
resolve_actor(request.user)       # same
resolve_actor("billing-worker")   # Actor(kind=SERVICE, id='billing-worker', ...)
resolve_actor(None)               # UNKNOWN
resolve_actor(None, fallback=SYSTEM)

Actor.user(42, "ada").as_dict()   # {'kind': 'user', 'id': '42', 'label': 'ada'}
```

An unauthenticated user resolves to `ANONYMOUS`, an absent one to `UNKNOWN`;
neither is an error, so a missing actor is recorded rather than raised. Use
`actor.is_known` to tell an attributed change from an unattributed one.

### Ambient actor (opt-in)

When an actor cannot be threaded through — a model signal, a deep service call
— bind one for the duration of a block and ask for it explicitly:

```python
from model_audit import actor_context, current_actor, resolve_actor

with actor_context(request):
    current_actor()                  # the request's actor
    resolve_actor(None, ambient=True)  # same, via the resolver

current_actor()                      # UNKNOWN again
```

Middleware can use `set_current_actor()` / `reset_current_actor()` instead of
the context manager.

The trade-off is deliberate: the binding is context-local (`contextvars`), so it
follows `await` inside a task but is *not* inherited by threads or executor
workers started within the block, and it makes the actor invisible at the call
site. `resolve_actor()` therefore never reads it unless asked with
`ambient=True`.

## License

MIT — see [LICENSE](LICENSE).

Maintained by [Shipmind Labs](https://shipmindlabs.com).
