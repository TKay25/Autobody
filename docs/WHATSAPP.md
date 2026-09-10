# WhatsApp chatbot — operator guide

## 1. What the customer sees

**Greeting** (any of `hi`, `hello`, `hie`, `mhoro`, `sawubona`, `menu`):

> Hello Takudzwa! 👋 Welcome to Topclass Auto Body.
> We are Masters of Restoration — panel beating, spray painting, detailing, ceramic
> coating and PPF.
>
> How can we help you today?
>
> `[Get a quote]` `[Track my repair]` `[My claim]`

## 2. Conversation flows

### Get a quote
```
Customer: get a quote
Bot:      Panel Beating & Spray Painting — noted. 👍
          What is the vehicle registration number? (e.g. ABC 1234)
Customer: ABC 1234
Bot:      Which service do you need?   ← list of 7 services with "from USD" prices
Customer: [Ceramic Coating]
Bot:      Briefly describe the damage or what you need done. You can also send photos 📷.
Customer: [photo]  →  attached to the job card
Customer: Swirl marks all over, needs full correction
Bot:      Thanks. Last thing — what name should we put on the job card?
Customer: Takudzwa Marufu
Bot:      ✅ Request logged, Takudzwa.
          Reference: TC-BKG-9A21C4
          Vehicle: ABC1234
          Service: Ceramic Coating
          Indicative price: from USD 350.
          Our front desk will confirm your booking and send the firm quotation…
```
Creates a `Customer` (if new), a `Vehicle`, and a `Booking` with
`source='whatsapp'` — it lands in **Bookings** in the web app.

### Track my repair
```
Customer: track TC-2026-0004
Bot:      Job TC-2026-0004
          🚗 Toyota Hilux D4D (ABC1234)
          Stage: Spray Painting
          ▰▰▰▰▰▰▰▰▱▱ 82%
          The vehicle is in the spray booth for painting.
          ⚠️ Waiting on 1 part(s): Front bumper — replacement
          📅 Promised date: 18 Sep 2026
```
Accepting either a job number (`TC-2026-0004`) or a registration plate.

### My claim
```
Customer: my claim
Bot:      🛡️ Claim CLM48213 — Old Mutual
          🚗 ABC1234 (job TC-2026-0002)
          Status: Submitted to Insurer
          Assessor: Mr. B. Chiweshe
          Assessment booked: 12 Sep 2026
          Excess: USD 150.00 — outstanding ⏳
          ⏳ Waiting 6 day(s) for the insurer's decision.
```

### Booking a service (detailing / coating / PPF)
Service list → next 6 days → contact → confirmation with a `TC-BKG-*` reference.

## 3. Proactive notifications

These fire automatically, from the web app, not from the bot:

| Trigger | Template | Body highlights |
|---|---|---|
| Job moves stage | `job_stage_update` | New stage, plain-English explanation, promised date, blocking parts |
| Job reaches `READY` | `vehicle_ready` | Balance due, payment methods, collection address, warranty note |
| Estimate created | `quotation_ready` | Reference, total, excess, reply `approve` / `decline` |
| **Quotation sent from the web app** | `document_share` | The quotation **PDF** as an attachment, plus Approve/Decline buttons |
| **Invoice sent** | `document_share` | The tax invoice **PDF**, totals and outstanding balance |
| **Payment recorded** | `document_share` | The **receipt PDF**, amount, method and the balance remaining |
| Last blocking part received | `parts_received` | Part list, "work continues" |
| Invoice issued | `payment_due` | Invoice no., total, balance, due date |
| Vehicle collected | `warranty_registered` | 12-month workmanship terms |

### Document attachments

`send_document()` posts a Meta `type: "document"` message with `{ link, filename, caption }`.
Meta downloads that link itself, so it must be reachable from the public internet over
HTTPS — set `PUBLIC_BASE_URL` in `.env`. Links are token-gated (`/doc/<kind>/<token>.pdf`)
and need no login, so the customer can forward them.

### Approving a quotation from the chat

`send_quotation()` follows the PDF with an interactive button message:

| Button | Reply id | Effect |
|---|---|---|
| Approve | `a_approve:<estimate_id>` | `approve_estimate()` → estimate `APPROVED`, claim and job flow unblocked |
| Decline | `a_decline:<estimate_id>` | Estimate `DECLINED`, estimator follow-up flagged |

The tap is handled in `IntentRouter._quotation_decision()` and written to
`notification_log` as `quotation_decision`. Unknown or stale ids get a friendly
"I could not find that quotation" reply rather than a crash.

Every attempt is written to `notification_log` (visible via `GET /api/notifications`)
with `sent` / `failed` / `skipped_optout`.

## 4. Compliance rules the code handles for you

| Rule | Implementation |
|---|---|
| 24-hour service window | `WaConversation.is_session_open` — inside it we send free text, outside it we send an approved template |
| Opt-out | `stop` / `unsubscribe` sets `Customer.whatsapp_opt_in = False`; every notification checks it. `start` re-subscribes |
| Webhook retries | The webhook always returns HTTP 200, even on internal error, so Meta doesn't hammer you |
| Webhook authenticity | Verification via `hub.verify_token`; CSRF is exempted for that blueprint only |
| Media retention | Inbound images are copied into `instance/uploads`, never hot-linked from Meta's CDN (those URLs expire) |

## 5. Testing without a Meta account

Two ways:

1. **In the app** — `#/inbox` → *Bot simulator*. Type as the customer, or click the
   quick-test buttons. Every reply is rendered in the conversation thread.
2. **Over HTTP** — `POST /api/whatsapp/simulate` (authenticated):

```jsonc
{ "wa_id": "+263775550555", "body": "quote" }          // free text
{ "wa_id": "+263775550555", "interactive_id": "m_track" }  // button click
```

## 6. Adding a language

1. Add the code to `LANGUAGES` in `intent_router.py`.
2. Add that key to each entry in the `T` dictionary.
3. The bot also needs the phrase for the language chip in the `lang:` list handler —
   `_input_lang` resolves `english` / `shona` / `ndebele` or a bare code.

## 7. Debugging a live conversation

Everything needed is in the inbox thread or the database:

```sql
SELECT direction, is_bot, state_hint, body, created_at
FROM wa_messages m
JOIN wa_conversations c ON c.id = m.conversation_id
WHERE c.wa_id = '263775550555'
ORDER BY m.id DESC;

SELECT state, context_json, human_takeover FROM wa_conversations
WHERE wa_id = '263775550555';
```

`context_json` tells you exactly what the bot was holding when it answered
(`reg`, `service`, `damage`, `strikes`, `lang`, `last_job_no`).
