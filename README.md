# Topclass Auto Body — Workshop OS

An in-house workshop management system plus a WhatsApp chatbot, built for
[Topclass Auto Body](https://topclass.co.zw) (23 George Avenue, Msasa, Harare).

> **Flask** (Python) + **SQLAlchemy** backend · **Bootstrap 5 + vanilla JS** front end
> with a React-style SPA shell (hyperscript components, store, hash router,
> optimistic updates) · **WhatsApp Cloud API** chatbot with a built-in simulator.

### Design language

| Colour | Role |
|---|---|
| **Deep dark blue** `#08142c` | Structure and chrome — sidebar, headings, table heads, print letterhead |
| **Crimson** `#c2102e` | Primary actions, brand mark, active navigation, alerts, QC gate |
| **Green** `#106b41` | Success — ready for collection, collected, paid, approved, WhatsApp |
| **Amber** `#e8a317` | Attention and in-progress — waiting stages, HIGH priority, progress bars |

Every token lives in one place (`:root` in `app/static/css/app.css`), and
`tests/test_frontend.py` fails the build if a token is used but never defined.

---

## What's inside

### The web app (`/app`)

| Module | Route | What it does |
|---|---|---|
| Dashboard | `#/dashboard` | Today's shop: open jobs, overdue, WIP value, receivables, claim aging |
| WIP board | `#/board` | Drag-and-drop Kanban across all 13 workshop stages |
| Job cards | `#/jobs` | Intake wizard with a live, panel-driven estimate calculator — opens in a popup |
| Job detail | `#/jobs/<id>` | Overview · Estimate · QC · History tabs, plus parts, claims and photos |
| Bookings | `#/bookings` | Website/WhatsApp/phone booking requests → confirm and schedule |
| Customers | `#/customers` | CRM records, portal links, WhatsApp opt-in management |
| Vehicles | `#/vehicles` | Registration-keyed vehicle register |
| WhatsApp inbox | `#/inbox` | Live conversations, human takeover, and a bot simulator |
| Claims | `#/claims` | All 7 panel insurers, aging, excess, shortfall tracking |
| Invoices | `#/invoices` | Issue invoices, record EcoCash/InnBucks/bank payments, issue and resend receipts |
| Reports | `#/reports` | Turnaround, work mix, insurer performance, technician productivity |
| Parts & stock | `#/parts` | Stock levels, reorder alerts, supplier list, job issues |
| Activity log | `#/activity` | Immutable audit trail — who changed what, filterable by entity |
| Staff | `#/staff` | Role-based accounts and WhatsApp bot configuration |

Cross-cutting:

- **Control layer** — one coherent set of inputs, selects, checkboxes, radios, switches
  and option cards, with hover, focus, checked, disabled and invalid states all themed.
  Selects carry a custom chevron; inputs can take a leading icon and a trailing affix
  (`USD`, `km`, `hrs`). Dialogs validate inline: the offending field turns crimson, shows
  the reason underneath, takes focus, and clears as soon as it is fixed.
- **Command palette** (`Ctrl`/`⌘` + `K`) — fuzzy search across job cards, customers and
  vehicles, plus every screen, action and CSV export.
- **Collapsible navigation** — icon-only rail mode (`Alt`+`B`), remembered per browser,
  with live counters for open jobs, pending bookings, open claims, overdue invoices and
  low stock. Active items carry a crimson spine and a soft glow.
- **Blurred modal overlays** — `backdrop-filter: blur(14px)` over the dimmed workspace,
  a sticky header with an accent rule and an icon well, sticky footer, and a lift-and-scale
  entrance. Sizes: `sm` `md` `lg` `xl` `full`.
- **Toasts** — centred at the top of the screen, above the workspace. Accent rule, icon
  well, title plus message, optional action button, and a countdown bar that pauses while
  you hover. Maximum of four on screen.
- **Job-card intake in a popup** — the whole booking-and-estimate wizard (customer,
  vehicle, job details, panel picker, live pricing) runs in a modal, so `n`, the topbar
  button, the command palette, the dashboard and the job list never leave the page you
  are on. The running estimate total sits in the modal footer.
- **Attach an existing quotation** — instead of re-tapping every panel, pick the
  quotation the customer already has and its line items are copied onto the job card.
  See [Intake](#intake-attach-a-quotation-or-build-one).
- **Print-ready documents** — job cards and invoices render on a proper letterhead
  with signature blocks, driven by a real `@media print` stylesheet.
- **Quotation, invoice and receipt documents** — real PDFs built with ReportLab, plus a
  branded no-login web page for each one. See [Documents](#documents).
- **CSV exports** — jobs, invoices, claims and stock, versioned by date.
- **Audit trail** — every mutation is written to `activity_log`, visible on the
  dashboard and the Activity screen.
- **Keyboard shortcuts** — `Ctrl K` palette, `Alt B` rail, `n` new job, `b` board,
  `i` inbox, `?` activity log.

Also included:

- **Customer portal** — `/portal/<token>`, a no-login page showing repair progress,
  outstanding invoices and bookings.
- **Public quote page** — `/quote`, embeddable from the marketing site (it writes a
  real `Booking` through `POST /api/bookings`).

### The WhatsApp chatbot

The bot is a **deterministic state machine** (`app/services/intent_router.py`) rather
than a free-form LLM — predictable answers matter when they carry prices and dates.

```
Customer → Meta Cloud API → POST /webhooks/whatsapp
                                   │
                                   ├─ log message + attachment to the conversation
                                   ├─ IntentRouter resolves state → replies
                                   ├─ quote requests create Booking/Customer/Vehicle
                                   └─ stage changes push notifications back out
```

Supported intents:

| Intent | Example | Result |
|---|---|---|
| Greeting / menu | `hi`, `mhoro`, `sawubona` | Main menu buttons |
| Get a quote | `get a quote`, `how much for a bumper respray` | Reg → service → damage → **Booking + estimate reference** |
| Track repair | `track TC-2026-0001`, `where is my car` | Stage, % complete bar, blocking parts, promised date, balance due |
| My claim | `my claim`, `old mutual claim CLM12345` | Insurer, assessor date, approved amount, excess, aging |
| Book a service | `book` | Service → day → contact → booking |
| Hours / location | `what time are you open` | Trading hours, address, phone |
| Warranty | `warranty` | 12-month workmanship terms |
| Talk to a person | `human`, `speak to someone` | Pauses the bot, flags the thread for the team |
| Language | `language` | English / **Shona** / **Ndebele** |
| Opt out | `stop` / `start` | Toggles `whatsapp_opt_in` on the customer record |

---

## Getting started

```powershell
# 1. Create and activate a virtual environment (optional but recommended)
python -m venv .venv
.venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure
Copy-Item .env.example .env      # then edit SECRET_KEY at minimum

# 4. Create the database and load demo data
python -c "from app import create_app; from app.extensions import db; from app.seed import run_seed; a=create_app(); app_ctx=a.app_context(); app_ctx.push(); db.create_all(); run_seed(with_demo=True)"

# 5. Run
python run.py
```

Open <http://127.0.0.1:5000>.

### Signing in

The landing page is the sign-in page: the form on the left, the workshop story on
the right. In development it also lists the seeded accounts as **one-click
buttons** — click one and you are signed in, no typing. The buttons are generated
from the same table the seeder uses, so they can never point at an account that
does not exist.

| Setting | Default | What it does |
|---|---|---|
| `SEED_PASSWORD` | `topclass123` | The password given to the accounts the seeder creates. **Set this before deploying** — the default is published in this repository. |
| `SHOW_DEMO_ACCOUNTS` | `true` in dev, `false` in production | Whether the sign-in page advertises the demo logins. A staging app can opt back in with `SHOW_DEMO_ACCOUNTS=true`. |

Demo accounts (development only):

| Email | Role |
|---|---|
| `owner@topclass.co.zw` | Owner (full access, manages staff) |
| `manager@topclass.co.zw` | Workshop Manager |
| `estimator@topclass.co.zw` | Estimator / Assessor liaison |
| `store@topclass.co.zw` | Storeman |
| `tech1@topclass.co.zw` | Technician |
| `front@topclass.co.zw` | Front desk |

### Adding staff

Managers get **Add staff member** in the avatar menu (top-right) and a button on
*Staff & settings* — both open the same form. Fill in a name, work email, role and
a temporary password and they can sign in immediately. Each role is described in
the dropdown so it is clear what is being granted.

To change somebody's password, edit their row on *Staff & settings* and fill in
**New password**. Roles and the active flag live in the same dialog, so leaving
someone who has left the business is a two-click job.

Only owners and managers can create or edit accounts; the endpoint behind the form
enforces that independently of the button (`POST /api/users`).

### CLI shortcuts

```powershell
flask --app run:app init-db          # create tables
flask --app run:app seed             # reference data + demo jobs
flask --app run:app reset-db         # drop, recreate, reseed
```

---

## Documents

Three customer-facing documents are generated from live data — never retyped:

| Document | Source record | Staff actions | Customer link |
|---|---|---|---|
| **Quotation** | `Estimate` | `PDF` · `Send` (WhatsApp, with Approve/Decline buttons) | `/doc/quote/<token>` |
| **Tax invoice** | `Invoice` | `PDF` · `Send` · copy share link | `/doc/invoice/<token>` |
| **Receipt** | `Payment` | `PDF` · `Send` | `/doc/receipt/<token>` |

Every record carries an unguessable `public_token` (`secrets.token_urlsafe(18)`), so the
customer link needs no login and staff pages stay behind authentication.

**PDFs** are built with ReportLab (`app/services/documents.py`) — navy letterhead,
crimson accent rule, a real line-item table, totals block, terms/bank details and a
signature strip. Append `.pdf` to any share link, or use the API:

```
GET /api/estimates/<id>/pdf     GET /api/invoices/<id>/pdf     GET /api/payments/<id>/pdf
```

**Numbering** — quotations use `TC-EST-XXXXXX`, invoices `INV-YYYY-NNNN` and receipts
`RCT-YYYY-NNNN`, all allocated inside the same transaction as the record.

**Receipts** are created automatically by `job_flow.record_payment()`. The **Record
payment** dialog has a *Send the receipt on WhatsApp* switch (on by default), so paying a
customer hands them a receipt before they leave reception. `#/invoices` → any invoice
shows a *Receipts issued* table with per-receipt PDF and send buttons; the receipt
register is also available at `GET /api/payments`.

**Sharing over WhatsApp** (`app/services/notifications.py`):

- `send_quotation(job, estimate)` — PDF plus an Approve/Decline button message. Tapping
  **Approve** runs `approve_estimate()` and marks the estimate `APPROVED`; tapping
  **Decline** sets it to `DECLINED`. Both are logged and answered in the customer's
  language.
- `send_invoice(job, invoice)` and `send_receipt(payment)` — PDF with a caption carrying
  the totals and the outstanding balance.

Meta fetches document links from the internet, so `PUBLIC_BASE_URL` **must** be a public
HTTPS address in production:

```dotenv
PUBLIC_BASE_URL=https://workshop.topclass.co.zw
```

In simulator mode the link is logged and rendered in `#/inbox` instead of being fetched.
Smoke-test the whole chain (PDFs, numbering, delivery, public routes) with:

```powershell
python tools/smoke_docs.py     # writes PDFs to instance/smoke/
```

---

## Intake: attach a quotation, or build one

Section 3 of the job-card wizard is a choice, so the same task is never done twice.

**Attach a quotation** (the default) — pick the quotation the customer already has and
its lines are copied onto the new job card. The list filters itself: choose a customer or
type a registration and only that customer's / that vehicle's quotations are offered, so
"the same car came back" or "the fleet sent the same job again" is one tap.

- Each row shows the reference, status, vehicle, customer, source job card, line count,
  total and date.
- Selecting one previews every line and the totals before you commit.
- The quotation's **insurance flag and excess are applied** to the form, because
  otherwise the copied total would not match the paperwork.
- Provenance is recorded: the new estimate's notes read
  `Copied from quotation TC-EST-XXXX (job card TC-YYYY-NNNN)` and an
  `estimate.copied` entry lands in the activity log.
- Copying a copy works, so a repeat repair can chain from the last one.

**Build it here** — the original panel picker with live pricing. Used when nothing is on
file, or when the job genuinely differs from the last one. If no quotation matches the
vehicle the wizard switches to this automatically.

**Quotation document** — either mode accepts a PDF or photo of the assessor's paperwork.
It is stored under `instance/uploads/documents/` and recorded against the job card
(visible in the Photos panel), so the source document sits with the job.

| Endpoint | What it does |
|---|---|
| `GET /api/quotations?q&reg_no&customer_id&exclude_job` | Quotations a new job card can be built from |
| `POST /api/jobs` with `source_estimate_id` | Creates the job and copies the quotation's lines |
| `POST /api/jobs/<id>/documents` | Multipart upload of the quotation PDF / photo |
| `GET /uploads/<path>` | Serves attachments and job photos to signed-in staff only |

---

## Connecting the real WhatsApp number

The app defaults to `WA_MODE=simulator`, which logs every outbound message to the
database and renders it in `#/inbox` — you can demo and test the entire bot with no
Meta account.

To go live:

1. Create a **Meta Business** app → add the **WhatsApp** product.
2. Complete **Business Verification**.
3. Register the number `+263 77 555 0555` (or a new number) as a WhatsApp Business Account.
4. Set the webhook URL to `https://<your-domain>/webhooks/whatsapp` and set the verify
   token to match `WA_VERIFY_TOKEN`.
5. Subscribe to the `messages` field.
6. In `.env`:

```
WA_MODE=live
WA_API_VERSION=v21.0
WA_PHONE_NUMBER_ID=1234567890
WA_BUSINESS_ACCOUNT_ID=0987654321
WA_ACCESS_TOKEN=EAAG...
WA_VERIFY_TOKEN=<the same random string you configured in Meta>
```

7. Submit these message templates for approval in WhatsApp Manager:

| Template | Used for |
|---|---|
| `job_stage_update` | Vehicle moved to a new stage |
| `vehicle_ready` | Ready for collection |
| `quotation_ready` | Estimate sent for approval |
| `parts_received` | Blocking parts have landed |
| `payment_due` | Invoice issued |
| `warranty_registered` | Warranty confirmation |

> **Why templates?** Meta only allows free-form replies inside the 24-hour service
> window that follows a customer's last message. Anything business-initiated outside
> that window must use a pre-approved template. The notification service checks
> `WaConversation.is_session_open` and switches automatically.

---

## Money model

All pricing lives in `app/constants.py` and `app/services/pricing.py` so the
estimator, the reports and the WhatsApp bot always agree.

- **Labour matrix** — 17 panels/operations with panel-beating and paint hours.
- **Charge-out rates** — panel `USD 25/hr`, paint `USD 28/hr`, metal/jig `USD 32/hr`.
- **Materials** — `USD 18` paint & materials per painted panel; consumables at 8% of labour.
- **Insurer rate card** — insurers get 10% off retail labour and a 15% parts markup
  (vs 25% for cash customers) and are treated as VAT-exempt.
- **VAT** — 15% on retail estimates, 0% on insurance.
- **Excess** — defaults to `USD 150` and is the customer's out-of-pocket portion.

Tune these in `constants.py` to match your supplier pricing.

---

## Project layout

```
Autobody/
├── app/
│   ├── __init__.py            # application factory, CLI, error handlers
│   ├── constants.py           # stages, services, insurers, labour matrix, rates
│   ├── extensions.py          # db, migrate, login_manager, csrf
│   ├── models.py              # 19 SQLAlchemy models
│   ├── seed.py                # staff, stock, demo jobs, demo WhatsApp thread
│   ├── services/
│   │   ├── pricing.py         # estimating engine
│   │   ├── job_flow.py        # job lifecycle, QC gate, invoicing, metrics
│   │   ├── intent_router.py   # the WhatsApp bot brain
│   │   ├── whatsapp_client.py # Cloud API client + simulator
│   │   ├── documents.py       # ReportLab quotation/invoice/receipt PDFs
│   │   └── notifications.py   # stage/quote/ready/payment + document triggers
│   ├── views/
│   │   ├── views.py           # SPA shell, portal, public quote
│   │   ├── docs.py            # public token-gated customer documents
│   │   ├── auth.py            # login/logout
│   │   ├── api.py             # the whole JSON API
│   │   └── whatsapp.py        # Meta webhook
│   ├── templates/             # base, login, app shell, portal, quote, document, error
│   └── static/
│       ├── css/app.css        # design tokens, components, print stylesheet
│       └── js/
│           ├── core.js        # h(), store, router, api, modal/form/print helpers
│           ├── cmdk.js        # Ctrl+K command palette
│           ├── app.js         # layout + route registration
│           └── views/         # dashboard, board, jobs, job_detail, registry,
│                              # parts, money, inbox, activity
├── tools/check_js.py          # front-end syntax guard (used by the tests)
├── tools/smoke_docs.py        # end-to-end document + WhatsApp smoke test
├── bootstrap.py               # one-command database setup
├── run_tests.py               # test runner that writes a readable report
└── tests/                     # 79 backend, bot, document, platform and front-end tests
```

---

## Tests

```powershell
python -m pytest -q
```

Or, on Windows terminals that mangle long pytest output:

```powershell
python run_tests.py          # writes a readable test-results.txt
```

Coverage:

| Area | What is asserted |
|---|---|
| Estimating | VAT, insurer discounts, parts markups, insurer/customer split |
| Job lifecycle | QC gate, parts gate, insurer-approval gate, overdue detection |
| Money | Invoice creation, part payments, balance and PAID transition |
| Claims | Linking, status transitions, excess, shortfall |
| Permissions | Manager-only staff creation, 401s for anonymous API calls |
| Sign-in page | The demo shortcuts match the seeded accounts, are absent in production, `?next=` cannot become an open redirect, `SEED_PASSWORD` overrides the default, and only managers can create accounts |
| Documents | PDF bytes for quotations, invoices and receipts; sequential receipt numbers; public `/doc/<kind>/<token>` pages and `.pdf` responses; junk tokens 404 |
| Quotation attach | The picker's customer / registration / search filters, copying a quotation's lines with kinds and prices intact, insurance flag and excess inheritance, the `estimate.copied` activity entry, 404 on an unknown quotation, 400 on an empty one, attachment upload and its traversal guard |
| Document delivery | `document_share` attachments logged in simulator mode, Approve/Decline buttons sent, `a_approve:`/`a_decline:` taps update the estimate and write a `quotation_decision` log |
| WhatsApp bot | Number normalisation, intent detection, full quote flow, fallback escalation, human takeover, language switching, opt-out, webhook verification and message processing |
| Platform | Search, activity trail, CSV exports, part/vehicle edits |
| Security | CSRF really is enforced on API writes, and exempted for the public booking form |
| Front end | Every shipped `.js` parses, every view is loaded by the shell, CSS tokens are defined, no leftover `console.log`/`debugger` |

The front-end check (`tools/check_js.py`) is a regex- and template-aware bracket
checker. It exists because a missing `)` in a deeply nested `h(...)` call is
invisible to Python tests and only shows up when someone opens that screen.

```powershell
python tools/check_js.py      # also runs as part of the suite
```

---

## Production notes

- **Database** — swap SQLite for PostgreSQL by setting `DATABASE_URL`, then run
  `flask --app run:app init-db`.
- **WSGI** — `gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120`
- **Uploads** — WhatsApp media and job photos land in `instance/uploads`. For a
  multi-server deployment point `UPLOAD_DIR` at shared storage.

---

## Deploying to Render

| Setting | Value |
|---|---|
| Environment | Python |
| Build command | `pip install -r requirements.txt` |
| Start command | `gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120 --preload` |
| Health check path | `/login` |

`wsgi:app` picks up the **production** config (secure cookies). Override with
`FLASK_ENV` if you ever need to.

### Environment variables to set

```dotenv
FLASK_ENV=production
SECRET_KEY=<long random string>          # without this, sessions use a dev default
SEED_PASSWORD=<something private>        # otherwise the seeded staff keep the published default
SHOW_DEMO_ACCOUNTS=false                 # the default in production
PUBLIC_BASE_URL=https://<your-app>.onrender.com
COMPANY_NAME=Topclass Auto Body
COMPANY_ADDRESS=23 George Avenue, Msasa, Harare
COMPANY_TEL=+263 242 446954
COMPANY_MOBILE=+263 77 555 0555
DATABASE_URL=postgresql://…              # see the warning below
WA_MODE=simulator                        # switch to "live" once Meta is verified
```

### ⚠️ SQLite does not survive a deploy

Render's filesystem is **ephemeral**. Every deploy, restart or idle spin-down
throws away `instance/` — which is where `topclass.db` and the uploaded photos
live. Pick one:

1. **PostgreSQL (recommended)** — add a Render Postgres instance, copy its
   *Internal Database URL* into `DATABASE_URL`, and add the driver to
   `requirements.txt`:
   ```
   psycopg2-binary==2.9.9
   ```
   Render hands out URLs beginning `postgres://`, which SQLAlchemy 2.x rejects;
   the app rewrites the scheme to `postgresql://` automatically. Then run the
   bootstrap once (below).
2. **Persistent disk** — add a Render Disk mounted at `/opt/render/project/src/instance`
   and keep SQLite. Fine for one instance, but you get no backups.

### First-run bootstrap

Tables must exist before the first request. From the Render **Shell** tab:

```bash
python bootstrap.py --no-demo     # reference data only — no demo customers or jobs
python bootstrap.py --reset       # full reset with demo data, for a staging app
```

### Notes

- PDFs are generated in-process by ReportLab, so `reportlab` and `pillow` are
  runtime dependencies, not extras.
- Meta downloads document links itself, so `PUBLIC_BASE_URL` must be the public
  HTTPS URL — a preview URL works, but rotate it when you switch to a custom domain.
- Free instances spin down after 15 minutes idle; the first request afterwards
  takes a few seconds to wake.
- **Resilience** — Zimbabwe-specific: keep the app usable on a tablet over a flaky
  link. The front end degrades gracefully (toasts on failure, no hard crashes), and
  all notifications are logged to `notification_log` so nothing is silently lost.
- **Data protection** — customer photos and IDs are personal data under Zimbabwe's
  Cyber and Data Protection Act (2021). Restrict database access, back up encrypted,
  and honour the `stop`/`start` opt-out the bot already implements.
