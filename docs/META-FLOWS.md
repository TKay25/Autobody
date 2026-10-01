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

## 0. Read this first: two hard constraints

### A Flow cannot upload a file

There is **no file-upload component** in WhatsApp Flows. This is a platform
limitation, not a gap in this app — ConnectLink has the same constraint. So:

1. The customer fills in the **form** — plate, service, description, day.
2. The bot then **asks for the photographs in the chat**, and anything they send
   is attached to the enquiry automatically.

Step 2 needs nothing from you. It keeps the **last 8 attachments** and accepts
images and PDFs together. A customer who sends 3 damage photos plus an assessor's
PDF gets all 4 attached to the enquiry, with their own filenames kept.

The **booking** form does *not* ask for photos — an appointment is not a damage
report, and the confirmation must not read like one.

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
