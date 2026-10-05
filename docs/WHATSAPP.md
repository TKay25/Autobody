# WhatsApp chatbot — operator guide

The bot is a deterministic, menu-first state machine in
`app/services/intent_router.py`. It is not an LLM: a workshop cannot afford an
invented price, and every answer here is testable without a network.

Nothing in this document requires the panel to be re-read from memory — it
describes what the code actually does today.

---

## 1. Connecting the WhatsApp account

Values live in `.env` (gitignored). **`.env` is never deployed** — on Render set
the same keys in the service's *Environment* tab, then redeploy.

| Key | Notes |
|---|---|
| `WA_MODE` | `simulator` (default, nothing leaves the machine) or `live` |
| `WA_PHONE_NUMBER_ID` | Topclass Auto Body: `1402997422890407` (+263 78 998 9577) |
| `WA_BUSINESS_ACCOUNT_ID` | `1101343192410894` |
| `WA_ACCESS_TOKEN` | **Expires.** Ask for a *System User* token in Business Settings → Users → System Users; those can be set to never expire |
| `WA_APP_SECRET` | Meta → App settings → Basic. **Set this in production.** While it is empty the webhook accepts unsigned payloads, so anyone who learns the URL can post fake messages at the bot |
| `WA_VERIFY_TOKEN` | Must match the *Verify token* box in Meta → WhatsApp → Configuration → Webhook |
| `WA_WEBHOOK_TOKEN` | Optional second lock. When set, Meta must deliver to `<callback-url>?token=<value>`. See below |
| `PUBLIC_BASE_URL` | Public HTTPS origin. Meta fetches document links itself, so `localhost` will not work |

### The verify token is not the app secret

They are constantly confused, and mixing them up silently breaks the bot:

|  | Verify token | App secret |
|---|---|---|
| Who invents it | **You** — any string | **Meta generates it** (32 hex chars) |
| Where it travels | the `GET` handshake query string | HMAC key over the `POST` body |
| What it proves | "you configured this webhook" | "Meta really sent this payload" |

Putting an invented value in `WA_APP_SECRET` makes `_signature_ok()` compute a
different HMAC than Meta does, so **every inbound message 403s** and the bot goes
deaf with no obvious cause. Leaving it empty means the webhook accepts unsigned
posts — and because the bot replies to the `from` field, a forged post can ask
`track <job no>` and have the job's status, vehicle and balance sent to an
arbitrary number.

**Without an app secret, set `WA_WEBHOOK_TOKEN`.** It reuses the same shared
secret you already have, as a check on every delivery rather than only the
handshake:

```
https://autobody.onrender.com/webhook?token=011235
```

It accepts `?token=` or an `X-Webhook-Token` header. It is **not** equivalent to
the app secret — it proves the caller knows the URL, not that Meta sent the
payload — so a leaked URL is still a hole. Turn on the app secret when you can.

> Set `WA_WEBHOOK_TOKEN` **after** updating the callback URL in Meta. Setting it
> first 403s every delivery.

**Webhook.** Point the Meta app at either of these — they are the same two
endpoints:

```
https://<your-domain>/webhooks/whatsapp     ← canonical
https://<your-domain>/webhook               ← accepted alias
```

Then, under **Webhook fields**, subscribe to **`messages`**. Saving the callback
URL alone does not deliver anything.

> On a sleeping free-tier host, open the site in a browser first so the service is
> warm, then click *Verify and save* — Meta gives up in seconds and a cold start
> takes much longer.

---

## 2. What the customer sees

> **A simulator deployment now says so, loudly.** At boot the log prints either
> `WhatsApp: LIVE via phone number id …` or `WhatsApp: SIMULATOR — replies are
> stored but NOT sent… Missing: WA_MODE=live, WA_ACCESS_TOKEN`. The inbox shows the
> same warning as a banner, every reply is stored with status `simulated` (and gets
> **no** delivery tick — a receipt for a message that was never sent is worse than
> none), and `GET /api/whatsapp/status` reports `live`, `mode` and `missing`.
>
> This exists because it did not before: a deployment sat in simulator mode
> accepting messages, answering them into a void, and drawing delivery ticks the
> whole time. ConnectLink never had this failure mode because it always posts to
> the Graph API and always reports the status code.

Any of `hi`, `hello`, `hie`, `mhoro`, `sawubona`, `menu` opens the greeting and
the main menu. It is a **list**, not buttons: WhatsApp caps buttons at three, and
that cap is what used to keep the extra rows off the greeting entirely.

| Row | Id | What it does |
|---|---|---|
| Enquiries | `m_enquiries` | The seven services → a short brief on the one picked → the enquiry form (or the chat enquiry when no Flow is built) |
| Track my repair | `m_track` | Job number or registration plate |
| I have paid — send proof | `m_pay` | Bank/EcoCash details, then waits for a screenshot |
| Our services & prices | `m_services` | The seven service lines with "from USD" prices |
| Talk to a person | `m_human` | Hands over **and** raises a callback ticket |
| Language | `m_lang` | English / Shona / Ndebele |
| Contact details | `m_info` | Address, hours, phone, email, website |
| Booking form | `m_bform` | The booking Flow. **Only answered, never offered** — see below |

There is **no "Get a quote" or "Book a service" row.** They led to the same desk
and asked overlapping questions, so the service choice they hid is on the screen
"Enquiries" now opens; two rows that differ only in wording is how a menu stops
being readable.

Booking is **not** advertised either. Holding a slot is a promise the workshop
makes, and the front desk owns that promise — so the customer enquires and the desk
books them in. A customer who does ask still gets the booking flow by typing
*book*, *appointment*, *slot* or *schedule*. `m_bform` stays in the handler table
so a thread that cached the old row cannot dead-end.

A "what next?" menu (also a list) carries the same rows plus **Warranty**
(`m_warranty`).

---

## 3. Conversation flows

### 3.1 Get a quote → an *enquiry*

```
Customer: get a quote
Bot:      What is the vehicle registration number? (e.g. ABC 1234)
Customer: ABC 1234
Bot:      Which service do you need?          ← list of 7, with "from USD" prices
Customer: [Ceramic Coating]
Bot:      Briefly describe the damage… You can also send photos 📷
Customer: [photo] [photo]
Bot:      📷 Photo received (2 so far) — it will be attached to your enquiry.
Customer: Swirl marks all over
Bot:      Thanks. Last thing — what name should we put on the job card?
Customer: Takudzwa Marufu
Bot:      Thanks Takudzwa. And an email address for the quotation? (or type *skip*)
Customer: takudzwa@example.co.zw
Bot:      ✅ Request logged, Takudzwa.
          Reference: TC-ENQ-9A21C4     ← an enquiry, not yet a booking
          Vehicle: ABC1234 · Service: Ceramic Coating
          Photos: 2 received ✅ · Email: takudzwa@example.co.zw
```

Creates a `Customer` (if new), a `Vehicle`, an enquiry (`Booking` with
`status=REQUESTED`, `source=whatsapp`) and one `BookingPhoto` per photo.

Points worth knowing:

- **Attachments: photos *and* PDFs, several of them.** Every inbound `image` or
  `document` is downloaded to `instance/uploads` and held in the conversation
  context as `{url, name, kind, caption}`, capped at `MAX_PENDING_MEDIA` = 8 per
  enquiry (the most recent 8 win). They are written to `booking_photos` when the
  enquiry is created — `kind` is `DAMAGE` for a picture and `DOCUMENT` for
  anything else. An inbound `document` carries an original filename from Meta
  ("assessor report.pdf") and that is what the desk sees, not `wa_<id>.pdf`.
- **A caption becomes the damage description.** If the customer types "rear door
  caved in" with the photo, that is stored as the enquiry's description — the
  media branch runs before the state handlers, so this text used to be discarded.
  It only fills a blank, so it cannot overwrite a description already given.
- The Bookings screen shows a **📎 pill** with the count; tapping it opens the
  viewer (`T.photoViewer`), which draws images and links anything else. In the
  inbox thread the same rule applies — a PDF is a link, never a broken `<img>`.
- **The email is optional.** `skip` works, and an address that fails the loose
  regex is treated as a skip rather than looping the customer.
- The Bookings screen shows a **📷 pill** with the count; tapping it opens the
  viewer (`T.photoViewer`).

### 3.2 Booking a service → an appointment with a time

```
typed "book" | "appointment" | "slot" | "schedule"
  → service list (bsvc:) → next 6 days (day:) → times (bslot:) → contact
```

There is no menu row for this. The desk books customers in; the bot only answers a
customer who asks outright. See the menu table above for why.

- The **time picker is real**: `BOOKING_SLOTS` (`08:00`–`16:00`) filtered by
  `BOOKING_SLOT_CAPACITY` (2 vehicles per slot, `app/constants.py`). A slot only
  counts bookings that are `REQUESTED`, `CONFIRMED` or `ATTENDED` — a cancelled
  appointment gives its time back.
- A **fully booked day** says so and offers another day rather than silently
  moving the customer.
- A typed time is accepted too: `9am`, `09:00`, `2 pm`.
- The chosen day **and** time are stored on the booking and shown in the
  confirmation.

### 3.3 Track my repair

```
Customer: track TC-2026-0004      (or the registration plate)
Bot:      Job card TC-2026-0004
          🚗 Toyota Hilux D4D (ABC1234)
          Stage: Spray Painting   ▰▰▰▰▰▰▰▰▱▱ 82%
          The vehicle is in the spray booth for painting.
          ⚠️ Waiting on 1 part(s): Front bumper — replacement
          📅 Promised date: 18 Sep 2026
```

### 3.4 I've paid — send proof

```
Customer: [I've paid — send proof]
Bot:      💳 How to pay
          CBZ Bank · Topclass Auto Body · Acc … · Branch Msasa
          EcoCash: +263 …
          Our records show INV-2026-0004 with USD 115.00 outstanding.
          📷 If you have already paid, send a photo or screenshot now…
Customer: [screenshot]
Bot:      ✅ Proof received for INV-2026-0004.
          Balance on record: USD 115.00
          Our front desk will check it against our statement and send your receipt.
```

- The proof becomes a `PaymentProof` row against the invoice — **not** a
  `Payment`. A proof is a claim, not money in the bank; somebody still has to
  verify it and record the payment. `is_verified`, `verified_at` and
  `verified_by` exist for that step.
- A **"Verify payment proof" task** lands on the front desk's to-do board.
- The bot targets the newest invoice that is still owing; a settled invoice is
  never offered for payment again.
- A screenshot that arrives with no invoice on file is answered honestly and the
  state resets, rather than inventing a match.
- The Invoices screen shows the same **📷 pill**, opening the shared viewer.

### 3.5 Warranty

The `warranty` keyword now opens a real claim instead of printing small print:

```
Customer: warranty
Bot:      🛡️ [the 12-month workmanship terms]
          We have TC-2026-9100 on file for this number.
          If something has gone wrong, tell me what it is and send a photo if you can.
Customer: The paint is bubbling along the roof
Bot:      🛡️ Warranty claim logged against TC-2026-9100.
```

- Creates a **`Workshop` high-priority task** linked to the job card, with what
  the customer said in the detail.
- Photos are added to the **job card** as `JobPhoto(kind="WARRANTY")`, right next
  to the original damage.
- **One claim per conversation**: further messages and photos append to the same
  ticket. Type `menu` to leave the flow.
- If no job card matches the number, the ticket is still raised and says so, so
  nobody quietly loses a comeback.

### 3.6 Talk to a person → a callback ticket

Setting `human_takeover` only *silenced* the bot; nobody was assigned the request
and nothing timed it. Now:

- A `Task` titled **"Call back {name}"** is created (category *Front desk*,
  priority *HIGH*, due today), assigned to the first front-desk account if one
  exists, with the phone number, the state the bot was in and the customer's last
  message in the detail.
- The customer is given a reference: `🎫 Callback ref: CALL-0007`.
- **Three taps is one phone call** — an open ticket for that number is reused
  rather than duplicated.
- Two consecutive messages the bot cannot answer escalate to the same ticket.

### 3.7 Post-collection feedback

Asked for by the scheduler (see §5), never mid-conversation:

```
Bot: How did we do on TC-2026-9200, Tariro?
     [Excellent] [Okay] [Poor]
```

- **Three options, not five** — WhatsApp caps reply buttons at three, and the
  same three ids (`rate:<job_id>:5|3|1`) are the quick-reply payloads on the
  `job_feedback` template, so a tap means the same thing inside or outside the
  24-hour window.
- The rating is stored on the job (`feedback_rating`, `feedback_text`,
  `feedback_at`).
- A **Poor** rating raises a high-priority front-desk task — the cheapest early
  warning that a job is coming back.
- Stale taps (a job that no longer exists) are thanked rather than crashing.

### 3.8 Language (EN / SN / ND)

Naming a language in any of the three — `Shona`, `chirungu`, `isindebele` — or
tapping `🌐 Language` switches mid-flow and **resumes the step the customer was
on**, keeping `reg` and `service`. A switch works even while the bot is waiting
for an answer, because `_handle_text` checks for a language request before the
state handlers. The bot also guesses from the writing when at least two markers
are present, and never inside a data-entry state.

> The Shona and Ndebele strings were written by a non-native speaker. Have them
> reviewed.

### 3.9 Opt-out

`stop` / `unsubscribe` clears `Customer.whatsapp_opt_in` and every notification
checks it. `start` / `subscribe` brings them back.

---

## 4. Proactive notifications

These fire from the web app, not from the bot. Every attempt is written to
`notification_log` (visible at `GET /api/notifications`) with
`sent` / `failed` / `skipped_optout`.

| Trigger | Template name | Body |
|---|---|---|
| Job moves stage | `job_stage_update` | New stage, plain-English meaning, promised date, blocking parts |
| Job reaches `READY` | `vehicle_ready` | Collection notice, plus a **Check balance** button |
| Estimate saved, "tell the customer" ticked | `quotation_ready` | Reference, total, plus **Approve** / **Decline** / **Download quotation**. Notice only — no PDF, and the estimate stays `DRAFT` |
| **Quotation sent** | `quotation_share` | The quotation **PDF**, plus **Approve** / **Decline** / **Download quotation** |
| **Invoice sent** | `invoice_share` | The tax invoice **PDF**, plus **Download invoice** |
| **Payment recorded** | `receipt_share` | The **receipt PDF**, plus **Download receipt** |
| Last blocking part received | `parts_received` | Part list, "work continues" |
| Invoice issued | `payment_due` | Invoice no., balance due, payment methods, plus **Download Invoice** and **Pay via EcoCash** |
| Vehicle collected | `warranty_registered` | The 12-month workmanship terms |
| **Day before an appointment** | `booking_reminder` | Reference, service, time, address, plus **Move it** and **Cancel appointment** |
| **Day after collection** | `job_feedback` | The three rating buttons |

### ⚠️ Templates must exist in WhatsApp Manager

Outside the 24-hour service window Meta only accepts an **approved template**.
Inside the window (`WaConversation.is_session_open`) free text is used; outside
it the code sends the name above.

**If a template is not registered, the send fails and the reason is recorded in
`notification_log`.** Register these in WhatsApp Manager → Message templates
before going live. `job_feedback` needs three quick-reply buttons whose payloads
are `rate:<job_id>:5`, `:3` and `:1`.

Note `send_quotation`, `send_invoice` and `send_receipt` attach the PDF via a
public link (`/doc/<kind>/<token>.pdf`). Meta downloads that link itself, so
`PUBLIC_BASE_URL` must be publicly reachable over HTTPS.

### Approving a quotation from the chat

| Button | Reply id | Effect |
|---|---|---|
| Approve | `a_approve` | `approve_estimate()` → `APPROVED` |
| Decline | `a_decline` | `DECLINED`, estimator follow-up |
| Download quotation | `doc_quote` | The quotation PDF |

The payloads are **bare** — no estimate id — because a template's payload is
frozen when Meta approves it and cannot interpolate one. Sending the notice
records `last_estimate_id` on the conversation, and every tap (and a typed
*approve* / *decline*) resolves against that record.

Tapping Approve twice says "already approved" rather than replaying the thank-you.

---

## 5. The scheduler

Three jobs need a clock. The first two are nudges in `app/services/notifications.py`
and are **idempotent** (a stamp column stops a second send). The third is the repair
queue for messages the workshop owed a customer and could not send.

```bash
flask booking-reminders        # tomorrow's appointments  (--date YYYY-MM-DD to override)
flask feedback-requests        # yesterday's collections  (--date YYYY-MM-DD to override)
flask outbound-retry           # re-send what the outage swallowed
```

```http
POST /api/bookings/reminders?date=2026-10-05     # manager only
POST /api/jobs/feedback-requests?date=2026-10-04 # manager only
POST /api/whatsapp/outbox/retry                  # manager only
GET  /api/whatsapp/outbox                        # what is waiting / given up
```

On Render, create a **Cron Job** per command (once a day each is plenty for the
nudges; **every few minutes** for the retry — a customer waiting on a reply is
not a once-a-day problem):

```bash
flask --app wsgi booking-reminders
flask --app wsgi feedback-requests
flask --app wsgi outbound-retry
```

Guards:

- `bookings.reminder_sent_at` and `job_cards.feedback_requested_at` are stamped
  **only on a successful send**, so a failed send is retried next run.
- The outbound queue carries its own next-attempt time, so running it too often
  finds nothing due rather than sending anything twice.

### Messages that could not be sent

When the link is down the bot cannot reply, and before `OutboundQueue` existed
those replies were logged as failed and **thrown away** — the customer asked a
question, never heard back, and nobody knew.

- Only **transient** failures are queued (no response from Meta, 429, 5xx). A 400
  means the message itself is wrong and will be wrong every time — that includes a
  free-form reply that sat in the queue long enough to fall outside the 24-hour
  window, which is why the retry path gives up on it rather than looping.
- The whole Meta envelope is stored, so a reply, a button prompt, a document and
  an approved template all replay through the same path.
- A delivered retry **updates** the failed entry in the thread rather than adding
  a second one, or the inbox reads as though the customer was answered twice.
- After 5 attempts it is marked `failed` and surfaced. The desk should phone that
  customer — an automatic system that silently gives up is worse than one that
  never tried.
- Cancelled, no-show and completed bookings are never reminded.
- Only jobs that actually reached `COLLECTED` are asked for feedback.

---

## 6. Testing without a Meta account

**There is no in-app bot simulator any more.** The inbox used to carry a "Try the
bot" panel — a customer-number box, a say-something box and six quick-reply
buttons — and it was removed: it was a second, fake way to drive a conversation
sitting directly under the real one, and the bot's replies appeared in a thread it
had invented rather than in the customer's. Nothing on the WhatsApp screen
fabricates an inbound message now; the thread only shows what actually arrived.

1. **Over HTTP** — `POST /api/whatsapp/simulate` (authenticated):

```jsonc
{ "wa_id": "+263775550555", "body": "quote" }
{ "wa_id": "+263775550555", "interactive_id": "m_track" }
```

It returns the conversation it landed in (`conversation`, `replies_sent`, `state`),
so a script can read back exactly what the customer would have seen.

> It takes **text and interactive ids only** — there is no `media_url` field, so
> the photo flows (enquiry photos, payment proof, warranty photos) cannot be driven
> from it. Use the unit tests for those, or post a real message to the live number.

2. **The unit tests.** The whole bot is covered by `tests/test_whatsapp_bot.py`
   with no network in simulator mode. This is the intended way to exercise it.

3. **A real phone.** With `WA_MODE=live`, message the business number. The thread,
   the state machine, the templates and the timing are all the same as production —
   which is the only test that proves the webhook, the signature check and the send
   loop actually work.

---

## 7. Adding a language

1. Add the code to `LANGUAGES` in `intent_router.py`.
2. Add that key to every entry in the `T` dictionary.
3. `_input_lang` resolves the language names, so add them to `LANGUAGE_NAMES`,
   and add any words a customer might write to `LANGUAGE_WORDS` /
   `LANGUAGE_MARKERS`.

---

## 8. Debugging a live conversation

Everything needed is in the inbox thread or the database:

```sql
SELECT direction, is_bot, state_hint, body, created_at
FROM wa_messages m
JOIN wa_conversations c ON c.id = m.conversation_id
WHERE c.wa_id = '263775550555'
ORDER BY m.id DESC;

SELECT state, context_json, human_takeover FROM wa_conversations
WHERE wa_id = '263775550555';

-- Why did a notification not arrive?
SELECT template, body, status, error, created_at
FROM notification_log ORDER BY id DESC LIMIT 20;
```

`context_json` is the bot's working memory: `reg`, `service`, `damage`, `book_time`,
`pending_media` (a list of `{url, name, kind, caption}`), `warranty_task_id`,
`strikes`, `lang`, `last_job_no`.

| Symptom | Likely cause |
|---|---|
| Every inbound message 403s | `WA_APP_SECRET` is set but wrong — Meta computes the HMAC with the real one. Verify in App settings → Basic. Or `WA_WEBHOOK_TOKEN` is set but the Meta callback URL does not carry `?token=` |
| Verification fails on save | `WA_VERIFY_TOKEN` on the host does not match the Meta box, or the service was cold-starting |
| Bot silent on one thread | `human_takeover` is on for that conversation |
| Nothing arrives at all | The `messages` field is not subscribed |
| A notification says `failed` | Almost always an unregistered message template, or `PUBLIC_BASE_URL` not reachable |
