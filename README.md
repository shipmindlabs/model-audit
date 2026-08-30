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

## Recording changes on save

Recording is opt-in per model. `register()` connects the `post_init` and
`post_save` signals for that model; every other model stays untouched.

```python
from model_audit import register, subscribe

register(Invoice, exclude=["search_vector"])

@subscribe
def write_to_log(record):
    print(record.action, record.model, record.pk, record.changes.as_dict())

invoice.total = 120
invoice.save()
# updated app.Invoice 7 {'total': (100, 120)}
```

A record carries the model label, the primary key, the `Action` (`CREATED` or
`UPDATED`), the actor, the `Changeset` and a timestamp; `record.as_dict()`
flattens all of it for logging. Since signals carry no call-site actor, the
recorder reads the ambient one — wrap the request or job in `actor_context()`
to attribute the change, otherwise it is recorded as `UNKNOWN`.

The fields being compared are chosen at registration:

- `exclude=[...]` drops single fields, `fields=[...]` narrows to an allow-list.
  A foreign key can be named either way (`author` or `author_id`).
- Timestamps that every save touches (`updated_at`, `modified`, …) are dropped
  by default; they are listed in `NOISY_FIELDS` and kept with
  `ignore_noisy=False`.
- Deferred fields are skipped, and relations are read by their stored id, so
  snapshotting never issues an extra query.
- Values that look sensitive are withheld before the record is handed out; see
  [Redaction](#redaction).

A creation reports every audited field as an addition. An update with
`save(update_fields=[...])` is narrowed to those fields, and an update that
changed nothing emits no record at all. `unregister(Invoice)` stops recording,
`is_registered()` and `registered_models()` report the current state.

## Redaction

An audit trail records *that* a field changed; it must not become a second copy
of the data it watches. Values of fields whose name reads like a secret, a
phone number or an identity document are replaced by `REDACTED` before a record
reaches any receiver.

```python
from model_audit import REDACTED, Redactor, redact_data, register

register(Customer)

customer.phone = "+31 6 1234 5678"
customer.auth_token = "tok_live_9f3"
customer.save()
# updated app.Customer 7 {'phone': (REDACTED, REDACTED), 'auth_token': (REDACTED, REDACTED)}
```

`REDACTED` is a `str` (`"[redacted]"`), so a record still serializes to JSON
without special-casing, while `value is REDACTED` separates it from a value
that merely looks like one. A field absent from one side keeps its `MISSING`,
so an addition still reads as one.

Matching is a case-insensitive substring test against `SENSITIVE_FIELDS` —
`token`, `password`, `secret`, `phone`, `passport`, `document`, `iban`, `ssn`
and their neighbours. Personal data that no default can guess is named per
model, and a field caught by mistake is released:

```python
register(Customer, redact=["nickname", "birthday"])
register(Shipment, redact=Redactor(allow=["document_type"]))
```

`exclude` and `redact` answer different questions: an excluded field is not
audited at all, a redacted one is audited by name with its values withheld.
Redaction is the safer default of the two — an excluded field leaves no trace
that it was ever touched.

Nothing here is tied to models. `redact()` takes a `Changeset`, and
`redact_data()` walks nested mappings and lists, which is the shape a request
or response body has on its way to a log sink:

```python
redact_data({"user": {"phone": "+31 6 1234 5678", "city": "Utrecht"}})
# {'user': {'phone': '[redacted]', 'city': 'Utrecht'}}
```

Email addresses are not redacted by default, since many trails are read by
them; add `redact=["email"]` where they count as personal data.

## HTTP requests

Which endpoints are audited, and how much of each request is kept, is declared
in one map. The middleware only looks the request up there, so the answer to
"is this endpoint audited, and what does it record?" is read in a single place
instead of reconstructed from conditions inside the middleware.

```python
from model_audit import AuditRoute, Capture, audit_routes, subscribe_requests

audit_routes({
    "/api/**": False,                                   # audited by exception
    "/api/orders/**": Capture.QUERY,
    "POST /api/orders/*/refund": AuditRoute(Capture.FULL, label="refund"),
    "POST,DELETE /api/sessions": AuditRoute(Capture.HEADERS, label="sign-in"),
})

@subscribe_requests
def write_to_log(record):
    print(record.method, record.path, record.status, record.label, record.payload)
```

Then add the middleware, after the authentication one:

```python
MIDDLEWARE = [
    ...,
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "model_audit.http.AuditMiddleware",
]
```

The map may also live in settings as `MODEL_AUDIT_ROUTES`, in the same shape;
the middleware reads it once when Django builds the chain. Declaring it from
code instead (an `AppConfig.ready()`) keeps route objects and their redactors
out of the settings module.

### Reading the map

- A pattern matches by path segment: `*` stands for one segment, `**` for any
  number of trailing ones.
- A key may name the verbs it applies to (`"POST,DELETE /api/sessions"`), which
  is the same as passing `methods=[...]` to the route; `WRITE_METHODS` is
  `POST`, `PUT`, `PATCH` and `DELETE`.
- The most specific declaration wins — more literal segments first, then an
  explicit method list, then a pattern without `**`. A broad `"/api/**": False`
  is therefore a default, not a veto: any endpoint below it can opt back in.
- `False` and `AuditRoute.off()` mean the same thing: matched in order to be
  left out. An unmatched path costs the lookup and nothing else — no body is
  read, no actor is resolved.

### What a route captures

`Capture.NOTHING` (the default) still records that the request happened: verb,
path, matched pattern, label, status, actor, duration and timestamp. The flags
add payloads — `QUERY`, `BODY`, `HEADERS`, `RESPONSE`, or `FULL` for all four —
under `record.payload`, and `record.as_dict()` flattens the lot.

Captured payloads go through [redaction](#redaction) first, with `Cookie` and
`Authorization` withheld from headers; pass `redactor=` on the route where an
endpoint carries personal data the defaults cannot guess. Bodies are read only
for form and JSON content types and only up to 64 KB (`MAX_BODY_BYTES`);
anything else is recorded as an `{"omitted": ...}` note rather than dropped
silently, so the trail says why a body is not there.

A view that raises is recorded too, with `status=None` and `error` naming the
exception class, before the exception continues on its way.

For the duration of an audited request the actor is bound as the ambient one,
so model records written by the view are attributed to the same party without
threading anything through. The record's own actor is resolved after the view,
which is what makes a sign-in endpoint report the user it authenticated.

## License

MIT — see [LICENSE](LICENSE).

Maintained by [Shipmind Labs](https://shipmindlabs.com).
