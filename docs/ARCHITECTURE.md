# Architecture

## Why this shape

A panel shop's business object is the **job card**. It is born at intake, accumulates
an estimate, possibly a claim, parts, photos and QC results, then dies as an invoice.
Everything in this codebase is arranged so that object has exactly one home and one
set of rules — the `JobCard` model and `app/services/job_flow.py`.

The web front end and the WhatsApp bot are **two clients of the same domain logic**.
Neither re-implements a rule. If a job cannot advance because parts are outstanding,
the API and the bot both get that answer from `job_flow.can_advance()`.

```
┌───────────────────────┐        ┌────────────────────────┐
│  SPA front end        │        │  WhatsApp chatbot      │
│  Bootstrap + vanilla  │        │  intent_router.py      │
│  JS (h/store/router)  │        │  state machine         │
└───────────┬───────────┘        └───────────┬────────────┘
            │ /api/*                         │ webhook → replies
            ▼                                ▼
      ┌──────────────────────────────────────────────┐
      │  Service layer                               │
      │  pricing · job_flow · notifications          │
      └───────────────────┬──────────────────────────┘
                          ▼
      ┌──────────────────────────────────────────────┐
      │  Models (SQLAlchemy) — 19 tables             │
      └──────────────────────────────────────────────┘
```

## Job lifecycle

```
INTAKE → ASSESSMENT → AWAITING_APPROVAL → PARTS_ORDER → STRIP → PANEL
      → PREP → PAINT → REASSEMBLY → DETAILING → QC → READY → COLLECTED
```

Guard rails enforced by `job_flow.can_advance()`:

| Gate | Rule |
|---|---|
| `AWAITING_APPROVAL` | Insurance jobs need a claim with status `APPROVED`/`PARTIAL`; cash jobs need an approved estimate |
| `PARTS_ORDER` | No part may be in `REQUIRED`, `ORDERED`, or `IN_TRANSIT` |
| `QC` | The checklist must exist **and** have zero failures |

Reaching `QC` auto-generates the checklist. Reaching `COLLECTED` auto-creates the
invoice. Any gate can be overridden from the UI (`force: true`), which is logged in
the stage history — the override exists because real workshops have real exceptions,
but it should leave a trail.

## The front-end "React feel" without a build step

| React idea | Implementation |
|---|---|
| JSX / elements | `h('div.card.p-3', props, ...children)` in `core.js` |
| Components | Plain functions returning nodes, e.g. `T.statCard({...})` |
| State | `T.createStore()` with `get`/`set`/`subscribe` |
| Routing | Hash router with `:param` patterns (`T.route('/jobs/:id', …)`) |
| Hooks-ish | `ctx.onCleanup(fn)` per route for teardown |
| Optimistic UI | Kanban drag moves the card immediately, rolls back on 409 |
| Modals | `T.modal()` and `T.formModal({ fields })` returning a Promise |
| Printing | `T.printDoc({ node })` renders a document to a real print stylesheet |

`T.formModal({ fields })` is the workhorse: it builds validated forms and resolves
to an object of values (or `null` on cancel), which collapses a lot of CRUD screens
into a few lines each.

### Things to know before editing the front end

1. **Every write needs the CSRF token.** `core.js` sends `X-CSRFToken` from
   `window.__CSRF__` on every request. If you add a raw `fetch`, you must do the
   same or the API will answer 400.
2. **Assets are cache-busted by mtime** through the `static_url()` Jinja global.
   Never hand-write `url_for('static', …)` in a template — `tests/test_frontend.py`
   will fail.
3. **A syntax error in one view file breaks only that screen** (each view is its own
   IIFE). Run `python tools/check_js.py` before committing; it is also part of the
   test suite.

## Audit trail

`ActivityLog` is append-only. `app/services/activity.log_activity()` is called from
every mutating endpoint and **never raises** — auditing must not be able to break a
workflow, so failures are swallowed after a rollback.

Entries carry `action` (`job.stage_changed`, `payment.recorded`, …), the human
`entity_ref`, a `summary` a manager can read, a `meta` JSON blob with the before/after
values, and the actor. `ActivityLog.tone` and `.icon` derive presentation from the
action name, which is why new actions should follow the `entity.verb` convention.

## Security posture

| Concern | Handling |
|---|---|
| Session theft / CSRF | Flask-WTF `CSRFProtect` on by default; the SPA sends `X-CSRFToken`; every write endpoint requires it. The `/webhooks/whatsapp` blueprint is the only exemption, because Meta cannot send a token. |
| Authentication | Flask-Login; `@login_required` on every API route; 401 JSON for `/api/*` |
| Authorisation | Role-based (`owner`/`manager` gate destructive staff operations) |
| Webhook trust | Shared verify token handshake; webhook blueprint exempt from CSRF (Meta cannot provide one) |
| Auditability | Every mutation logged with actor, IP and before/after metadata |
| Data at rest | Job photos and inbound WhatsApp media live under `instance/uploads` |


## Money and rounding

Every monetary value moves through `Decimal` and is quantised to 2 dp before it
reaches SQLAlchemy or JSON. Floats only appear at the JSON boundary. VAT is applied
at the estimate header, not per line, so totals reconcile to the cent.

## WhatsApp design decisions

1. **Menu-first, not LLM-first.** Wrong prices in a workshop cost real money. Button
   and list replies are also what Meta's UX prefers and they cannot be misparsed.
2. **Deterministic state.** The conversation's state and working memory live in
   `WaConversation.context_json` — a JSON blob holding `reg`, `service`, `damage`,
   `strikes`, `lang`, `last_job_no`. It survives restarts and is visible in the inbox,
   so support staff can see exactly why the bot did what it did.
3. **Escalation is a first-class path.** Two consecutive unrecognised messages, or an
   explicit "talk to a person", set `human_takeover` and the bot goes silent. The
   thread appears flagged in the inbox.
4. **Simulator mode.** `WA_MODE=simulator` (the default) persists outbound messages
   instead of calling Meta, so the entire bot is demoable and testable offline.
5. **Never block the workflow on a send failure.** `notifications._dispatch` catches
   everything and writes a `NotificationLog` row with `status='failed'`, so a Meta
   outage cannot stop a vehicle moving through the shop.

## Extending

| Want to… | Where |
|---|---|
| Change labour hours or rates | `app/constants.py` → `LABOUR_MATRIX`, `PANEL_RATE`, `PAINT_RATE` |
| Add a panel | Add a key to `LABOUR_MATRIX` — the estimator UI picks it up automatically |
| Add an insurer | `app/constants.py` → `INSURERS` + `INSURER_ALIASES` |
| Add a workshop stage | `STAGES`, `STAGE_LABELS`, `STAGE_COLOURS`, `STAGE_PROGRESS`, `STAGE_CUSTOMER_TEXT` |
| Add a bot intent | `INTENT_PATTERNS` + a `_menu_*` handler in `intent_router.py` |
| Add a notification | A function in `notifications.py` + a Meta template name |
| Add an API endpoint | `app/views/api.py` (all routes are `@login_required` JSON) |
| Add a screen | A file in `static/js/views/` using `T.route(...)`, then a `<script>` in `templates/app.html` |
