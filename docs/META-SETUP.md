# Meta setup — every template and Flow to create

Everything the bot needs, in the order to create it. Each entry says what the
code sends, so the `{{n}}` order is not a guess.

Two rules decide almost everything here:

1. **Free-form messages only work for 24 hours** after the customer's last
   message. Outside that window Meta accepts *only* an approved template. Every
   notification below therefore has a template counterpart.
2. **A template's buttons are fixed when it is approved.** Meta cannot
   interpolate an id into them, so the button payloads below are static and the
   code resolves them against the conversation. That is why `quotation_share`
   and `job_feedback` exist as separate templates rather than reusing the plain
   ones.

---

## Part 1 — Message templates

Create these in **WhatsApp Manager → Account tools → Message templates**.
Language: **English (en)** for all of them. Category: **Utility** for all of
them (they are all about a transaction the customer already started, which is
also the cheaper category).

Names must match exactly — they are the constants in
`app/services/notifications.py`.

| # | Template name | Header | Body variables | Buttons |
|---|---|---|---|---|
| 1 | `job_stage_update` | — | 3 | — |
| 2 | `vehicle_ready` | — | 2 | 1 quick-reply |
| 3 | `quotation_ready` | — | 3 | — |
| 4 | `quotation_share` | **Document** | 3 | 3 quick-reply |
| 5 | `document_share` | **Document** | 1 | — |
| 6 | `payment_due` | — | 3 | — |
| 7 | `parts_received` | — | 2 | — |
| 8 | `warranty_registered` | — | 2 | — |
| 9 | `booking_reminder` | — | 3 | — |
| 10 | `job_feedback` | — | 2 | 3 quick-reply |
| 11 | `enquiry_form` | — | 0 | 1 Flow button |

---

### 1. `job_stage_update`

Sent when a job card moves stage.

- `{{1}}` customer name
- `{{2}}` stage label (e.g. `Panel beating`)
- `{{3}}` what that stage means, in plain English

```
Hello {{1}}, an update on your vehicle.

Stage: {{2}}
{{3}}

Reply *track* to check progress any time.
```

### 2. `vehicle_ready`

Sent when the job reaches READY. One quick-reply button.

- `{{1}}` customer first name
- `{{2}}` registration number

```
Good news {{1}} — your vehicle ({{2}}) is ready for collection.

Please bring your ID and collection slip. If a balance is due, tap *Check
balance* for the payment details.
```

Button (type: **Quick reply**):

| Button text | Payload |
|---|---|
| Check balance | `m_pay` |

> **Why the balance is not in the body.** A template's body is frozen when Meta
> approves it, so a figure written into it is a snapshot from the moment it was
> sent — a fortnight later the customer is still reading that number. The button
> answers from the invoice as it stands, and the same button is sent free-form
> inside the 24-hour window, so a tap means the same thing either side of it.
>
> `m_pay` already does the right thing: bank details, the outstanding invoice and
> its balance, then "send a screenshot if you have already paid" — and it puts
> the customer in the state that captures a payment proof. On a settled account
> it simply omits the balance rather than showing USD 0.00.

### 3. `quotation_ready`

Sent when an estimate is raised, *without* the PDF (the "approve/decline" nudge).

- `{{1}}` customer name
- `{{2}}` quotation reference
- `{{3}}` total

```
Hello {{1}}, your quotation {{2}} is ready.

Total: USD {{3}}

Reply *approve* to authorise the repair, or *decline* and our team will call you.
```

### 4. `quotation_share` — the important one

**This is the quotation itself.** Header type **Document**. Three quick-reply
buttons.

- `{{1}}` customer name
- `{{2}}` quotation reference
- `{{3}}` total

```
Hello {{1}}, here is your quotation {{2}} for USD {{3}}.

Tap Approve to authorise the repair, Decline if you would like to discuss it, or
Download to keep a copy. Vehicles are released on settlement of the account.
```

Buttons (type: **Quick reply**, in this order):

| Button text | Payload |
|---|---|
| Approve | `a_approve` |
| Decline | `a_decline` |
| Download | `doc_quote` |

> **Why the payloads have no id.** A template's payload is frozen at approval, so
> it cannot carry `a_approve:17`. Sending the quotation records the estimate
> against the conversation, and those three bare payloads resolve against it
> (`IntentRouter._handle_choice`). Inside the 24-hour window the buttons are sent
> free-form instead and *do* carry the id — both paths work.

### 5. `document_share`

Header type **Document**. Used for the **invoice** and the **receipt** (anything
with no buttons attached).

- `{{1}}` customer name

```
Hello {{1}}, here is the document you asked for. Tap the file above to open or
save it.

Reply *menu* if you need anything else.
```

### 6. `payment_due`

Sent when an invoice is issued.

- `{{1}}` customer name
- `{{2}}` invoice number
- `{{3}}` balance due

```
Hello {{1}}, invoice {{2}} has been raised.

Balance due: USD {{3}}

Payment: Cash, EcoCash, InnBucks, bank transfer or card at reception. Please
quote the invoice number with any transfer.
```

### 7. `parts_received`

Sent when the last blocking part lands.

- `{{1}}` customer name
- `{{2}}` up to three part names, comma-separated

```
Good news {{1}} — the parts we were waiting for have arrived ({{2}}).

Work continues and we will update you at the next stage.
```

### 8. `warranty_registered`

Sent on collection.

- `{{1}}` customer name
- `{{2}}` job card number

```
Hello {{1}}, the warranty on job card {{2}} is registered. Keep this message as
your warranty reference.

Topclass Auto Body warrants workmanship for 12 months. Ceramic coating and paint
protection film carry the manufacturer's warranty subject to the maintenance
schedule.
```

### 9. `booking_reminder`

The day-before nudge. This one is **designed** to land outside the window — the
customer booked days ago and has said nothing since.

- `{{1}}` customer name
- `{{2}}` when, e.g. `Mon 06 Oct 2026 at 09:00`
- `{{3}}` reference (`TC-ENQ-…` or `TC-BKG-…`)

```
Hello {{1}}, a reminder about your appointment.

Reference: {{3}}
When: {{2}}

Reply *menu* if you need to move it.
```

> Note the order: `{{3}}` is printed before `{{2}}`. That is deliberate and
> matches the code — the reference is what the customer repeats back, so it sits
> next to the label.

### 10. `job_feedback`

The day-after-collection ask. Three quick-reply buttons.

- `{{1}}` customer first name
- `{{2}}` job card number

```
Hello {{1}}, how did we do on job card {{2}}?

One tap tells the workshop owner. If anything was not right, tap Poor and tell us
what happened — we would rather hear it from you.
```

Buttons (type: **Quick reply**, in this order):

| Button text | Payload |
|---|---|
| Excellent | `rate:5` |
| Okay | `rate:3` |
| Poor | `rate:1` |

> A **Poor** tap raises a HIGH-priority task for the front desk automatically. It
> is the only early warning that a job is coming back, so it is a task rather
> than a line in a report.

### 11. `enquiry_form`

The template that **launches the enquiry Flow** from outside the 24-hour window
(a Flow cannot be sent free-form to a cold customer).

- No body variables.

```
Hello, thank you for contacting Topclass Auto Body.

Tap the button below to tell us about your vehicle and what you need. It takes
about a minute, and our front desk will call you back with a firm quotation.
```

Button (type: **Flow**):

| Field | Value |
|---|---|
| Button text | `Open form` |
| Flow | the **Enquiry form** Flow created in Part 2 |
| Flow action | `Navigate` |
| Screen | `ENQUIRY` |
| Flow token | `enquiry` |

---

## Part 2 — The enquiry Flow

**WhatsApp Manager → Flows → Create flow → Custom.** Name it
**Enquiry form**. The endpoint URL is not needed: this app only reads the
answers, it does not serve the Flow.

### ⚠️ A Flow cannot upload a file

There is **no file-upload component** in WhatsApp Flows — this is a platform
limitation, not a gap in this app. (ConnectLink has the same constraint; its
Flow is a form too.)

So the design is:

1. The customer fills in **the form** — plate, service, description, day.
2. The bot then **asks for the photographs in the chat**, and any photo or PDF
   they send is attached to the enquiry automatically.

You do **not** need to do anything to enable step 2 — it already works, keeps up
to the **last 8 attachments**, and accepts images and PDFs together. A customer
who sends 3 damage photos plus an assessor's PDF gets all 4 attached to the
enquiry with their own filenames kept.

### The screen and fields

The Flow needs **one screen with the API name `ENQUIRY`** (the code opens
`flow_action_payload.screen = "ENQUIRY"`).

These field names must be used **exactly** — they are what
`IntentRouter._lead_from_flow` reads. Misspelling one does not raise; that field
simply arrives empty.

| Field label in the builder | API name (must match) | Component | Required |
|---|---|---|---|
| Your name | `contact_name` | Text input | yes |
| Mobile or email | `contact_email` | Text input | no |
| Registration number | `reg_no` | Text input | yes |
| Vehicle | `vehicle` | Text input | no |
| What do you need? | `service` | Dropdown | yes |
| Describe the damage | `damage` | Text area | yes |
| Preferred day | `preferred_date` | Date picker | no |
| Preferred time | `preferred_time` | Dropdown | no |

**`service`** must offer the seven service lines exactly as they appear in the
app (`app/constants.py → SERVICES`), because the answer is matched against them
to pick the rate card:

- Panel Beating & Spray Painting
- Car Detailing
- Ceramic Coating
- Paint Protection Film
- Car Vinyl Wrapping
- Mechanical Repairs
- Auto Electrical

**`preferred_time`** should offer the workshop's slots: `08:00`, `09:00`,
`10:00`, `11:00`, `12:00`, `13:00`, `14:00`, `15:00`, `16:00`.

**`preferred_date`** must send an ISO date (`YYYY-MM-DD`), which is what the
date picker gives you by default.

An unrecognised `service` is handled gracefully — the enquiry is still raised
against the default service rather than being lost.

### What happens when it is submitted

1. Customer and vehicle are found or created (the plate is normalised, so
   `adz 4477` becomes `ADZ4477`).
2. An **enquiry** is raised: `TC-ENQ-…`, status `REQUESTED`, source `whatsapp`.
3. Every attachment sent earlier in the chat is attached to it.
4. The customer gets a confirmation with the reference, then a prompt for photos.

A form that arrives empty or garbled is **not** turned into an enquiry — the
customer is asked to resend, rather than the desk getting a record reading
"reg TBC".

### Wiring the Flow up

1. Create the Flow and **publish** it (a draft Flow can only be opened in the
   test view).
2. Copy the **Flow ID** (a long number) into the `WA_FLOW_ENQUIRY_ID`
   environment variable.
3. Restart. The **"Enquiry form"** row appears in the bot's main menu
   automatically — it is hidden while `WA_FLOW_ENQUIRY_ID` is empty, because a
   menu row that opens nothing is worse than no row.

`WhatsAppClient.send_flow` currently opens the Flow in `draft` mode, which is
what Meta requires while you are building it. Once published, change
`flow_action_payload` / the screen mode in `app/services/whatsapp_client.py`
(`send_flow`) to `published`.

---

## Part 3 — Which code sends what

| Trigger | Function | Template used |
|---|---|---|
| Job changes stage | `notify_stage_change` | `job_stage_update` |
| Job reaches READY | `notify_ready_for_collection` | `vehicle_ready` |
| Estimate raised | `notify_quote_ready` | `quotation_ready` |
| Quotation sent | `send_quotation` | `quotation_share` |
| Invoice sent | `send_invoice` | `document_share` |
| Payment recorded | `send_receipt` | `document_share` |
| Parts arrive | `notify_parts_received` | `parts_received` |
| Invoice issued | `notify_invoice_issued` | `payment_due` |
| Vehicle collected | `notify_warranty` | `warranty_registered` |
| Day before appointment | `notify_booking_reminder` | `booking_reminder` |
| Day after collection | `notify_feedback_request` | `job_feedback` |
| Cold customer wants a form | the enquiry Flow / `enquiry_form` | `enquiry_form` |

### The buttons the customer can tap

| Payload | Comes from | Effect |
|---|---|---|
| `a_approve:<id>` / `a_approve` | quotation buttons | Estimate → `APPROVED` |
| `a_decline:<id>` / `a_decline` | quotation buttons | Estimate → `DECLINED` |
| `doc:quote:<id>` / `doc_quote` | quotation buttons | Sends the quotation PDF |
| `doc:invoice:<id>` | invoice buttons | Sends the invoice PDF |
| `doc:receipt:<id>` | receipt buttons | Sends the receipt PDF |
| `rate:5` / `rate:3` / `rate:1` | feedback buttons | Records the rating |
| `m_*` | the menus | Menu navigation |

The versions **without** an id are the ones an approved template can send; the
ones **with** an id come from free-form buttons inside the 24-hour window. Both
are handled.

---

## Part 4 — Checklist

- [ ] Create the 11 templates above (names exactly as written, `Utility`)
- [ ] `quotation_share`: document header + Approve / Decline / Download quick replies
- [ ] `document_share`: document header
- [ ] `job_feedback`: Excellent / Okay / Poor quick replies
- [ ] `enquiry_form`: Flow button pointing at the published Enquiry Flow
- [ ] Create the **Enquiry form** Flow with screen `ENQUIRY` and the field names above
- [ ] Publish the Flow, copy its id
- [ ] Set `WA_FLOW_ENQUIRY_ID` on the host and restart
- [ ] Set `WA_APP_SECRET` — still outstanding, and the webhook currently accepts
      unsigned posts, so anyone who learns the URL can forge a customer message
- [ ] `PUBLIC_BASE_URL` must be public HTTPS: Meta fetches every PDF itself

### Where the templates are not yet needed

`job_stage_update`, `vehicle_ready`, `parts_received`, `warranty_registered`,
`payment_due` and `quotation_ready` are only used when the customer has gone
quiet. Inside the 24-hour window the same words are sent as ordinary messages,
which cost nothing. Create them all anyway — a job card routinely outlives the
window.
