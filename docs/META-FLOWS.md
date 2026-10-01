# WhatsApp Flows — the two forms, and exactly what to type into Meta

This is the build spec for the two Flows the bot offers: the **Request form**
(what is wrong with the vehicle) and the **Booking form** (when to bring it in).

Both are **custom Flows** built in WhatsApp Manager → Flows → *Create flow* →
**Custom**. Neither needs an endpoint URL: this app only *reads* the answers, it
never serves the Flow.

> **Companion document.** `docs/META-SETUP.md` covers the **templates** (the 12
> message templates and their buttons) and Part 2 there holds the older, single
> enquiry-form spec. This file supersedes that section — where the two disagree,
> this one is right, because it was written against the code.

---

## The two journeys, end to end

There are **two requests a customer can make**, and **each has two ways in** — a
guided chat flow and a form. Both ways end at the same record.

### Request — "something is wrong with my vehicle, what will it cost?"

```
ENTRY (any of three)
  a) Main menu row ... "Get a quote"            -> m_quote
  b) Main menu row ... "Enquiry form"           -> m_form   (the WhatsApp Flow)
  c) Typed ........... "quote", "price", "how much"

CHAT PATH  (states in WaConversation.state)
  QUOTE_REG      "Sure. What is the vehicle registration number? (e.g. ABC 1234)"
                 -> plate normalised: "adz 4477" becomes "ADZ4477"; under 3 chars is rejected
  QUOTE_SERVICE  a list of the 7 services, with "from USD n" beside each
  QUOTE_DESC     "Please describe the damage. Send photos, and a PDF of any assessment…"
                 -> any photo or PDF sent here is attached to the record
  QUOTE_CONTACT  "what name should we put on the job card?"  (*skip* uses the WhatsApp profile name)
  QUOTE_EMAIL    "And an email address for the quotation?"   (*skip* is fine — never re-asked)
  -> _create_lead()

FORM PATH
  The customer taps "Enquiry form" -> the ENQUIRY screen opens inside WhatsApp.
  On submit, _lead_from_flow() writes the same context and calls the SAME
  _create_lead(), then asks for the photographs in the chat.

RAISED
  Customer + Vehicle found or created        (plate normalised, never "TBC")
  Booking: TC-ENQ-…, status REQUESTED, source whatsapp
  Every attachment sent at any point, up to 8, attached

CUSTOMER GETS
  "Request logged, <first name>." then reference, vehicle, service, and an
  indicative price — that last one only for the services we can price from the
  rate card without seeing the vehicle (Car Detailing, Ceramic Coating, Paint
  Protection Film, Car Vinyl Wrapping).
  Each of the following appears only when it is true: the preferred slot if a day
  was given, the attachment count if anything was sent, the email if one was
  captured. Closing line: the front desk will confirm the booking and send the
  firm quotation during business hours. Plus "Anything else I can help with?".
  The form path gets one extra message before the menu: send photographs of the
  damage, and any assessor's report as a PDF.
```

### Booking request — "I want to bring it in on a day"

```
ENTRY (any of four)
  a) Main menu row ... "Book a service"          -> m_book
  b) Main menu row ... "Booking form"            -> m_bform  (the WhatsApp Flow)
  c) Typed ........... "book", "appointment", "slot"
  d) The reminder's "Move it" button             -> b_move   (MOVES an existing one)

CHAT PATH
  BOOK_SERVICE   the same 7 services, but under their own "bsvc:" ids so a tap
                 cannot be mistaken for the quote flow's "svc:" ids
  BOOK_DATE      the next 6 days, Saturday marked "Limited (Saturday)"
  BOOK_TIME      only the times with room — capacity is 2 vehicles per slot,
                 and a typed time into a full slot is refused
  BOOK_CONTACT   name (or *skip*) -> _create_lead()

FORM PATH
  The "Booking form" row opens the BOOKING screen. On submit,
  _booking_from_flow() checks the slot's capacity BEFORE writing the record,
  then calls _create_lead(). No plate needed; no photo request.

REMINDER PATH  (the day before: booking_reminder template)
  "Move it"      -> _booking_move()   reminds the context WHICH booking is being
                    moved, then reuses BOOK_DATE -> BOOK_TIME and lands the new
                    slot on that same booking. It does not raise a second one.
  "Cancel appointment" -> asks "Yes, cancel it / No, keep it" first.

RAISED
  Booking: TC-ENQ-…, status REQUESTED, source whatsapp, with the chosen slot
  Moveable and cancellable by the customer right up to the day

CUSTOMER GETS
  "Request logged, <first name>." then reference, service, indicative price, and
  the slot. A booking adds one line an enquiry does not: either "Every appointment
  is confirmed by the desk, so watch for a message" or, if the slot was already
  full, that the desk will offer the nearest alternative.
```

### What the desk does with it

Both arrive on **Enquiries & Bookings** as a `REQUESTED` record. The desk drives
it from there, and the wording follows the house rule — a staff member *attends to
an enquiry*, a *booking* is *confirmed*:

```
REQUESTED   "New enquiry"          nobody has picked it up yet
ATTENDED    "Attended to"          a staff member has dealt with it
CONFIRMED   "Booking confirmed"    only now is it a booking, and only now does it
                                   earn a TC-BKG-… reference beside its TC-ENQ-…
ARRIVED     "Customer came through"
COMPLETED   "Completed"  |  NO_SHOW "Did not arrive"  |  CANCELLED "Cancelled"
```

Moving a confirmed booking from the desk (`POST /api/bookings/<id>/reschedule`)
and moving it from the customer's *Move it* button run the **same code**
(`app/services/bookings.py`), so the two can never disagree about what changed.

---

## 0. Read this first

### A Flow **can** carry a file — I had this wrong

An earlier version of this document claimed Flows have no file-upload component.
**That was wrong.** Meta's Flow component reference has a *Media upload* section,
and its component list includes **Photo Picker** and **Document Picker** — the
`sensitive`-field table on the same page even specifies how each is masked
("Hidden uploaded media completely" / "Hidden uploaded documents completely").

ConnectLink uses exactly this: its enquiry Flow's `response_json` carries

```json
"attachment": [ { "id": "…", "mime_type": "…", "sha256": "…", "file_name": "…" } ]
```

and the business then downloads each entry from the Graph API by that `id`, stores
the bytes against the enquiry, and can hand it back later with a Download button.
That is the pattern to copy.

**Status in this repo.** The picker is *not* wired up yet — our parser reads text
fields only, and the bot still asks for photographs in the chat after the form.
Both routes work, and they are not mutually exclusive:

| Route | When | State |
|---|---|---|
| **In the Flow** (Photo / Document Picker) | customer fills the form | **to build** — see below |
| **In the chat**, after the form | customer skipped the picker | **working today** |

The chat route needs nothing from you: it keeps the **last 8 attachments** and
accepts images and PDFs together. A customer who sends 3 damage photos plus an
assessor's PDF gets all 4 attached to the enquiry, with their own filenames kept.

The **booking** form asks for neither — an appointment is not a damage report, and
its confirmation must not read like one.

### To wire the picker up, two things are needed

1. **Meta side** — add a `PhotoPicker` (and, for the assessor's report, a
   `DocumentPicker`) to the `ENQUIRY` screen, and include `attachment` in the
   screen's `complete` payload so it reaches us in `response_json`.
2. **Code side** — read `data["attachment"]` in `IntentRouter._lead_from_flow`,
   download each entry from `GET /{media-id}` with the access token, and store it
   as a `BookingPhoto` against the enquiry — the same shape the chat route already
   creates, so the desk sees one kind of attachment either way.

Confirm the exact component property names on Meta's *media upload* guide before
pasting any JSON: this document has already been wrong once about this feature.


### The field **API names** are a contract with this code

They are typed by you into the builder and read by
`IntentRouter._flow_value`. A misspelling does **not** raise — that field simply
arrives empty and the record is raised without it. Use the names below exactly.

| Our field | Also accepted (aliases) | Read by |
|---|---|---|
| `contact_name` | `name` | both forms |
| `contact_email` | `email` | both forms |
| `reg_no` | `registration` | both forms |
| `service` | `service_type` | both forms |
| `preferred_date` | `date` | both forms |
| `preferred_time` | `time` | both forms |
| `damage` | `description`, `details` | request form |
| `vehicle` | `vehicle_model` | request form |
| `notes` | `damage`, `description` | booking form |

The aliases exist so a rename on your side degrades instead of breaking. Prefer
the left-hand column.

---

## 1. The two Flows at a glance

| | **Request form** | **Booking form** |
|---|---|---|
| Flow name in Meta | `Request form` | `Booking form` |
| Screen API name | `ENQUIRY` | `BOOKING` |
| `flow_token` sent by the app | `enquiry` | `booking` |
| Env var for its Flow ID | `WA_FLOW_ENQUIRY_ID` | `WA_FLOW_BOOKING_ID` |
| Menu row it appears as | `Enquiry form` | `Booking form` |
| Asks for | plate, service, damage, day | service, day, time |
| Photographs | asked for afterwards, in chat | not asked for |
| Ends with | "the desk will call you back" | "the desk confirms your appointment" |
| Record raised | Booking, `REQUESTED`, source `whatsapp` | same, with the chosen slot |

Both forms raise the **same kind of record** — there is no separate Enquiry model
in this app; an enquiry *is* a `Booking` with status `REQUESTED`.

---

## 2. Flow 1 — the Request form

### 2.1 Flow settings

| Setting | Value |
|---|---|
| Name | `Request form` |
| Category | `Lead generation` (any category works; nothing reads it) |
| Endpoint URL | **leave empty** — not a data-exchange Flow |
| Screen API name | `ENQUIRY` — must match exactly |

### 2.2 Screen `ENQUIRY` — fields

| # | Label shown to the customer | API name | Component | Required |
|---|---|---|---|---|
| 1 | Your name | `contact_name` | Text input | **yes** |
| 2 | Mobile or email | `contact_email` | Text input | no |
| 3 | Registration number | `reg_no` | Text input | **yes** |
| 4 | Vehicle | `vehicle` | Text input | no |
| 5 | What do you need? | `service` | Dropdown | **yes** |
| 6 | Describe the damage | `damage` | Text area | **yes** |
| 7 | Preferred day | `preferred_date` | Date picker | no |
| 8 | Preferred time | `preferred_time` | Dropdown | no |

Copy to type in, verbatim:

```
Title:        Tell us about your vehicle
Footer label: Send to Topclass
```

Optional helper text for field 6 (`Describe the damage`) — set it as the field's
`helper-text`:

```
The panel, and what happened. Photos come next, in the chat.
```

### 2.3 Dropdown `service` — the options

These are the seven service lines from `app/constants.py → SERVICES`. The answer
is matched against them to pick the rate card, so use these strings **exactly**:

| Option id (builder) | Title (shown) |
|---|---|
| `auto_body` | Auto Body |
| `panel_spray` | Panel Beating & Spray Painting |
| `rebuild` | Rebuilds & Performance Upgrades |
| `detail` | Car Detailing |
| `ceramic` | Ceramic Coating |
| `ppf` | Paint Protection Film |
| `wrap` | Car Vinyl Wrapping |

> A mismatch is survivable — an unrecognised service falls back to *Panel Beating
> & Spray Painting* rather than losing the enquiry — but it silently misprices the
> job, so use the titles above.

### 2.4 Dropdown `preferred_time` — the options

These are the workshop's nine slots (`app/constants.py → BOOKING_SLOTS`):

```
08:00   09:00   10:00   11:00   12:00   13:00   14:00   15:00   16:00
```

`preferred_date` must send an ISO date (`YYYY-MM-DD`), which the date picker does
by default.

---

## 3. Flow 2 — the Booking form

### 3.1 Flow settings

| Setting | Value |
|---|---|
| Name | `Booking form` |
| Category | `Appointment booking` (any category works) |
| Endpoint URL | **leave empty** |
| Screen API name | `BOOKING` — must match exactly |

### 3.2 Screen `BOOKING` — fields

| # | Label shown to the customer | API name | Component | Required |
|---|---|---|---|---|
| 1 | Your name | `contact_name` | Text input | **yes** |
| 2 | Mobile or email | `contact_email` | Text input | no |
| 3 | Registration number | `reg_no` | Text input | **no** |
| 4 | What do you need? | `service` | Dropdown | **yes** |
| 5 | Which day? | `preferred_date` | Date picker | **yes** |
| 6 | What time? | `preferred_time` | Dropdown | **yes** |
| 7 | Anything we should know? | `notes` | Text area | no |

Copy to type in, verbatim:

```
Title:        Book your appointment
Footer label: Request appointment
```

Notes on the deliberate choices:

- **`reg_no` is optional here.** A detailing or ceramic appointment does not need
  a plate. Leave it empty and the booking is raised with **no vehicle** rather
  than a vehicle called "TBC" — that placeholder used to litter the vehicles list.
- **`notes` is not `damage`.** It is free text for "parked under a tree", not a
  damage description, and the confirmation wording follows from that.
- The date picker in the builder can carry a **minimum date**. Set it to today so
  a customer cannot request a slot in the past; a past date is dropped by the
  parser and the desk is told to confirm the day instead.

### 3.3 Dropdowns

`service` and `preferred_time` take **exactly the same options** as section 2.3
and 2.4.

> **A slot can be full.** The dropdown cannot know how many vehicles are already
> booked into a time — only the app can. So the app checks capacity when the form
> lands and, when a slot is already full, tells the customer plainly that the desk
> will offer the nearest alternative. The booking is still recorded; the desk
> decides. Do not hide this by leaving times off the dropdown.

---

## 4. The completion payload — the part people get wrong

A screen that ends the Flow must be marked **terminal**, and its footer's
`on-click-action` must be `complete`. **The keys in that payload are what arrive
as `response_json`** — if a key is missing here, the field is missing everywhere,
however correctly you named it on the component.

Every component's value is referenced as `${form.<api name>}`.

### Request form

```json
{
  "name": "complete",
  "payload": {
    "contact_name": "${form.contact_name}",
    "contact_email": "${form.contact_email}",
    "reg_no": "${form.reg_no}",
    "vehicle": "${form.vehicle}",
    "service": "${form.service}",
    "damage": "${form.damage}",
    "preferred_date": "${form.preferred_date}",
    "preferred_time": "${form.preferred_time}"
  }
}
```

### Booking form

```json
{
  "name": "complete",
  "payload": {
    "contact_name": "${form.contact_name}",
    "contact_email": "${form.contact_email}",
    "reg_no": "${form.reg_no}",
    "service": "${form.service}",
    "preferred_date": "${form.preferred_date}",
    "preferred_time": "${form.preferred_time}",
    "notes": "${form.notes}"
  }
}
```

### A skeleton you can paste into the JSON editor

Both Flows are one screen. Switch the builder to **JSON** and use this as a
starting point. **Keep the `version` the builder generated for you** — Meta
changes it between releases, and the value below is only a placeholder.

```json
{
  "version": "7.0",
  "screens": [
    {
      "id": "BOOKING",
      "title": "Book your appointment",
      "terminal": true,
      "layout": {
        "type": "SingleColumnLayout",
        "children": [
          { "type": "TextInput", "name": "contact_name", "label": "Your name",
            "required": true, "input-type": "text" },
          { "type": "TextInput", "name": "contact_email", "label": "Mobile or email",
            "required": false, "input-type": "text" },
          { "type": "TextInput", "name": "reg_no", "label": "Registration number",
            "required": false, "input-type": "text" },
          { "type": "Dropdown", "name": "service", "label": "What do you need?",
            "required": true,
            "data-source": [
              { "id": "auto_body", "title": "Auto Body" },
              { "id": "panel_spray", "title": "Panel Beating & Spray Painting" },
              { "id": "rebuild", "title": "Rebuilds & Performance Upgrades" },
              { "id": "detail", "title": "Car Detailing" },
              { "id": "ceramic", "title": "Ceramic Coating" },
              { "id": "ppf", "title": "Paint Protection Film" },
              { "id": "wrap", "title": "Car Vinyl Wrapping" }
            ] },
          { "type": "DatePicker", "name": "preferred_date", "label": "Which day?",
            "required": true },
          { "type": "Dropdown", "name": "preferred_time", "label": "What time?",
            "required": true,
            "data-source": [
              { "id": "08:00", "title": "08:00" },
              { "id": "09:00", "title": "09:00" },
              { "id": "10:00", "title": "10:00" },
              { "id": "11:00", "title": "11:00" },
              { "id": "12:00", "title": "12:00" },
              { "id": "13:00", "title": "13:00" },
              { "id": "14:00", "title": "14:00" },
              { "id": "15:00", "title": "15:00" },
              { "id": "16:00", "title": "16:00" }
            ] },
          { "type": "TextArea", "name": "notes",
            "label": "Anything we should know?", "required": false },
          { "type": "Footer", "label": "Request appointment",
            "on-click-action": {
              "name": "complete",
              "payload": {
                "contact_name": "${form.contact_name}",
                "contact_email": "${form.contact_email}",
                "reg_no": "${form.reg_no}",
                "service": "${form.service}",
                "preferred_date": "${form.preferred_date}",
                "preferred_time": "${form.preferred_time}",
                "notes": "${form.notes}"
              }
            } }
        ]
      }
    }
  ]
}
```

For the request form, change `id` to `ENQUIRY`, swap `notes` for `damage`
(TextArea) and add `vehicle` (TextInput), and use the section 4 payload.

---

## 5. What happens when a form is submitted

1. The `nfm_reply` arrives at `/webhooks/whatsapp`. The `flow_token` decides
   which form it was: anything before a colon names it, so `enquiry:2026-10-01`
   is still the request form.
2. **A form with no usable answers is rejected.** The customer is asked to resend
   rather than the desk getting a record reading "reg TBC". This matters because
   Meta retries a delivery that does not get a 200.
3. Customer and vehicle are found or created; the plate is normalised, so
   `adz 4477` becomes `ADZ4477`.
4. Any attachments sent earlier in the chat are attached to the record.
5. The record is raised — `TC-ENQ-…`, status `REQUESTED`, source `whatsapp`.
6. The customer gets the reference, then the form-specific closing line.

Verified end to end, both forms, in `tests/test_whatsapp_bot.py`.

---

## 6. Wiring it up

1. Build the Flow, **publish** it (a draft Flow can only be opened in the test
   view), then copy its **Flow ID**.
2. Set the matching environment variable:

   | Flow | Variable |
   |---|---|
   | Request form | `WA_FLOW_ENQUIRY_ID` |
   | Booking form | `WA_FLOW_BOOKING_ID` |

3. Restart. The menu row appears **only** once its own id is configured — a row
   that opens nothing is worse than no row at all. Until then the row falls back
   to the equivalent chat flow, which does the same job in more messages.

> **The main menu is now at WhatsApp's ten-row ceiling.** Both forms plus the
> eight original rows is exactly ten. Anything else added to
> `main_menu_reply()` has to displace something.

### Offering a form to a customer who has gone quiet

A free-form Flow message can only be sent **inside the 24-hour window**. Past
that, Meta requires an approved template with a **Flow button**:

| Template | Flow button | Screen | Token |
|---|---|---|---|
| `enquiry_form` | *Open form* | `ENQUIRY` | `enquiry` |
| `booking_form` | *Book now* | `BOOKING` | `booking` |

The first is documented as template 12 in `docs/META-SETUP.md`. A companion
`booking_form` template is listed there as an optional extra — build it if you
want to chase a customer who enquired days ago.

### Switching from draft to published

`WhatsAppClient.send_flow` opens the Flow with the screen mode still set to
`draft`, which is what Meta requires while you are building. Once published,
change the mode in `app/services/whatsapp_client.py` (`send_flow`).
