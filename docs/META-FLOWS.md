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
  It asks three things: the service, the vehicle make and model, and photographs
  (optional). The name is not asked — it is already known from the profile.
  On submit, _lead_from_flow() writes the same context and calls the SAME
  _create_lead(), attaching any photographs the Flow carried. If the Flow carried
  none, the chat then invites them; if it carried some, it does not ask again.

RAISED
  Customer + Vehicle found or created        (plate normalised, never "TBC")
  A plate-less enquiry records NO Vehicle — Vehicle.reg_no is NOT NULL, and the
  make and model stand in as the vehicle label until the car arrives.
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
  The form path gets one extra message before the menu — and only when the Flow
  carried no photographs: send photographs of the damage, and any assessor's
  report as a PDF.
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

1. **Meta side** — put a `PhotoPicker` (or a `DocumentPicker`, for the assessor's
   report too) on the form's screen. See section 4 for the exact component.
   > ⚠️ **Leave it out of the `complete` payload.** Referencing the picker there
   > fails validation with two `Flow JSON errors`. See section 4 for the detail and
   > what it means for delivery.
2. **Code side** — the webhook finds any answer holding a **list of objects with
   an `id`** (`_flow_media` in `app/views/whatsapp.py`), downloads each entry from
   `GET /{media-id}` with the access token, and hands them to
   `IntentRouter._attach_flow_media`, which parks them with anything sent in chat
   so both routes produce the same `BookingPhoto`. **Already built and tested.**
   The name of the component does not matter — it is found by shape — and picker
   answers are excluded from the text lookups, so a key reading "Photos of the
   damage" can never be read as a damage description.

⚙️ **Status: code done.** Both routes are covered by tests
(`test_a_form_that_carried_files_does_not_ask_for_them_again`,
`test_a_picker_key_is_never_read_as_the_damage_description`). Whether the picker
delivers without a payload entry is unverified — see section 4.


### The field **API names** do **not** have to be a contract

The answers are resolved by **meaning**, so you can leave the builder's own names
in place. Keys are compared on letters and digits alone with the `screen_<n>_`
prefix dropped, so all of these answer a lookup for the service:

| Key that arrives | Why it is found |
|---|---|
| `service` | exact |
| `service_type` | exact alias |
| `screen_0_What_do_you_need_0` | normalises to `whatdoyouneed0`; matched by hint |
| `What_do_you_need_11da7f` | normalises to `whatdoyouneed11da7f`; matched by hint |

Exact names are tried first, then the hints in `_FLOW_HINTS`
(`app/services/intent_router.py`). Picker answers are skipped entirely — they are
media, not text — and a field nothing matches is a blank on the record, never an
exception.

| Field | Also accepted | Read by |
|---|---|---|
| `service` | `service_type`, anything reading "what do you need" | both forms |
| `vehicle` | `vehicle_model`, anything reading "vehicle make model" | request form |
| *(picker)* | **any name** — found by shape, not by name | request form |
| `contact_email` | `email` | *optional* |
| `notes` | `damage`, `description`, "anything we should know" | booking form |
| `preferred_date` | `date`, "which day" | booking form |
| `preferred_time` | `time`, "what time" | booking form |
| `contact_name` | `name`, "your name" | *optional* — falls back to the WhatsApp profile name |
| `reg_no` | `registration`, "registration number" | *optional* — the plate is taken on arrival |
| `damage` | `description`, `details` | *optional* — folded into the booking notes |

The last three are no longer asked for by either form, but the code still reads
them, so a Flow that carries them keeps working.

**Why this is tolerant rather than strict.** It used to demand our own names
exactly. A Flow that worked perfectly in the builder then arrived with no service
and no vehicle — an enquiry priced against the default card, with nothing on it
saying which car it was about. Nothing raised; it just quietly mispriced the
quotation. Renaming the builder's components to suit the code was the wrong fix,
so the code reads them instead (`test_a_form_named_by_the_meta_builder_still_lands`).

The aliases exist so a rename on your side degrades instead of breaking. Prefer
the left-hand column.

---

## 1. The two Flows at a glance

| | **Request form** | **Booking form** |
|---|---|---|
| Flow name in Meta | `Request form` | `Booking form` |
| Screen API name | `QUESTION_ONE` *(configurable)* | `BOOKING` *(configurable)* |
| Env var for its screen name | `WA_FLOW_ENQUIRY_SCREEN` | `WA_FLOW_BOOKING_SCREEN` |
| `flow_token` sent by the app | `enquiry` | `booking` |
| Env var for its Flow ID | `WA_FLOW_ENQUIRY_ID` | `WA_FLOW_BOOKING_ID` |
| Menu row it appears as | `Enquiry form` | `Booking form` |
| Asks for | service, make & model, photos | service, day, time |
| Name | **from WhatsApp** | **from WhatsApp** |
| Photographs | in the Flow's picker; asked for in chat only if none came | not asked for |
| Ends with | "the desk will call you back" | "the desk confirms your appointment" |
| Record raised | Booking, `REQUESTED`, source `whatsapp` | same, with the chosen slot |

> **The screen name comes from the Flow, so it lives in config, not in code.**
> Meta's builder names the first screen `QUESTION_ONE` and *rejects* a screen name
> the Flow does not define — the form simply will not open. The default matches
> what the builder generates, so a Flow pasted from section 4 works with no `.env`
> change at all. Rename the screen in Meta and you set one variable.

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
| Screen API name | `QUESTION_ONE` — the builder's default. Set `WA_FLOW_ENQUIRY_SCREEN` if you rename it |
| `data_api_version` | **omit** — that is only for Flows with an endpoint |
| `routing_model` | **omit** — one screen, so there is nothing to route between |

### 2.2 Screen `QUESTION_ONE` — fields

The left column is what the customer sees and the middle is what the builder
chose. **None of it needs to match our code**, because the answers are resolved by
meaning — see section 4.

| # | Label shown to the customer | Component `name` | Component | Required |
|---|---|---|---|---|
| 1 | What do you need | `What_do_you_need_11da7f` | Dropdown | **yes** |
| 2 | Vehicle Make, Model | `Vehicle_Make_Model_2e7fab` | Text input | **yes** |
| 3 | Describe the enquiry | `Describe_the_enquiry` | Text area | no |
| 4 | Photos of the damage or vehicle | `Photos_of_the_damage` | Photo picker (or Document picker) | no |

Options for the dropdown are in section 2.3; the picker is section 2.5. There is
**no** name, registration, day, time or email field.

- **No name field.** WhatsApp sends the profile name with every inbound message
  and we keep it on the conversation, so the form does not ask for it — see
  `IntentRouter._wa_name()`. Asking for something the customer has already told
  WhatsApp is one more field to abandon.
- **No registration.** The plate is taken when the vehicle actually arrives.
  > ⚠️ **Consequence:** `Vehicle.reg_no` is `NOT NULL`, so an enquiry with no plate
  > records **no Vehicle at all** — the make and model is the only thing identifying
  > the car. It is written to the booking notes (`Vehicle: Toyota Hilux 2019`) and
  > shown in the customer's confirmation, but a later job card for that customer
  > will not link to an existing vehicle record.
- **The description is back, in the customer's own words.** It sits **above** the
  picker so the customer says what happened first and then shows it. It is read as
  the damage description, so it lands on the booking notes the desk works from.
- **No day and no time.** Those belong on the **booking** form, where holding a
  slot is the entire point. An enquiry is "tell us what you need"; it should not
  hint at a reservation the shop has not agreed.
- **No email.** The quotation is sent to the WhatsApp thread, which is where the
  customer is already.

Any of those fields **would** still be read if a Flow carried one, so an install
running the earlier draft keeps working rather than quietly dropping answers.

Copy to type in, verbatim:

```
Screen title: EQUIRY FORM
Footer label: Continue
```

### 2.3 Dropdown `What_do_you_need_11da7f` — the options

These are the seven service lines from `app/constants.py → SERVICES`, with the
option ids the builder generated. **Leave those ids exactly as they are** — they
are what the dropdown returns, and `match_flow_service()` resolves them back to
service names by comparing letters and digits alone.

> **Leave the builder's ids alone.** A Dropdown returns the **id**, not the
> title, and our parser matches the answer against the service names — so a
> code-style id like `auto_body` silently misprices the job: nothing in the
> synonym table matches "auto", so it falls through to *Panel Beating & Spray
> Painting*. `match_flow_service()` compares on **letters and digits alone** and
> strips the leading index, so both of the builder's own forms resolve:
>
> | Option id | Resolves to |
> |---|---|
> | `0_Autobody` | Auto Body |
> | `1_Panel_Beating_&_Spray_Painting` | Panel Beating & Spray Painting |
> | `3_Car_Detailing` | Car Detailing |
>
> If you hand-write a JSON, `id` = `title` (`"Auto Body"` / `"Auto Body"`) is
> equally safe and reads better in the builder's dropdown.

| Titles, in the builder's order |
|---|
| `Autobody` |
| `Panel Beating & Spray Painting` |
| `Rebuilds & Performance Upgrades` |
| `Car Detailing` |
| `Ceramic Coating` |
| `Paint Protection Film` |
| `Car Vinyl Wrapping` |

An unrecognised service still raises the enquiry against the default service
rather than being lost — but it misprices the job, so use these strings exactly.

### 2.4 There is no `preferred_time` on this form

The nine workshop slots (`app/constants.py → BOOKING_SLOTS`: `08:00` through
`16:00`) are offered on the **booking** form only. The enquiry form has no day and
no time.

### 2.5 The file field — `DocumentPicker` or `PhotoPicker`, never both

**One media component per screen.** Meta rejects a screen carrying both a
`PhotoPicker` and a `DocumentPicker`: *"You can only have a maximum of 1
component of type PhotoPicker or DocumentPicker per screen."*

Use a **single `DocumentPicker`** if you want the damage pictures *and* an
assessor's PDF: `allowed-mime-types` is what lets the customer pick from their
gallery, and including `image/jpeg` is what enables photo picking.

```json
{
  "type": "DocumentPicker",
  "name": "Photos_of_the_damage",
  "label": "Photos of the damage or vehicle",
  "description": "A picture of the whole panel and a close-up. An assessor's report or existing quotation can go here as a PDF too.",
  "max-file-size-kb": 25600,
  "min-uploaded-documents": 0,
  "max-uploaded-documents": 10,
  "allowed-mime-types": ["image/jpeg", "image/png", "application/pdf"]
}
```

| Property | Notes |
|---|---|
| `type` | `DocumentPicker` (or `PhotoPicker` for photos only, camera included) |
| `name` | the component's name on the screen — **not** the key in `response_json`. Our code does not read it by name; it finds media by shape. Keep the same value as the `PhotoPicker` it replaces. |
| `label` | max 80 chars |
| `description` | max 300 chars |
| `max-file-size-kb` | default 25600 (25 MiB); range 1–25600 |
| `min-uploaded-documents` | 0 makes it optional, >0 makes it required |
| `max-uploaded-documents` | range 1–30 — **but a response message carries at most 10 files, totalling 100 MiB**, so cap it at 10 |
| `allowed-mime-types` | include `image/jpeg` to allow gallery photos; `application/pdf` for the report |

The `PhotoPicker` alternative adds `photo-source` (`camera_gallery` default,
`camera`, or `gallery`) and uses `min-uploaded-photos` /
`max-uploaded-photos` instead — but it takes **photos only**, no PDFs.

**It must sit at the top level of the `complete` payload.** Nesting it is an
error, and so is putting it in a `navigate` payload:

```json
"on-click-action": {
  "name": "complete",
  "payload": {
    "attachment": "${form.attachment}"
  }
}
```

It also cannot be pre-filled (`init-values` is rejected for both pickers).

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

| # | Label shown to the customer | API name | Component | Required | Notes |
|---|---|---|---|---|---|
| 1 | What do you need? | `service` | Dropdown | **yes** | options in section 2.3 |
| 2 | Which day? | `preferred_date` | Date picker | **yes** | `min-date` = today |
| 3 | What time? | `preferred_time` | Dropdown | **yes** | the nine slots |
| 4 | Registration number | `reg_no` | Text input | no | `helper-text`: `Optional` |
| 5 | Anything we should know? | `notes` | Text area | no | |
| 6 | Email for the confirmation | `contact_email` | Text input | no | `input-type`: `email` |

**This is where a day and a time belong** — holding a slot is the entire point of
the booking form, and both are required here.

**Still no name field.** Same reason as the enquiry form: the WhatsApp profile
name is already on the conversation, and `_wa_name()` uses it.

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
- **Set the date picker's minimum to today**, so a customer cannot request a slot
  in the past. A past date is dropped by the parser and the desk is told to
  confirm the day instead.

### 3.3 Dropdowns

`service` takes **exactly the same options** as section 2.3 — including the rule
about `id` and `title` being the same string.

`preferred_time` offers the workshop's nine slots (`app/constants.py →
BOOKING_SLOTS`), and here **`id` and `title` are naturally identical**:

| id **and** title |
|---|
| `08:00` · `09:00` · `10:00` · `11:00` · `12:00` · `13:00` · `14:00` · `15:00` · `16:00` |

> **Cap it at these nine.** The value is stored straight onto the booking as its
> slot with no validation, so a form offering `17:30` would record a time the shop
> does not work. Capacity (2 vehicles a slot) is checked by the code, not by the
> dropdown — a full slot is accepted and the customer is told the desk will offer
> the nearest alternative.

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
    "screen_0_What_do_you_need_0": "${form.What_do_you_need_11da7f}",
    "screen_0_Vehicle_Make_Model_1": "${form.Vehicle_Make_Model_2e7fab}",
    "screen_0_Describe_the_enquiry_2": "${form.Describe_the_enquiry}"
  }
}
```

**The three typed answers, and only those.** No `contact_name` (it comes from
WhatsApp), no `reg_no` (taken on arrival), no day or time (the booking form's job)
— and **not the picker**, which is the one that trips people up. Referencing
`${form.Photos_of_the_damage}` here fails validation; see the note in section 4.

### Booking form

```json
{
  "name": "complete",
  "payload": {
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

Both Flows are one screen. Switch the builder to **JSON** and use the blocks
below as-is. **Keep the `version` the builder generated for you** — Meta changes
it between releases, and `7.3` is only what these were authored against.

Nothing here needs a data endpoint: no `data_api_version`, no `routing_model`,
Endpoint URL empty.

#### Flow 1 — Request form

**This is the exact JSON Meta accepted, verified in the builder on 1 Oct 2026.**
Do not "improve" it: the shape below is what validates, and two plausible-looking
variants do **not** (see the note after it).

```json
{
  "screens": [
    {
      "data": {},
      "id": "QUESTION_ONE",
      "layout": {
        "children": [
          {
            "children": [
              {
                "type": "TextBody",
                "text": "Kindly provide the details required below for your enquiry"
              },
              {
                "data-source": [
                  {
                    "id": "0_Autobody",
                    "title": "Autobody"
                  },
                  {
                    "id": "1_Panel_Beating_&_Spray_Painting",
                    "title": "Panel Beating & Spray Painting"
                  },
                  {
                    "id": "2_Rebuilds_&_Performance_Upgrades",
                    "title": "Rebuilds & Performance Upgrades"
                  },
                  {
                    "id": "3_Car_Detailing",
                    "title": "Car Detailing"
                  },
                  {
                    "id": "4_Ceramic_Coating",
                    "title": "Ceramic Coating"
                  },
                  {
                    "id": "5_Paint_Protection_Film",
                    "title": "Paint Protection Film"
                  },
                  {
                    "id": "6_Car_Vinyl_Wrapping",
                    "title": "Car Vinyl Wrapping"
                  }
                ],
                "label": "What do you need",
                "name": "What_do_you_need_11da7f",
                "required": true,
                "type": "Dropdown"
              },
              {
                "input-type": "text",
                "label": "Vehicle Make, Model",
                "name": "Vehicle_Make_Model_2e7fab",
                "required": true,
                "type": "TextInput",
                "helper-text": "Kindly provide the vehicle make and model"
              },
              {
                "type": "TextArea",
                "name": "Describe_the_enquiry",
                "label": "Describe the enquiry",
                "required": false,
                "helper-text": "Tell us briefly what has happened or what you need"
              },
              {
                "type": "PhotoPicker",
                "name": "Photos_of_the_damage",
                "label": "Photos of the damage or vehicle",
                "description": "A picture of the whole panel and one close-up. You can pick from your gallery.",
                "photo-source": "camera_gallery",
                "min-uploaded-photos": 0,
                "max-uploaded-photos": 8,
                "max-file-size-kb": 25600
              },
              {
                "label": "Continue",
                "on-click-action": {
                  "name": "complete",
                  "payload": {
                    "screen_0_What_do_you_need_0": "${form.What_do_you_need_11da7f}",
                    "screen_0_Vehicle_Make_Model_1": "${form.Vehicle_Make_Model_2e7fab}",
                    "screen_0_Describe_the_enquiry_2": "${form.Describe_the_enquiry}"
                  }
                },
                "type": "Footer"
              }
            ],
            "name": "flow_path",
            "type": "Form"
          }
        ],
        "type": "SingleColumnLayout"
      },
      "terminal": true,
      "title": "EQUIRY FORM"
    }
  ],
  "version": "7.3"
}
```

### ⚠️ Two things that look wrong but are right, and one that is not

**1. The `Form` wrapper stays, and the picker lives *inside* it.** This looks like
it should break the rule that a `Form`'s children are "Form components" — but Meta
accepts it. Removing the wrapper is unnecessary; do not do it.

**2. The picker is deliberately absent from the `complete` payload.** Adding
`"screen_0_Photos_of_the_damage_2": "${form.Photos_of_the_damage}"` produces
**two `Flow JSON errors`** — one against the picker, one against the footer that
follows it, which is why the builder underlines the payload block. Meta's docs
suggest a picker *may* be referenced in a `complete` payload; in this Flow, on this
version, it is rejected. Omitting it is the shape that validates.

> ⚠️ **Consequence, and it matters.** The completion payload is what becomes
> `response_json`, so a picker that is not referenced there may never reach us —
> the customer would attach photos and the desk would see none. Our side handles
> **both** outcomes and needs no change either way (`_flow_media` in
> `app/views/whatsapp.py` finds media by *shape*, under any key):
>
> - photos arrive → they are downloaded and attached, and the bot does **not**
>   re-ask for them (`test_a_form_that_carried_files_does_not_ask_for_them_again`);
> - photos do not arrive → the bot asks for them in chat, exactly as it does today
>   (`test_a_form_with_no_files_still_asks_for_them`).
>
> **Test it once from a real phone before the demo:** submit the form with one
> photo and watch the thread. Either the enquiry lands with the photo attached, or
> the bot asks for photos — both are correct, but you want to know which you have.

**3. The title says `EQUIRY FORM`.** That is a typo for `ENQUIRY` and it is the
heading the customer reads at the top of the form. It is harmless to our code —
nothing reads it — so it is your call. Worth fixing before a client sees it.

Note that the payload key and the component's `name` are **different strings** —
`screen_0_What_do_you_need_0` versus `What_do_you_need_11da7f`. That is how the
builder works and our reader handles it either way:

- the **answer** is looked up tolerantly. Keys are compared on letters and digits
  alone with the `screen_<n>_` prefix dropped, so `screen_0_What_do_you_need_0`
  answers a lookup for the service and `screen_0_Vehicle_Make_Model_1` answers one
  for the vehicle, whatever the builder happened to call them. Exact names are
  still tried first.
- the **picker** is found by *shape*, not by name — any answer holding a list of
  objects with an `id` is media, downloaded and attached. It is also excluded from
  the text lookups, so a key reading "Photos of the damage" can never be mistaken
  for a damage description.

To accept an assessor's PDF as well, swap that component for the
`DocumentPicker` in section 2.5. Keep the `name` as it is. It is one **or** the
other, never both. Expect the same "not in the payload" behaviour.

#### Flow 2 — Booking form

A new Flow, so there is no builder output to preserve — this one **is** written by
hand, and uses our own field names. That is equally fine: the reader tries exact
names first. Note that it has no picker; an appointment is not a damage report.

```json
{
  "version": "7.3",
  "screens": [
    {
      "id": "BOOKING",
      "title": "BOOKING FORM",
      "terminal": true,
      "layout": {
        "type": "SingleColumnLayout",
        "children": [
          {
            "type": "Dropdown",
            "name": "service",
            "label": "What do you need",
            "required": true,
            "data-source": [
              { "id": "Auto Body", "title": "Auto Body" },
              { "id": "Panel Beating & Spray Painting", "title": "Panel Beating & Spray Painting" },
              { "id": "Rebuilds & Performance Upgrades", "title": "Rebuilds & Performance Upgrades" },
              { "id": "Car Detailing", "title": "Car Detailing" },
              { "id": "Ceramic Coating", "title": "Ceramic Coating" },
              { "id": "Paint Protection Film", "title": "Paint Protection Film" },
              { "id": "Car Vinyl Wrapping", "title": "Car Vinyl Wrapping" }
            ]
          },
          {
            "type": "DatePicker",
            "name": "preferred_date",
            "label": "Which day?",
            "required": true
          },
          {
            "type": "Dropdown",
            "name": "preferred_time",
            "label": "What time?",
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
            ]
          },
          {
            "type": "TextArea",
            "name": "notes",
            "label": "Anything we should know?",
            "required": false,
            "helper-text": "Optional"
          },
          {
            "type": "TextInput",
            "name": "reg_no",
            "label": "Registration number",
            "required": false,
            "input-type": "text",
            "helper-text": "Optional"
          },
          {
            "type": "TextInput",
            "name": "contact_email",
            "label": "Email for the confirmation",
            "required": false,
            "input-type": "email",
            "helper-text": "Optional"
          },
          {
            "type": "Footer",
            "label": "Request appointment",
            "on-click-action": {
              "name": "complete",
              "payload": {
                "contact_email": "${form.contact_email}",
                "reg_no": "${form.reg_no}",
                "service": "${form.service}",
                "preferred_date": "${form.preferred_date}",
                "preferred_time": "${form.preferred_time}",
                "notes": "${form.notes}"
              }
            }
          }
        ]
      }
    }
  ]
}
```

**Do not add `"init-values"` to a picker** — Meta rejects it. `min-date` on the
date picker needs a data endpoint to express "today", so it is left off; a past
date typed anyway is dropped by the parser and the desk is told to confirm the
day instead (section 3.3).

---

## 5. What happens when a form is submitted

1. The `nfm_reply` arrives at `/webhooks/whatsapp`. The `flow_token` decides
   which form it was: anything before a colon names it, so `enquiry:2026-10-01`
   is still the request form.
2. **A form whose answers are all blank is rejected.** The customer is asked to
   resend rather than the desk getting a hollow record for every delivery Meta
   retries. (Meta retries a delivery that does not get a 200.)
3. **The Flow's own media is downloaded first**, from the picker entries, and
   parked with anything the customer already sent in chat — one list, capped at
   8. See `_flow_media()` in `app/views/whatsapp.py`.
4. Customer and vehicle are found or created. **No plate is asked for any more**, so
   a plate-less enquiry records **no Vehicle** (`Vehicle.reg_no` is `NOT NULL`) and
   the make and model is the vehicle label. If a Flow does carry a plate it is
   normalised, so `adz 4477` becomes `ADZ4477`.
5. **The customer's name comes from the WhatsApp profile name** — neither form asks
   for it (`IntentRouter._wa_name()`), and it falls back to the number so a record
   is never written with a blank name.
6. The record is raised — `TC-ENQ-…`, status `REQUESTED`, source `whatsapp` — with
   every attachment in hand.
7. The customer gets the reference, then the form-specific closing line. The
   request form adds the "send us photographs" invitation **only if the Flow
   carried none** — a customer who already sent pictures is not asked twice.

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

3. Only if you renamed the screen in Meta, set its **API name** too — the
   defaults are what the builder calls the first screen of each form, so a Flow
   pasted from section 4 needs nothing here:

   | Flow | Variable | Default |
   |---|---|---|
   | Request form | `WA_FLOW_ENQUIRY_SCREEN` | `QUESTION_ONE` |
   | Booking form | `WA_FLOW_BOOKING_SCREEN` | `BOOKING` |

4. Restart. The menu row appears **only** once its own id is configured — a row
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
| `enquiry_form` | *Open form* | `QUESTION_ONE` | `enquiry` |
| `booking_form` | *Book now* | `BOOKING` | `booking` |

The screen column must match the Flow exactly, and both are configurable
(`WA_FLOW_ENQUIRY_SCREEN` / `WA_FLOW_BOOKING_SCREEN`) because a template's Flow
button carries its own copy of the screen name.

The first is documented as template 12 in `docs/META-SETUP.md`. A companion
`booking_form` template is listed there as an optional extra — build it if you
want to chase a customer who enquired days ago.

### Switching from draft to published

`WhatsAppClient.send_flow` opens the Flow with the screen mode still set to
`draft`, which is what Meta requires while you are building. Once published,
change the mode in `app/services/whatsapp_client.py` (`send_flow`).
