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
| 3 | `quotation_ready` | — | 3 | 3 quick-reply |
| 4 | `quotation_share` | **Document** | 3 | 3 quick-reply |
| 5 | `invoice_share` | **Document** | 1 | 1 quick-reply |
| 6 | `receipt_share` | **Document** | 1 | 1 quick-reply |
| 7 | `payment_due` | **Text** `INVOICE` | 3 | 2 quick-reply |
| 8 | `parts_received` | — | 2 | — |
| 9 | `warranty_registered` | — | 2 | — |
| 10 | `booking_reminder` | — | 3 | 2 quick-reply |
| 11 | `job_feedback` | — | 2 | 3 quick-reply |
| 12 | `enquiry_form` | — | 0 | 1 Flow button |
| 13 | `booking_form` *(optional)* | — | 1 | 1 Flow button |

**Thirteen templates** (twelve required, `booking_form` optional). Every one that
delivers or refers to a document carries a Download button, and each button names
what it fetches. `parts_received` and `warranty_registered` have no button because
there is no file behind them — don't add one.

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

### 3. `quotation_ready` — the "it's ready" notice

Sent when the desk **ticks "Tell the customer the quotation is ready"** on the
estimate builder, *without* the PDF. It is a heads-up, not the document: the
estimate stays **DRAFT** and only `quotation_share` hands over the PDF and flips
it to **SENT**. Unticking the box sends nothing at all.

Three quick-reply buttons. `{{1}}` first name, `{{2}}` reference, `{{3}}` total
(the number only — the body already says `USD`).

```
Hello {{1}}, your quotation {{2}} is ready.

Total: USD {{3}}

Tap *Approve* to authorise the repair, *Decline* if you would like to discuss it,
or *Download quotation* to keep a copy.
```

Buttons (type: **Quick reply**, in this order):

| Button text | Payload |
|---|---|
| Approve | `a_approve` |
| Decline | `a_decline` |
| Download quotation | `doc_quote` |

> **None of the three carries an id,** because a template's payload is frozen when
> Meta approves it. Sending the notice records which estimate the thread is about,
> and every tap resolves against that. Typing *approve* (or *decline*, *approved
> thanks*, *please proceed*) resolves the same way, so the sentence above is true
> whether the customer taps or types.

### 4. `quotation_share` — the important one

**This is the quotation itself.** Header type **Document**. Three quick-reply
buttons.

- `{{1}}` customer name
- `{{2}}` quotation reference
- `{{3}}` total

```
Hello {{1}}, here is your quotation {{2}} for USD {{3}}.

Tap Approve to authorise the repair, Decline if you would like to discuss it, or
Download quotation to keep a copy. Vehicles are released on settlement of the
account.
```

Buttons (type: **Quick reply**, in this order):

| Button text | Payload |
|---|---|
| Approve | `a_approve` |
| Decline | `a_decline` |
| Download quotation | `doc_quote` |

> **Why the payloads have no id.** A template's payload is frozen at approval, so
> it cannot carry `a_approve:17`. Sending the quotation records the estimate
> against the conversation, and those three bare payloads resolve against it
> (`IntentRouter._handle_choice`). Inside the 24-hour window the buttons are sent
> free-form instead and *do* carry the id — both paths work.

### 5. `invoice_share`

Header type **Document**. One quick-reply button. `{{1}}` customer name.

```
Hello {{1}}, here is your invoice. Tap the file above to open or save it.

Please quote the invoice number with any transfer.
```

Button (type: **Quick reply**):

| Button text | Payload |
|---|---|
| Download invoice | `doc_invoice` |

> **Why this is separate from the receipt.** The Download button has to name what
> it fetches, and a template's buttons are frozen when Meta approves it — so an
> invoice and a receipt cannot share one template. This replaced the old
> `document_share`.

### 6. `receipt_share`

Header type **Document**. One quick-reply button. `{{1}}` customer name.

```
Hello {{1}}, here is your receipt. Tap the file above to open or save it.

Thank you for your business.
```

Button (type: **Quick reply**):

| Button text | Payload |
|---|---|
| Download receipt | `doc_receipt` |

### 7. `payment_due`

Sent when an invoice is issued. **Two** quick-reply buttons — the invoice PDF is
not attached to this notice, so one button fetches it and the other answers
"how do I pay?".

- **Header (text):** `INVOICE` — fixed text, so it needs no parameters
- `{{1}}` customer **first name**, `{{2}}` invoice number, `{{3}}` balance due
  (the number only — the body already says `USD`)
- **Footer:** `TopClass Autobody • Client Notifications`

```
Hello {{1}}, invoice {{2}} has been raised.

Balance due: USD {{3}}

Payment: Cash, EcoCash, InnBucks, bank transfer or card at reception. Please
quote the invoice number with any transfer.
```

Buttons (type: **Quick reply**, in this order):

| Button text | Payload |
|---|---|
| Download Invoice | `doc_invoice` |
| Pay via EcoCash | `m_pay` |

> `m_pay` is the **same id the collection notice uses** for *Check balance*, and
> it answers with the bank details, the EcoCash number and the outstanding
> invoice, then waits for the customer's confirmation screenshot. Reusing it
> means the payment path is one piece of code rather than two that can drift.

### 8. `parts_received`

No button — there is nothing to download.
`{{1}}` customer name, `{{2}}` up to three part names, comma-separated.

```
Good news {{1}} — the parts we were waiting for have arrived ({{2}}).

Work continues and we will update you at the next stage.
```

### 9. `warranty_registered`

No button. `{{1}}` customer name, `{{2}}` job card number.

```
Hello {{1}}, the warranty on job card {{2}} is registered. Keep this message as
your warranty reference.

Topclass Auto Body warrants workmanship for 12 months. Ceramic coating and paint
protection film carry the manufacturer's warranty subject to the maintenance
schedule.
```

### 10. `booking_reminder`

Two quick-reply buttons, so the customer can change or drop the appointment
without typing. `{{1}}` customer name, `{{2}}` when, `{{3}}` reference.
**Note `{{3}}` prints before `{{2}}`** — deliberate, the reference sits by its label.

```
Hello {{1}}, a reminder about your appointment.

Reference: {{3}}
When: {{2}}

Let us know if anything has changed.
```

Buttons (type: **Quick reply**, in this order):

| Button text | Payload |
|---|---|
| Move it | `b_move` |
| Cancel appointment | `b_cancel` |

> **The order is deliberate.** *Move it* — the one people actually want — is
> first, and the destructive button is not where a thumb lands. *Cancel* does not
> cancel on the tap either: it asks “Yes, cancel it / No, keep it” first, and only
> `b_cancel_yes` cancels. An appointment is worth more than the tap that loses it.
>
> The wording moved off “Reply *menu* if you need to move it”, which pointed at
> the main menu — a list of eight options, none of them “move my appointment”.

### 11. `job_feedback`

No button — the three quick replies *are* the interaction.
`{{1}}` first name, `{{2}}` job card number.

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

> A **Poor** tap raises a HIGH-priority task for the front desk automatically.

### 12. `enquiry_form`

Launches the enquiry Flow. No body variables. Button type **Flow**.

```
Hello, thank you for contacting Topclass Auto Body.

Tap the button below to tell us about your vehicle and what you need. It takes
about a minute, and our front desk will call you back with a firm quotation.
```

Button → Flow action `Navigate`, screen `QUESTION_ONE`, flow token `enquiry`.

> The screen name here must match the Flow exactly — Meta rejects a screen the
> Flow does not define and the form will not open. `QUESTION_ONE` is what Meta's
> own builder calls the first screen; if you rename it, set
> `WA_FLOW_ENQUIRY_SCREEN` to match, because the app sends the name too.

### 13. `booking_form` — optional

Sends the **booking** Flow to a customer who has gone quiet. Only needed if you
build the Booking form; the chat booking flow covers customers inside the
window. No body variables. Button type **Flow**.

```
Hello {{1}}, still want to bring the vehicle in?

Tap below to pick a service, a day and a time. It takes about a minute and our
front desk will confirm the appointment.
```

Button → Flow action `Navigate`, screen `BOOKING`, flow token `booking`.
`{{1}}` is the customer's first name.

---

## Part 2 — The Flows

> **This part is superseded by `docs/META-FLOWS.md`.** There are now **two**
> Flows — the Request form (`QUESTION_ONE`) and the Booking form (`BOOKING`) — and the
> service list below is out of date. Build them from `docs/META-FLOWS.md`; this
> section is kept only so a reader who follows an old link is pointed somewhere
> useful.

### ⚠️ CORRECTED — a Flow **can** carry a file

This section used to claim Flows have no file-upload component. **That was
wrong.** Meta's Flow component reference has a *Media upload* section listing
**Photo Picker** and **Document Picker**, and ConnectLink reads the result from
`response_json` as an `attachment` array of media ids.

See `docs/META-FLOWS.md` for the corrected position and what is still to build.
The chat-based fallback described below still works and is still what runs today.

So the design is:

1. The customer fills in **the form** — service, vehicle make & model, and
   (optionally) photographs of the damage, inside the Flow's own picker.
2. If the Flow carried no photographs, the bot then **asks for them in the chat**,
   and any photo or PDF they send is attached to the enquiry automatically.

You do **not** need to do anything to enable the chat fallback — it already works,
keeps up to the **last 8 attachments**, and accepts images and PDFs together. A
customer who sends 3 damage photos plus an assessor's PDF gets all 4 attached to
the enquiry with their own filenames kept.

### The screen and fields

The Flow needs **one screen**, and **the screen's API name must match what the app
sends** — Meta rejects a screen the Flow does not define, so the form simply will
not open. Meta's builder calls the first screen `QUESTION_ONE`, which is the
default; `WA_FLOW_ENQUIRY_SCREEN` overrides it.

⚠️ **The field names do not need to match the app.** They are read by meaning —
see `docs/META-FLOWS.md` §0 — so the builder's own names work as they are. The
form asks for **three** things, because the name comes from the WhatsApp profile
and the plate is taken when the car arrives.

| Field label in the builder | Component `name` (the builder's is fine) | Component | Required |
|---|---|---|---|
| What do you need | `What_do_you_need_11da7f` | Dropdown | **yes** |
| Vehicle Make, Model | `Vehicle_Make_Model_2e7fab` | Text input | **yes** |
| Describe the enquiry | `Describe_the_enquiry` | Text area | no |
| Photos of the damage or vehicle | `Photos_of_the_damage` | `PhotoPicker` or `DocumentPicker` | no |

**The names are resolved by meaning**, so a rename in the builder degrades to a
blank on the record rather than an error. The `enquiry_form` template's *Open
form* button must point at the same screen the app names — `QUESTION_ONE` by
default.

**`service`** must offer the service lines exactly as they appear in the app
(`app/constants.py → SERVICES`), because the answer is matched against them
to pick the rate card:

- Auto Body
- Panel Beating & Spray Painting
- Rebuilds & Performance Upgrades
- Car Detailing
- Ceramic Coating
- Paint Protection Film
- Car Vinyl Wrapping

Set each option's **`id` and `title` to the same string**: a Dropdown returns the
`id`, so `auto_body` would silently fall through to *Panel Beating & Spray
Painting*. There are no day, time or registration fields on the enquiry form —
those belong to the **booking** form.

An unrecognised `service` is handled gracefully — the enquiry is still raised
against the default service rather than being lost.

> **`docs/META-FLOWS.md` is the authoritative spec** for both Flows: field tables,
dropdown options, completion payloads and a JSON skeleton you can paste in.

### What happens when it is submitted

1. Customer and vehicle are found or created (the plate is normalised, so
   `adz 4477` becomes `ADZ4477`).
2. An **enquiry** is raised: `TC-ENQ-…`, status `REQUESTED`, source `whatsapp`.
3. Every attachment is attached to it — the Flow's own picker files **and**
   anything sent earlier in the chat, in one list.
4. The customer gets a confirmation with the reference. The photo prompt follows
   **only when the Flow carried no files**.

A form whose answers are all blank is **not** turned into an enquiry — the
customer is asked to resend, rather than the desk getting a hollow record for
every retry.

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
| Estimate saved, box ticked | `notify_quote_ready` | `quotation_ready` (notice only) |
| Quotation sent from the job card | `send_quotation` | `quotation_share` (the PDF) |
| Invoice sent | `send_invoice` | `invoice_share` |
| Payment recorded | `send_receipt` | `receipt_share` |
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
| `doc_invoice` | invoice + payment buttons | Sends the invoice PDF |
| `doc_receipt` | receipt buttons | Sends the receipt PDF |
| `m_pay` | collection notice, invoice notice | Payment details, then waits for proof |
| `b_move` | booking reminder | New day and time, then moves the appointment |
| `b_cancel` | booking reminder | Asks before cancelling |
| `b_cancel_yes` | the confirm prompt | Booking → `CANCELLED` |
| `b_cancel_keep` | the confirm prompt | Leaves it alone |
| `rate:5` / `rate:3` / `rate:1` | feedback buttons | Records the rating |
| `m_*` | the menus | Menu navigation |

The versions **without** an id are the ones an approved template can send; the
ones **with** an id come from free-form buttons inside the 24-hour window. Both
are handled.

---

## Part 4 — Checklist

- [ ] Create the 13 templates above (names exactly as written, `Utility`)
- [ ] `quotation_share`: document header + Approve / Decline / Download quotation
- [ ] `invoice_share`: document header + Download invoice
- [ ] `receipt_share`: document header + Download receipt
- [ ] `payment_due`: text header `INVOICE`, footer, and **Download Invoice**
      (`doc_invoice`) + **Pay via EcoCash** (`m_pay`)
- [ ] `vehicle_ready`: Check balance
- [ ] `booking_reminder`: **Move it** (`b_move`) + **Cancel appointment**
      (`b_cancel`)
- [ ] `job_feedback`: Excellent / Okay / Poor
- [ ] `enquiry_form`: Flow button pointing at the published Request form Flow
- [ ] Create the **Request form** Flow — screen `QUESTION_ONE`, one screen, three fields
- [ ] Create the **Booking form** Flow — screen `BOOKING` (see `docs/META-FLOWS.md`)
- [ ] Publish both Flows and copy their ids — a menu row appears only once its id is set
- [ ] Set `WA_FLOW_ENQUIRY_ID` and `WA_FLOW_BOOKING_ID` on the host and restart
- [ ] Set `WA_APP_SECRET` — still outstanding, and the webhook currently accepts
      unsigned posts, so anyone who learns the URL can forge a customer message
- [ ] `PUBLIC_BASE_URL` must be public HTTPS: Meta fetches every PDF itself

### Where the templates are not yet needed

`job_stage_update`, `vehicle_ready`, `parts_received`, `warranty_registered`,
`payment_due` and `quotation_ready` are only used when the customer has gone
quiet. Inside the 24-hour window the same words are sent as ordinary messages,
which cost nothing. Create them all anyway — a job card routinely outlives the
window.
