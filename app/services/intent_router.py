"""Conversational engine for the Topclass WhatsApp bot.

A deterministic, menu-first state machine (with keyword/NLP fallback) rather than
a free-form LLM. Reasons:

* predictable answers for a workshop where wrong info costs money;
* works with the fast-path button/list replies Meta prefers;
* fully testable without a network.

The bot's working memory lives in ``WaConversation.context`` (a JSON blob) so the
conversation survives restarts and can be inspected in the web inbox.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal

from flask import current_app

from ..constants import (
    BOOKING_SLOT_CAPACITY,
    BOOKING_SLOTS,
    SERVICES,
    SERVICE_BY_CODE,
    SERVICE_NAMES,
    STAGE_CUSTOMER_TEXT,
    STAGE_LABELS,
    STAGE_PROGRESS,
)
from ..extensions import db
from ..models import (Booking, BookingPhoto, Customer, Estimate, Invoice, JobCard, JobPhoto,
                      Payment, PaymentProof, Task, Vehicle, WaConversation, utcnow)
from .notifications import public_url
from .pricing import quick_quote

# ── tiny i18n table (English / Shona / Ndebele) ──────────────────────────────
LANGUAGES = {
    "en": "English",
    "sn": "Shona",
    "nd": "Ndebele",
}

# Ways a customer might ask for a language, or name one, in any of the three.
# Used for the free-text switch, so a customer can change language by simply
# writing the name of it — no menu required.
LANGUAGE_NAMES = {
    "en": {"english", "eng", "en", "chirungu", "isilungu"},
    "sn": {"shona", "chishona", "sn", "sh"},
    "nd": {"ndebele", "isindebele", "nd"},
}
LANGUAGE_WORDS = {
    "language", "languages", "lang", "mutauro", "mitauro",
    "ulimi", "izilimi", "translate",
}
LANGUAGE_PHRASES = {
    "change language", "switch language", "select language", "choose language",
    "shandura mutauro", "shintsha ulimi", "khetha ulimi", "sarudza mutauro",
}

# Words that give away which language somebody is writing in. Two or more are
# required before the bot switches on its own, so a single borrowed word ("mari"
# in an otherwise English sentence) does not flip the conversation.
LANGUAGE_MARKERS = {
    "sn": {"ndapota", "mhoro", "mangwanani", "masikati", "manheru", "ndinoda",
           "ndiri", "mota", "mari", "zvakanaka", "ndatenda", "tinotenda",
           "nhamba", "chaizvo", "unogona", "ndingakubatsira", "pano", "sei"},
    "nd": {"ngicela", "sawubona", "ngiyabonga", "siyabonga", "ngifuna", "imoto",
           "izimoto", "kanjani", "kuhle", "yebo", "lutho", "futhi", "kumbe",
           "manje", "ngingakusiza", "lapha"},
}

# States that are waiting for an answer. Auto-detection stays out of these so it
# cannot swallow the registration number or the damage description the customer
# just typed; an explicit request ("Shona") still works from any of them.
DATA_ENTRY_STATES = {
    "QUOTE_REG", "QUOTE_SERVICE", "QUOTE_DESC", "QUOTE_CONTACT", "QUOTE_EMAIL",
    "TRACK_REF", "BOOK_SERVICE", "BOOK_DATE", "BOOK_TIME", "BOOK_CONTACT",
    "PAYMENT_PROOF", "WARRANTY_CLAIM",
}

T = {
    "welcome": {
        "en": "Hello {name}, welcome to {company}.\n\n"
              "Panel beating, spray painting, detailing, ceramic coating, "
              "paint protection film and vinyl wrapping.\n\n"
              "How can we help?",
        "sn": "Mhoro {name}, takugamuchirai ku{company}.\n\n"
              "Tinogadzira mota: panel beating, kupenda, kuchenesa, ceramic "
              "coating nePPF.\n\nTingakubatsirai sei?",
        "nd": "Sawubona {name}, siyakwamukela ku{company}.\n\n"
              "Silungisa izimoto: panel beating, ukupenda, ukuhlanza, ceramic "
              "coating nePPF.\n\nSingakusiza njani?",
    },
    "menu_prompt": {
        "en": "Please choose an option below.",
        "sn": "Sarudzai chimwe chezvinotevera.",
        "nd": "Khetha okunye kwalokhu okulandelayo.",
    },
    "ask_reg": {
        "en": "Sure. What is the vehicle registration number? (e.g. ABC 1234)",
        "sn": "Zvakanaka. Nderipi nhamba yemota? (semuenzaniso ABC 1234)",
        "nd": "Kulungile. Yini inombolo yemota? (isb. ABC 1234)",
    },
    "ask_service": {
        "en": "Which service do you need?",
        "sn": "Ndeipi sevhisi yamunoda?",
        "nd": "Yiphi insiza oyidingayo?",
    },
    "ask_desc": {
        "en": "Please describe the damage. Send photos, and a PDF of any "
              "assessment you already have.",
        "sn": "Tsanangurai kukuvara kwemota. Tumirai mifananidzo, uye PDF "
              "yearhenti yamunayo.",
        "nd": "Chaza umonakalo. Thumela izithombe, kanye ne-PDF yohlelo "
              "lwenhlolovo onalo.",
    },
    "ask_ref": {
        "en": "Please send your job number (e.g. TC-2026-0007) or vehicle registration.",
        "sn": "Tumirai nhamba yejobi (semuenzaniso TC-2026-0007) kana nhamba yemota.",
        "nd": "Thumela inombolo yomsebenzi (isb. TC-2026-0007) kumbe inombolo yemota.",
    },
    "not_found": {
        "en": "I could not find anything with that reference. Please check and "
              "try again, or type *menu*.",
        "sn": "Handina kuwana chinhu neiyo nhamba. Edzai zvakare, kana kunyora *menu*.",
        "nd": "Angitholanga lutho ngaleyo inombolo. Zama futhi, kumbe bhala *menu*.",
    },
    "invalid_choice": {
        "en": "Sorry, I did not understand that. Please choose from the menu.",
        "sn": "Ndineurombo, handina kunzwisisa. Sarudzai kubva pamenu.",
        "nd": "Uxolo, angizwisisanga. Khetha kusuka kumenyu.",
    },
    "handoff": {
        "en": "No problem — I have asked one of our team to take over. Someone will reply shortly during business hours ({hours}).",
        "sn": "Hapana dambudziko — ndakumbira mumwe wechikwata kuti atevere. Achapindura munguva dzebasa ({hours}).",
        "nd": "Akunankinga — ngicele omunye wethimba ukuthi athathe. Uzaphendula ngesikhathi somsebenzi ({hours}).",
    },
    "bye": {
        "en": "Thank you for choosing {company}. Drive safely.",
        "sn": "Tinotenda nekusarudza {company}. Fambai zvakanaka.",
        "nd": "Siyabonga ngokukhetha {company}. Hamba kuhle.",
    },
    "lang_hint": {
        "en": "Reply Shona or Ndebele to change language.",
        "sn": "Pindurai English kana Ndebele kushandura mutauro.",
        "nd": "Phendula English kumbe Shona ukushintsha ulimi.",
    },
    "lang_set": {
        "en": "Language set to English.",
        "sn": "Mutauro washandurwa kuShona.",
        "nd": "Ulimi lushintshiwe lwaba isiNdebele.",
    },
    "lang_unknown": {
        "en": "Please choose English, Shona or Ndebele.",
        "sn": "Sarudzai English, Shona kana Ndebele.",
        "nd": "Khetha English, Shona kumbe Ndebele.",
    },
    "ask_name": {
        "en": "Thanks. Last thing — what name should we put on the job card? "
              "Reply with your name (or type *skip* if you are already a customer).",
        "sn": "Ndatenda. Chekupedzisira — nderipi zita rinouya pajob kadi? "
              "Pindurai nezita renyu (kana kunyora *skip* kana muri mutengi wedu).",
        "nd": "Ngiyabonga. Okokugcina — yiliphi ibizo elizafakwa kukadi lomsebenzi? "
              "Phendula ngebizo lakho (kumbe bhala *skip* uma usuvele ungumthengi).",
    },
    "ask_email": {
        "en": "Thanks {name}. And an email address for the quotation? "
              "(or type *skip*)",
        "sn": "Ndatenda {name}. Uye kero yeemail yequotation? (kana kunyora *skip*)",
        "nd": "Ngiyabonga {name}. Lakhe ikheli le-imeyili lesiphakamiso? "
              "(kumbe bhala *skip*)",
    },
    "more_prompt": {
        "en": "Anything else I can help with?",
        "sn": "Pane zvimwe zvandingakubatsira nazvo?",
        "nd": "Kukhona okunye engingakusiza ngakho?",
    },
}


def t(key: str, lang: str, **kwargs) -> str:
    table = T.get(key, {})
    text = table.get(lang) or table.get("en", "")
    return text.format(**kwargs)


# ── intent detection ─────────────────────────────────────────────────────────
INTENT_PATTERNS = [
    ("book", r"\b(book|booking|appointment|slot|schedule|bhuka)\b"),
    ("track", r"\b(track|status|progress|where is|how far|ready|collection|kupi)\b"),
    ("quote", r"\b(quote|quotation|estimate|price|cost|how much|charge|mari)\b"),
    ("hours", r"\b(hours|open|opening|closing|time|what time)\b"),
    ("location", r"\b(where|location|address|directions|find you|kupi)\b"),
    ("human", r"\b(human|agent|person|talk to|speak to|manager|call me|phone)\b"),
    ("services", r"\b(services|what do you do|offerings|menu of)\b"),
    ("warranty", r"\b(warranty|guarantee|guarantee period)\b"),
    ("menu", r"^(menu|main menu|start|hello|hi|hey|hie|mhoro|sawubona|good (morning|afternoon|day))[\s!.,]*$"),
    ("stop", r"\b(stop|unsubscribe|opt out)\b"),
]


def detect_intent(text: str) -> str | None:
    low = (text or "").strip().lower()
    if not low:
        return None
    for intent, pattern in INTENT_PATTERNS:
        if re.search(pattern, low):
            return intent
    return None


def match_service(text: str) -> str | None:
    """Fuzzy-match a free-text service description to a service line."""
    low = (text or "").strip().lower()
    if not low:
        return None
    for name in SERVICE_NAMES:
        if name.lower() in low:
            return name
    synonyms = {
        "panel": "Panel Beating & Spray Painting",
        "spray": "Panel Beating & Spray Painting",
        "paint": "Panel Beating & Spray Painting",
        "dent": "Panel Beating & Spray Painting",
        "scratch": "Panel Beating & Spray Painting",
        "bumper": "Panel Beating & Spray Painting",
        "accident": "Panel Beating & Spray Painting",
        "crash": "Panel Beating & Spray Painting",
        "detail": "Car Detailing",
        "valet": "Car Detailing",
        "wash": "Car Detailing",
        "ceramic": "Ceramic Coating",
        "coating": "Ceramic Coating",
        "ppf": "Paint Protection Film",
        "film": "Paint Protection Film",
        "wrap": "Car Vinyl Wrapping",
        "vinyl": "Car Vinyl Wrapping",
        "rebuild": "Rebuilds & Performance Upgrades",
        "engine": "Rebuilds & Performance Upgrades",
        "upgrade": "Rebuilds & Performance Upgrades",
    }
    for keyword, service in synonyms.items():
        if keyword in low:
            return service
    return None


    return None


REG_PATTERN = re.compile(r"\b([A-Z]{2,3}[ -]?\d{2,5}[A-Z]?)\b", re.I)
JOB_PATTERN = re.compile(r"\b(TC-\d{4}-\d{3,5})\b", re.I)
# Deliberately loose. An address that fails a strict RFC regex is still worth
# keeping — the front desk can correct it, but a missed one is a lead we cannot
# send a quotation to.
EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


# ── reply builders ───────────────────────────────────────────────────────────
def text(body: str, **kw) -> dict:
    return {"type": "text", "body": body, **kw}


def buttons(body: str, options: list[tuple[str, str]], header: str | None = None) -> dict:
    return {
        "type": "buttons",
        "body": body,
        "header": header,
        "buttons": [{"id": oid, "title": title} for oid, title in options],
    }


def main_menu_reply(company: str, lang: str, prefix: str = "") -> dict:
    """The greeting menu.

    A list rather than buttons. WhatsApp caps buttons at three, and that cap is
    exactly what kept "Book a service" off the greeting — the flow existed, but
    customers could only find it by typing *book*. A list shows everything.
    """
    body = t("menu_prompt", lang)
    if prefix:
        body = prefix + "\n\n" + body
    rows = [
        {"id": "m_quote", "title": "Get a quote",
         "description": "Send photos, get a price"},
        {"id": "m_book", "title": "Book a service",
         "description": "Pick a day and a time"},
        {"id": "m_track", "title": "Track my repair",
         "description": "Job number or registration"},
        {"id": "m_pay", "title": "I have paid",
         "description": "Send a receipt or EcoCash proof"},
        {"id": "m_services", "title": "Our services & prices"},
        {"id": "m_human", "title": "Talk to a person"},
        {"id": "m_lang", "title": "Language"},
        {"id": "m_info", "title": "Contact details"},
    ]
    # Only offered once a Flow has actually been built and its id configured.
    # Tapping a row that opens nothing is worse than the row not being there.
    try:
        form_id = (current_app.config or {}).get("WA_FLOW_ENQUIRY_ID") or ""
    except RuntimeError:      # no app context, e.g. a text-only unit test
        form_id = ""
    if form_id:
        rows.insert(1, {"id": "m_form", "title": "Enquiry form",
                        "description": "Fill it in, we call you back"})

    return {
        "type": "list",
        "body": body,
        # A list footer rather than a body line: the hint is an aside, and the
        # body is what the customer has to read to choose.
        "footer": t("lang_hint", lang),
        "button": "Start",
        "sections": [{"title": company, "rows": rows}],
    }


def service_list_reply(lang: str, id_prefix: str = "svc") -> dict:
    """The service picker — a list, never a "reply with a number".

    ``id_prefix`` exists because the booking flow needs its own ids: the quote
    flow claimed ``svc:``, so tapping a service while booking was routed into the
    quote flow instead and a booking could never actually be completed by tap.
    """
    rows = []
    for service in SERVICES:
        full = service["name"]
        short = service.get("short") or full
        quote = quick_quote(full)
        price = f"from USD {quote['from_price']:.0f}" if quote else ""
        # The full name goes in the description when the title had to be shortened
        # to fit WhatsApp's 24-character row title. A plain hyphen rather than a
        # middot: a middot is easy to lose in an edit and renders inconsistently.
        if short != full and price:
            description = f"{full} - {price}"
        else:
            description = price
        row = {"id": f"{id_prefix}:{full}", "title": short[:24]}
        if description:
            row["description"] = description[:72]
        rows.append(row)
    return {
        "type": "list",
        "body": t("ask_service", lang),
        "button": "Choose service",
        "sections": [{"title": "Our services", "rows": rows}],
    }


def more_menu_reply(lang: str = "en") -> dict:
    """The catch-all "what next?" menu.

    A list rather than buttons, because WhatsApp caps buttons at three — which is
    exactly how the language option ended up unreachable: ``m_lang`` was wired up
    in the router but no menu ever offered it.
    """
    return {
        "type": "list",
        "menu": "more",                  # re-rendered per language in handle()
        "body": t("more_prompt", lang),
        "footer": t("lang_hint", lang),
        "button": "Options",
        "sections": [{"title": "What next?", "rows": [
            {"id": "m_quote", "title": "Get a quote"},
            {"id": "m_track", "title": "Track my repair"},
            {"id": "m_book", "title": "Book a service"},
            {"id": "m_pay", "title": "I have paid"},
            {"id": "m_services", "title": "Our services"},
            {"id": "m_warranty", "title": "Warranty"},
            {"id": "m_human", "title": "Talk to a person"},
            {"id": "m_lang", "title": "Language"},
            {"id": "m_info", "title": "Contact details"},
        ]}],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Router
# ─────────────────────────────────────────────────────────────────────────────
class IntentRouter:
    """Processes one inbound message and returns the replies to send."""

    FALLBACK_LIMIT = 2
    # A customer attaching a 60-file album must not bloat the context column or
    # bury the desk in noise. The last few are the ones that matter.
    MAX_PENDING_MEDIA = 8

    def __init__(self, conversation: WaConversation, app=None):
        self.conv = conversation
        self.app = app or current_app
        self.cfg = self.app.config
        self.lang = conversation.ctx_get("lang", "en")
        self.company = self.cfg["COMPANY_NAME"]
        self.replies: list[dict] = []
        # Cleared by _fallback so repeated confusion escalates to a human.
        self.reset_strikes = True

    # ── entry point ──────────────────────────────────────────────────────
    def handle(self, *, text_body: str | None = None, interactive_id: str | None = None,
               media_url: str | None = None, media_name: str | None = None,
               flow_response: dict | None = None) -> list[dict]:
        raw = (text_body or "").strip()
        choice = (interactive_id or "").strip()

        if self.conv.human_takeover:
            # A human is handling this thread; the bot stays silent.
            return []

        if flow_response is not None:
            # A completed Flow. Checked first: it arrives as an interactive
            # message but carries no button id, and the answers must not be read
            # as free text and matched against a state handler.
            replies = self._handle_flow(flow_response)
        elif media_url:
            # ``raw`` is the caption the customer typed with the attachment — the
            # media branch runs ahead of the state handlers, so it used to be
            # discarded and the customer's own words about the damage were lost.
            replies = self._handle_media(media_url, media_name, raw)
        elif choice:
            replies = self._handle_choice(choice)
        else:
            replies = self._handle_text(raw)

        if replies and self.reset_strikes:
            self.conv.ctx_set(strikes=0)
            db.session.commit()

        # The catch-all menu comes from a module-level helper used at a dozen call
        # sites, so its prompt is filled in here rather than threading the current
        # language through every one of them.
        return [
            more_menu_reply(self.lang) if r.get("menu") == "more" else r
            for r in replies
        ]

    # ── media (photos, PDFs and any other attachment) ────────────────────
    @staticmethod
    def _media_kind(url: str, name: str | None = None) -> str:
        """DAMAGE for a picture, DOCUMENT for anything else (a PDF report)."""
        probe = (name or url or "").lower()
        return "DAMAGE" if re.search(r"\.(png|jpe?g|webp|gif)(\?|$)", probe) else "DOCUMENT"

    def _pending_media(self) -> list[dict]:
        """The attachments collected so far, as ``{url, name, kind, caption}``.

        Normalised on read: a conversation that was mid-flow before attachments
        were named holds bare URL strings, and must keep working.
        """
        out = []
        for item in self.conv.ctx_get("pending_media") or []:
            if isinstance(item, dict):
                out.append(item)
            else:
                out.append({"url": item, "name": "",
                            "kind": self._media_kind(item), "caption": ""})
        return out

    def _handle_media(self, media_url: str, media_name: str | None = None,
                      caption: str = "") -> list[dict]:
        # While we are waiting for payment proof, an attachment is the answer to the
        # question — it must not be filed as a damage photo.
        if self.conv.state == "PAYMENT_PROOF":
            return self._attach_payment_proof(media_url, media_name)
        if self.conv.state == "WARRANTY_CLAIM":
            return self._log_warranty_claim(media_url=media_url, media_name=media_name)

        entry = {
            "url": media_url,
            "name": (media_name or "")[:120],
            "kind": self._media_kind(media_url, media_name),
            "caption": caption[:300],
        }
        # Capped: keep the most recent, which are the ones that show the damage
        # once the customer has thought a bit more.
        media = self._pending_media() + [entry]
        self.conv.ctx_set(pending_media=media[-self.MAX_PENDING_MEDIA:])

        # A caption is the customer describing the damage in their own words, which
        # is exactly what the estimator needs. Only fills a blank.
        if caption and not self.conv.ctx_get("damage"):
            self.conv.ctx_set(damage=caption)

        label = entry["name"] or ("a photo" if entry["kind"] == "DAMAGE" else "the document")
        job = self._find_job()
        if job:
            from ..models import JobPhoto

            db.session.add(JobPhoto(
                job_id=job.id, filename=media_url.rsplit("/", 1)[-1], url=media_url,
                kind=entry["kind"],
                caption=(entry["name"] or caption or "Sent via WhatsApp")[:255],
                source="whatsapp",
            ))
            db.session.commit()
            return [text(f"{label} received and added to your job card, thank you.")]

        # Committed here because pending_media is load-bearing: it is what feeds
        # BookingPhoto when the enquiry is finally created.
        db.session.commit()
        count = len(media)
        plural = "" if count == 1 else "s"
        return [text(
            f"{label} received. {count} attachment{plural} so far. "
            "They will all go on your enquiry.\n"
            "Send your registration number if you have not already, or type *menu*."
        )]

    # ── WhatsApp Flows (a form the customer filled in) ───────────────────
    def _menu_form(self) -> list[dict]:
        """Offer the enquiry Flow.

        A Flow gathers the structured answers a free-text chat cannot — the plate,
        the service, the day — as one tidy payload instead of five messages the
        desk has to piece together. It cannot carry a file, so the bot asks for the
        photographs straight afterwards (see :meth:`_lead_from_flow`).
        """
        flow_id = self.cfg.get("WA_FLOW_ENQUIRY_ID") or ""
        if not flow_id:
            # No form configured on this install. Fall back to the chat quote flow
            # rather than opening nothing.
            return self._menu_quote()

        self.conv.state = "MAIN_MENU"
        db.session.commit()
        return [{
            "type": "flow",
            "body": "Fill this in and our front desk will call you back with a firm "
                    "quotation. It takes about a minute.",
            "flow_id": flow_id,
            "flow_token": "enquiry",
            "header": "Enquiry form",
            "footer": self.company,
        }]

    def _handle_flow(self, flow: dict) -> list[dict]:
        """A completed WhatsApp Flow.

        A Flow collects the structured answers; **the files still arrive in the
        chat**, because WhatsApp Flows have no file-upload component. So the
        answers are written into the conversation context and handed to the same
        ``_create_lead`` the chat path uses, which already attaches every photo
        and PDF the customer sent before or alongside the form.
        """
        token = str(flow.get("flow_token") or "").strip()
        data = flow.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        # The token we set when sending the form is what routes the response.
        # Anything before the first colon names the form: "enquiry:2026-10-01".
        kind = token.split(":", 1)[0].strip().lower()

        # Meta owns the payload shape, so a garbled response_json yields no usable
        # answers at all. Raising an enquiry from that would put a record reading
        # "reg TBC, service Panel Beating" on the desk's list for every delivery
        # Meta retries. The customer is better served by being asked to resend.
        if not [value for value in data.values() if str(value).strip()]:
            current_app.logger.warning("Flow response carried no answers (%r)", token)
            return [text(
                "Sorry, that form came through empty. Please open it again and send it "
                "once more — or type *menu* if you would rather just chat."
            ), more_menu_reply(self.lang)]

        if kind in {"enquiry", "quote", "booking", "book"}:
            return self._lead_from_flow(data)

        current_app.logger.warning("Flow response with an unknown token %r", token)
        return [text(
            "Thank you — we have your details and the front desk will be in touch."
        ), more_menu_reply(self.lang)]

    @staticmethod
    def _flow_value(data: dict, *names: str) -> str:
        """The first non-empty value among ``names``, as trimmed text.

        The field names are ours, but they are also typed into Meta's Flow
        builder. A rename there must come back empty rather than raise, so every
        lookup tolerates a miss and callers supply the fallback.
        """
        for name in names:
            value = data.get(name)
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                value = ", ".join(str(item) for item in value)
            shown = str(value).strip()
            if shown:
                return shown
        return ""

    def _lead_from_flow(self, data: dict) -> list[dict]:
        """Turn a submitted enquiry form into an enquiry.

        Deliberately reuses :meth:`_create_lead` rather than raising the booking
        here: that method already knows how to find-or-create the customer and
        the vehicle, price the service, attach the pending media, clear the
        context and tell the desk. A second copy of that would drift from the
        chat path within a release.
        """
        name = self._flow_value(data, "contact_name", "name")
        email = self._flow_value(data, "contact_email", "email")
        reg = self._flow_value(data, "reg_no", "registration").upper().replace(" ", "")
        service = self._flow_value(data, "service", "service_type")
        damage = self._flow_value(data, "damage", "description", "details")
        day = self._flow_value(data, "preferred_date", "date")
        slot = self._flow_value(data, "preferred_time", "time")
        vehicle = self._flow_value(data, "vehicle", "vehicle_model")

        # A Flow dropdown carries whatever the builder typed into it, and an
        # unknown service would raise inside quick_quote. Match what we can and
        # fall back to the default rather than losing the enquiry.
        service = (service if service in SERVICE_NAMES else match_service(service)) \
            or "Panel Beating & Spray Painting"

        notes = [damage or "Submitted the enquiry form on WhatsApp"]
        if vehicle:
            notes.append(f"Vehicle: {vehicle}")

        self.conv.ctx_set(
            reg=reg or "TBC",
            service=service,
            damage="\n".join(notes),
            book_date=day or None,
            book_time=slot or None,
            contact_name=name,
            contact_email=email,
        )
        replies = self._create_lead(name, email=email)

        # A form cannot carry a photo, so ask for them explicitly — the damage
        # pictures are the single most useful thing the desk can receive. Slotted
        # ahead of the catch-all menu so the order reads confirmation → next step.
        invitation = text(
            "One thing the form cannot carry is the pictures.\n\n"
            "Send photographs of the damage here in the chat — the whole panel, "
            "then close-ups — and any assessor's report or quotation as a PDF. "
            "Everything you send is attached to your enquiry."
        )
        return replies[:-1] + [invitation] + replies[-1:]

    # ── interactive replies (buttons / list rows) ────────────────────────
    def _handle_choice(self, choice: str) -> list[dict]:
        if choice.startswith("svc:"):
            return self._start_quote_with_service(choice.split(":", 1)[1])
        if choice.startswith("bsvc:"):
            return self._input_book_service(choice.split(":", 1)[1])
        if choice.startswith("day:"):
            return self._input_book_date(choice.split(":", 1)[1])
        if choice.startswith("bslot:"):
            return self._input_book_time(choice.split(":", 1)[1])
        if choice.startswith("lang:"):
            return self._input_lang(choice.split(":", 1)[1])
        if choice.startswith("rate:"):
            return self._record_feedback(choice)
        if choice.startswith("a_approve:"):
            return self._quotation_decision(choice.split(":", 1)[1], approve=True)
        if choice.startswith("a_decline:"):
            return self._quotation_decision(choice.split(":", 1)[1], approve=False)
        if choice in {"a_approve", "a_decline", "doc_quote"}:
            # The `quotation_share` template's quick-reply buttons carry a FIXED
            # payload — Meta cannot interpolate the estimate id into an approved
            # template — so the bare ids resolve against the quotation we last
            # sent on this thread.
            estimate_id = self.conv.ctx_get("last_estimate_id")
            if not estimate_id:
                return self._fallback()
            if choice == "doc_quote":
                return self._document_reply(f"doc:quote:{estimate_id}")
            return self._quotation_decision(str(estimate_id),
                                            approve=choice == "a_approve")
        if choice.startswith("doc:"):
            return self._document_reply(choice)

        handlers = {
            "m_quote": self._menu_quote,
            "m_track": self._menu_track,
            "m_book": self._menu_book,
            "m_pay": self._menu_pay,
            "m_warranty": self._menu_warranty,
            "m_human": self._menu_human,
            "m_info": self._menu_info,
            "m_menu": self._go_main_menu,
            "m_services": self._menu_services,
            "m_form": self._menu_form,
            "m_lang": self._menu_lang,
            "a_approve": lambda: [text(
                "Great — thank you for approving. We will order the parts and start work. "
                "We will keep you posted at every stage. "
            )],
            "a_decline": lambda: [text(
                "Understood. A member of our team will contact you to discuss the quotation."
            )],
        }
        handler = handlers.get(choice)
        if handler:
            return handler()
        return self._fallback()

    # ── free text ────────────────────────────────────────────────────────
    def _handle_text(self, raw: str) -> list[dict]:
        state = self.conv.state

        # Language first, data-entry states included: answering the registration
        # prompt with "Shona" has to switch the conversation over, not be
        # rejected as a bad plate number.
        switch = self._language_request(raw)
        if switch is not None:
            return switch

        # A state that is waiting for data consumes the message first.
        state_handler = {
            "QUOTE_REG": self._input_quote_reg,
            "QUOTE_SERVICE": self._input_quote_service,
            "QUOTE_DESC": self._input_quote_desc,
            "QUOTE_CONTACT": self._input_quote_contact,
            "QUOTE_EMAIL": self._input_quote_email,
            "TRACK_REF": self._input_track_ref,
            "BOOK_SERVICE": self._input_book_service,
            "BOOK_DATE": self._input_book_date,
            "BOOK_TIME": self._input_book_time,
            "BOOK_CONTACT": self._input_book_contact,
            "PAYMENT_PROOF": self._input_payment_proof_text,
            "WARRANTY_CLAIM": self._input_warranty_text,
            "LANG": self._input_lang,
        }.get(state)
        if state_handler:
            # Allow a global escape hatch from any data-entry state.
            if re.fullmatch(r"(menu|main menu|cancel|stop|reset)", raw.strip().lower()):
                return self._go_main_menu()
            return state_handler(raw)

        if not raw:
            return self._fallback()

        # Nobody has chosen a language and they are plainly writing in one: meet
        # them where they are instead of making them ask for it. Kept out of the
        # data-entry states above so it can never eat the answer they just typed.
        detected = self._detect_language(raw)
        if detected:
            return self._apply_language(detected)

        intent = detect_intent(raw)
        if intent == "menu":
            return self._go_main_menu()
        if intent == "quote":
            return self._menu_quote()
        if intent == "track":
            return self._menu_track()
        if intent == "book":
            return self._menu_book()
        if intent == "human":
            return self._menu_human()
        if intent == "hours":
            return [text(
                f"*Opening hours*\n{self.cfg['COMPANY_HOURS']}\n\n"
                f"*Telephone*\n{self.cfg['COMPANY_TEL']}\n{self.cfg['COMPANY_MOBILE']}"
            )] + [more_menu_reply()]
        if intent == "location":
            return [text(
                f"*Where we are*\n{self.cfg['COMPANY_ADDRESS']}\n\n"
                f"*Telephone*\n{self.cfg['COMPANY_TEL']}\n"
                f"*Website*\n{self.cfg['COMPANY_WEBSITE']}"
            )] + [more_menu_reply()]
        if intent == "services":
            return self._menu_services()
        if intent == "warranty":
            return self._menu_warranty()
        if intent == "stop":
            customer = self.conv.customer or self._customer_by_wa()
            if customer:
                customer.whatsapp_opt_in = False
                db.session.commit()
            return [text(
                "You have been unsubscribed from progress updates. Reply *start* at any time "
                "to receive them again."
            )]
        if re.search(r"^(start|subscribe|resume)\b", raw.strip().lower()):
            customer = self.conv.customer or self._customer_by_wa()
            if customer:
                customer.whatsapp_opt_in = True
                db.session.commit()
            return [text("You are subscribed again.")] + [main_menu_reply(self.company, self.lang)]

        # Unstructured: try to be useful before falling back.
        service = match_service(raw)
        if service:
            return self._start_quote_with_service(service)
        job = self._lookup_job(raw)
        if job:
            return self._job_status_reply(job)

        return self._fallback()

    # ── menu actions ─────────────────────────────────────────────────────
    def _go_main_menu(self) -> list[dict]:
        self.conv.state = "MAIN_MENU"
        db.session.commit()
        return [main_menu_reply(self.company, self.lang)]

    def _menu_info(self) -> list[dict]:
        return [text(
            f"*{self.company}*\n\n"
            f"*Address*\n{self.cfg['COMPANY_ADDRESS']}\n\n"
            f"*Opening hours*\n{self.cfg['COMPANY_HOURS']}\n\n"
            f"*Telephone*\n{self.cfg['COMPANY_TEL']}\n{self.cfg['COMPANY_MOBILE']}\n\n"
            f"*Email*\n{self.cfg['COMPANY_EMAIL']}\n\n"
            f"*Website*\n{self.cfg['COMPANY_WEBSITE']}"
        ), more_menu_reply()]

    def _menu_services(self) -> list[dict]:
        """The price list, as a tappable list rather than a numbered block.

        It used to be numbered text telling the customer to "reply with a number",
        which only the QUOTE_SERVICE state honoured — so at the main menu the
        number went nowhere. The list removes the need for that instruction.

        Deliberately does NOT enter QUOTE_SERVICE to make typed numbers work. That
        traps the customer in the service question: "track my repair" typed next is
        then read as a bad service choice and answered with this same list. Typing a
        service *name* still works, through the unstructured fallback in
        ``_handle_text``.
        """
        return [service_list_reply(self.lang), main_menu_reply(self.company, self.lang)]

    def _menu_lang(self) -> list[dict]:
        # Remember where we were, so choosing a language resumes that step rather
        # than dumping the customer back at the main menu and losing their answers.
        if self.conv.state != "LANG":
            self.conv.ctx_set(lang_return_state=self.conv.state)
        self.conv.state = "LANG"
        db.session.commit()
        return [{"type": "list", "body": "Choose your language / Sarudza mutauro / Khetha ulimi",
                 "button": "Language",
                 "sections": [{"title": "Languages", "rows": [
                     {"id": f"lang:{code}", "title": label, "description": ""}
                     for code, label in LANGUAGES.items()
                 ]}]}]

    def _input_lang(self, raw: str) -> list[dict]:
        code = self._language_code(raw)
        if not code:
            return [text(t("lang_unknown", self.lang))]
        return self._apply_language(code)

    @staticmethod
    def _language_code(raw: str) -> str | None:
        """Map "Shona", "chishona", "sn" onto a language code."""
        words = re.findall(r"[a-z]+", (raw or "").strip().lower())
        for code, names in LANGUAGE_NAMES.items():
            if any(word in names for word in words):
                return code
        return None

    def _language_request(self, raw: str) -> list[dict] | None:
        """Reply to a spoken language request, or None if it is not one.

        Runs before the state handlers, which is what makes the switch available at
        any point in a conversation rather than only from a menu.
        """
        low = re.sub(r"[^a-z\s]", " ", (raw or "").strip().lower())
        low = re.sub(r"\s+", " ", low).strip()
        if not low or len(low) > 40:
            return None                      # too long to be "just change language"

        words = low.split()
        if low in LANGUAGE_PHRASES or set(words) & LANGUAGE_WORDS:
            return self._menu_lang()

        # A short message that only names a language: "Shona", "in shona", "chishona".
        named = {code for code, names in LANGUAGE_NAMES.items() if set(words) & names}
        if len(named) == 1 and len(words) <= 3:
            return self._apply_language(named.pop())
        return None

    def _detect_language(self, raw: str) -> str | None:
        """Guess the language from how the customer wrote, or None.

        Needs two markers before acting, and never runs once the customer has made
        an explicit choice — a borrowed word like "mari" in an English sentence
        must not flip the whole conversation.
        """
        if self.conv.ctx_get("lang_chosen"):
            return None
        words = set(re.findall(r"[a-z]+", (raw or "").lower()))
        for code, markers in LANGUAGE_MARKERS.items():
            if code != self.lang and len(words & markers) >= 2:
                return code
        return None

    def _apply_language(self, code: str) -> list[dict]:
        """Persist the language and carry on where the customer was.

        Reached from the language list, the ``lang:<code>`` ids and free text, so a
        customer can switch at any point.
        """
        if code not in LANGUAGES:
            return [text(t("lang_unknown", self.lang))]

        previous = self.conv.ctx_get("lang_return_state") or self.conv.state
        self.lang = code
        self.conv.ctx_set(lang=code, lang_chosen=True)
        self.conv.ctx_clear("lang_return_state")

        # Mid-flow: confirm, then re-ask the pending question in their language.
        # Without this, switching language while entering a registration number
        # would silently discard the quote they had already started.
        if previous in DATA_ENTRY_STATES:
            self.conv.state = previous
            db.session.commit()
            return [text(t("lang_set", code))] + self._resume_in_language(previous)

        # Otherwise fold the confirmation into the menu, so they get something
        # tappable rather than a bare acknowledgement.
        db.session.commit()
        return [main_menu_reply(self.company, code, prefix=t("lang_set", code))]

    def _resume_in_language(self, previous: str) -> list[dict]:
        """Re-ask the question the customer was answering, now translated."""
        if previous == "QUOTE_REG":
            return [text(t("ask_reg", self.lang))]
        if previous == "QUOTE_SERVICE":
            return [service_list_reply(self.lang)]
        if previous == "QUOTE_DESC":
            return [text(t("ask_desc", self.lang))]
        if previous == "QUOTE_CONTACT":
            return [text(t("ask_name", self.lang))]
        if previous == "QUOTE_EMAIL":
            return [text(t("ask_email", self.lang, name=""))]
        if previous == "TRACK_REF":
            return [text(t("ask_ref", self.lang))]
        if previous == "BOOK_SERVICE":
            return self._menu_book()
        if previous == "BOOK_TIME":
            day = self._parse_book_date(self.conv.ctx_get("book_date"))
            slots = self._time_list(day) if day else None
            return [slots] if slots else self._go_main_menu()
        return self._go_main_menu()

    # ── quotation approval (WhatsApp buttons) ────────────────────────────
    def _quotation_decision(self, raw_id: str, *, approve: bool) -> list[dict]:
        """Customer tapped Approve / Decline on a quotation we sent them."""
        from ..models import Estimate
        from .job_flow import approve_estimate

        try:
            estimate_id = int(raw_id)
        except (TypeError, ValueError):
            return self._fallback()

        estimate = db.session.get(Estimate, estimate_id)
        if not estimate:
            return [text(
                "I could not find that quotation. Our team will call you to confirm."
            )] + [more_menu_reply()]

        job = estimate.job
        customer = job.customer if job else None
        name = (customer.name if customer else "there").split(" ")[0]
        currency = estimate.currency or "USD"

        if approve:
            if estimate.status == "APPROVED":
                # Tapping approve again must not read as a second approval.
                # Meta can redeliver a tap, and customers do tap twice.
                reply = text(
                    f"Quotation *{estimate.reference}* is already approved, {name} — "
                    "we are already on it.\n\nReply *menu* if there is anything else."
                )
            else:
                approve_estimate(
                    estimate,
                    approved_by=f"{customer.name if customer else 'Customer'} (WhatsApp)",
                )
                reply = text(
                    f"Thank you, {name} — quotation *{estimate.reference}* is approved.\n\n"
                    f"We will order the parts, book the vehicle into the workshop and keep you "
                    f"updated at every stage."
                )
        else:
            estimate.status = "DECLINED"
            db.session.commit()
            reply = text(
                f"Understood, {name}. Quotation *{estimate.reference}* has been marked as "
                f"declined and one of our estimators will call you to discuss the options."
            )

        self.conv.ctx_set(last_estimate_id=estimate.id)
        self.conv.human_takeover = False
        db.session.commit()

        try:
            from ..models import NotificationLog

            db.session.add(NotificationLog(
                channel="whatsapp",
                recipient=customer.wa_number if customer else None,
                template="quotation_decision",
                body=f"{'Approved' if approve else 'Declined'} {estimate.reference} via WhatsApp",
                job_id=job.id if job else None,
                status="sent",
            ))
            db.session.commit()
        except Exception:  # noqa: BLE001 - logging must never break the reply
            db.session.rollback()

        return [reply, more_menu_reply()]

    def _document_reply(self, choice: str) -> list[dict]:
        """Customer tapped Download on a quotation, invoice or receipt.

        Nothing is attached here. Meta fetches the link itself when it delivers
        the message and the ``/doc/...`` route builds the PDF on demand, so the
        reply is instant, the customer can download the file as many times as
        they like, and a document that has since been re-issued is never served
        out of a stale attachment.

        The "Generating..." line goes first because fetching and building the
        PDF takes a moment on Meta's side, and a button that appears to do
        nothing is worse than one that says what it is doing.
        """
        parts = choice.split(":")
        if len(parts) != 3:
            return self._fallback()
        kind, raw_id = parts[1], parts[2]
        try:
            record_id = int(raw_id)
        except (TypeError, ValueError):
            return self._fallback()

        # (model, route, the field carrying the number, what the customer calls it)
        kinds = {
            "quote": (Estimate, "quote", "reference", "quotation"),
            "invoice": (Invoice, "invoice", "invoice_no", "invoice"),
            "receipt": (Payment, "receipt", "receipt_no", "receipt"),
        }
        entry = kinds.get(kind)
        if entry is None:
            return self._fallback()
        model, route, number_field, label = entry

        record = db.session.get(model, record_id)
        token = getattr(record, "public_token", None) if record else None
        if not token:
            # A button from a thread that has outlived its document, or one that
            # was voided. Answer with something a person can act on.
            return [text(
                f"I am sorry, that {label} is no longer available. "
                "Type *menu* if you would like a fresh copy or to speak to the team."
            )]

        number = getattr(record, number_field, None) or record_id
        return [
            text(f"Generating your {label}... one moment."),
            {
                "type": "document",
                "link": public_url(f"/doc/{route}/{token}.pdf"),
                "filename": f"{label.title()}-{number}.pdf",
                "caption": f"Your {label} {number}.",
            },
        ]

    def _menu_human(self) -> list[dict]:
        """Hand the thread to a human *and* leave a ticket behind.

        Setting ``human_takeover`` on its own only silenced the bot: nobody was
        assigned the request and nothing timed it, so it depended entirely on
        somebody watching the inbox. The Task is what puts it in the attention
        panel and on the to-do board with a name against it.
        """
        previous_state = self.conv.state
        self.conv.human_takeover = True
        self.conv.state = "HUMAN"
        task = self._log_callback(previous_state=previous_state)
        db.session.commit()

        body = t("handoff", self.lang, hours=self.cfg["COMPANY_HOURS"])
        if task:
            body += f"\n\n *Callback ref:* CALL-{task.id:04d}"
        return [text(body)]

    def _log_callback(self, *, previous_state: str) -> Task | None:
        """Open a callback ticket, or reuse the one already pending.

        A customer who taps "talk to a person" three times is one phone call, not
        three — so an existing open ticket for this number wins.
        """
        customer = self.conv.customer or self._customer_by_wa()
        # The WhatsApp profile name is all we have for a first-time caller, and
        # "Call back Callback Tester" is a far better ticket than a bare number.
        who = (customer.name if customer
               else (self.conv.profile_name or f"+{self.conv.wa_id}"))
        marker = f"WhatsApp +{self.conv.wa_id}"

        existing = Task.query.filter(
            Task.status != "DONE", Task.detail.like(f"%{marker}%")
        ).first()
        if existing:
            return existing

        detail = [
            f"Requested from WhatsApp by {who}.",
            marker,
            f"Bot was at: {previous_state}",
        ]
        if customer and customer.email:
            detail.append(f"Email: {customer.email}")
        last = self.conv.messages[0] if self.conv.messages else None
        if last and last.body:
            detail.append(f'Their last message: "{last.body[:200]}"')

        task = Task(
            title=f"Call back {who}",
            detail="\n".join(detail),
            category="Front desk",
            priority="HIGH",
            status="OPEN",
            due_date=date.today(),
            custodian_id=self._front_desk_user_id(),
        )
        db.session.add(task)
        db.session.flush()
        return task

    def _front_desk_user_id(self) -> int | None:
        """Best effort. An unassigned ticket still beats no ticket, so a missing
        front-desk account must not break the handoff."""
        try:
            from ..constants import ROLE_FRONT
            from ..models import User

            user = (User.query.filter_by(role=ROLE_FRONT, is_active_user=True)
                    .order_by(User.id).first())
            return user.id if user else None
        except Exception:  # noqa: BLE001
            return None

    # ── quote flow ───────────────────────────────────────────────────────
    def _menu_quote(self) -> list[dict]:
        self.conv.state = "QUOTE_REG"
        db.session.commit()
        return [text(t("ask_reg", self.lang))]

    def _start_quote_with_service(self, service: str) -> list[dict]:
        if service not in SERVICE_NAMES:
            return self._fallback()
        self.conv.ctx_set(service=service)
        # We may already know the vehicle (e.g. arriving from the service list
        # after the registration step). Only ask for what we still need.
        if self.conv.ctx_get("reg"):
            self.conv.state = "QUOTE_DESC"
            db.session.commit()
            return [text(f"{service} — noted.\n\n" + t("ask_desc", self.lang))]
        self.conv.state = "QUOTE_REG"
        db.session.commit()
        return [text(f"{service} — noted.\n\n" + t("ask_reg", self.lang))]

    def _input_quote_reg(self, raw: str) -> list[dict]:
        match = REG_PATTERN.search(raw.upper())
        reg = match.group(1).replace(" ", "").upper() if match else raw.strip().upper()[:12]
        if len(reg) < 3:
            return [text("That does not look like a registration number. Try again, e.g. ABC 1234.")]
        self.conv.ctx_set(reg=reg)
        service = self.conv.ctx_get("service")
        if service:
            self.conv.state = "QUOTE_DESC"
            db.session.commit()
            return [text(t("ask_desc", self.lang))]
        self.conv.state = "QUOTE_SERVICE"
        db.session.commit()
        return [service_list_reply(self.lang)]

    def _input_quote_service(self, raw: str) -> list[dict]:
        service = match_service(raw)
        if not service:
            try:
                idx = int(re.sub(r"\D", "", raw) or 0)
                if 1 <= idx <= len(SERVICE_NAMES):
                    service = SERVICE_NAMES[idx - 1]
            except ValueError:
                service = None
        if not service:
            return [service_list_reply(self.lang)]
        return self._start_quote_with_service(service)

    def _input_quote_desc(self, raw: str) -> list[dict]:
        self.conv.ctx_set(damage=raw.strip()[:600])
        self.conv.state = "QUOTE_CONTACT"
        db.session.commit()
        return [text(t("ask_name", self.lang))]

    def _input_quote_contact(self, raw: str) -> list[dict]:
        # Only hold the name here — the email is one more question, and the lead
        # is created once we have both. "skip" is still honoured, in which case
        # we fall back to the WhatsApp profile name.
        name = raw.strip()[:120]
        if name.lower() in {"skip", "none", "-"}:
            customer = self.conv.customer or self._customer_by_wa()
            name = customer.name if customer else f"WhatsApp +{self.conv.wa_id}"
        self.conv.ctx_set(contact_name=name)
        self.conv.state = "QUOTE_EMAIL"
        db.session.commit()
        first = name.split()[0] if name.split() else "there"
        return [text(t("ask_email", self.lang, name=first))]

    def _input_quote_email(self, raw: str) -> list[dict]:
        """Optional. An address is kept; anything else counts as a skip.

        Re-asking here would loop, and a missed address must not cost us the
        enquiry — the desk can see the whole thread and ask again when it calls.
        """
        answer = raw.strip()
        email = ""
        if answer.lower() not in {"skip", "none", "-", "no", "n/a", "na"}:
            match = EMAIL_PATTERN.search(answer)
            if match:
                email = match.group(0)[:160]
        return self._create_lead(self.conv.ctx_get("contact_name") or "", email=email)

    @staticmethod
    def _parse_book_date(raw: str | None) -> date | None:
        """Read the day the customer tapped in the booking flow, if any."""
        if not raw:
            return None
        match = re.search(r"\d{4}-\d{2}-\d{2}", str(raw))
        if not match:
            return None
        try:
            parsed = date.fromisoformat(match.group(0))
        except ValueError:
            return None
        return parsed if parsed >= date.today() else None

    def _create_lead(self, name: str, email: str = "") -> list[dict]:
        ctx = self.conv.context
        reg = ctx.get("reg") or "TBC"
        service = ctx.get("service") or "Panel Beating & Spray Painting"
        damage = ctx.get("damage") or "See WhatsApp conversation"
        # The booking flow asks which day suits the customer. That answer used to be
        # collected and then discarded, so every WhatsApp booking landed on tomorrow.
        booked_for = self._parse_book_date(ctx.get("book_date"))
        booked_at = ctx.get("book_time") or None

        customer = self.conv.customer or self._customer_by_wa()
        if not customer:
            customer = Customer(
                name=name, phone=f"+{self.conv.wa_id}", whatsapp=f"+{self.conv.wa_id}",
                email=email or None,
                notes="Created by WhatsApp bot", whatsapp_opt_in=True,
            )
            db.session.add(customer)
            db.session.flush()
            self.conv.customer_id = customer.id
        elif name and customer.name.startswith("WhatsApp +"):
            customer.name = name
        # Fill a blank address, but never overwrite one the front desk typed in.
        if email and not customer.email:
            customer.email = email

        notes = f"Auto-created from WhatsApp.\n{damage}"
        if booked_for:
            slot_note = booked_for.isoformat() + (f" at {booked_at}" if booked_at else "")
            notes += f"\nPreferred slot: {slot_note}"

        vehicle = Vehicle.query.filter_by(customer_id=customer.id, reg_no=reg).first()
        if not vehicle:
            vehicle = Vehicle(customer_id=customer.id, reg_no=reg)
            db.session.add(vehicle)
            db.session.flush()

        booking = Booking(
            customer_id=customer.id,
            vehicle_id=vehicle.id,
            service=service,
            slot_date=booked_for or (date.today() + timedelta(days=1)),
            slot_time=booked_at,
            status="REQUESTED",
            source="whatsapp",
            notes=notes,
            quoted_from=Decimal(str(quick_quote(service)["from_price"])),
        )
        db.session.add(booking)
        db.session.flush()

        # Attachments sent anywhere during the conversation used to be parked in
        # the context and then silently dropped here. They are the most useful
        # thing the desk can receive, so each one becomes a row against the enquiry
        # — photos and PDFs alike, with the customer's own filename kept.
        media = self._pending_media()
        for item in media:
            db.session.add(BookingPhoto(
                booking_id=booking.id,
                filename=(item["url"].rsplit("/", 1)[-1] or "attachment")[:255],
                url=item["url"],
                kind=item.get("kind") or "DAMAGE",
                caption=(item.get("name") or item.get("caption")
                         or "Sent via WhatsApp")[:255],
                source="whatsapp",
            ))

        self.conv.state = "MAIN_MENU"
        self.conv.ctx_clear("reg", "service", "damage", "book_date", "book_time",
                            "contact_name", "contact_email", "pending_media")
        db.session.commit()

        estimate_note = ""
        if service in {"Car Detailing", "Ceramic Coating", "Paint Protection Film",
                       "Car Vinyl Wrapping"}:
            estimate_note = f"\nIndicative price: *from USD {booking.quoted_from:.0f}*."
        day_note = ""
        if booked_for:
            day_note = (f"*Preferred slot:* {booked_for.strftime('%a %d %b %Y')}"
                        + (f" at {booked_at}" if booked_at else "") + "\n")
        photo_note = f"*Attachments:* {len(media)} received\n" if media else ""
        email_note = f"*Email:* {customer.email}\n" if customer.email else ""
        return [
            text(
                f"Request logged, {customer.name.split()[0]}.\n\n"
                f"*Reference:* {booking.reference}\n"
                f"*Vehicle:* {reg}\n"
                f"*Service:* {service}{estimate_note}\n"
                f"{day_note}{photo_note}{email_note}\n"
                "Our front desk will confirm your booking and send the firm quotation during "
                f"business hours ({self.cfg['COMPANY_HOURS']})."
            ),
            more_menu_reply(),
        ]

    # ── tracking flow ────────────────────────────────────────────────────
    def _menu_track(self) -> list[dict]:
        job = self._find_job()
        if job:
            return self._job_status_reply(job)
        self.conv.state = "TRACK_REF"
        db.session.commit()
        return [text(t("ask_ref", self.lang))]

    def _input_track_ref(self, raw: str) -> list[dict]:
        job = self._lookup_job(raw)
        if not job:
            return self._fallback(message=t("not_found", self.lang))
        self.conv.state = "MAIN_MENU"
        db.session.commit()
        return self._job_status_reply(job)

    def _job_status_reply(self, job: JobCard) -> list[dict]:
        progress = STAGE_PROGRESS.get(job.stage, 0)
        vehicle = job.vehicle.title if job.vehicle else "Vehicle"
        reg = job.vehicle.reg_no if job.vehicle else "-"
        stage = STAGE_LABELS.get(job.stage, job.stage)
        # No bar. It was ten block characters, which renders as tofu on some
        # devices and tells the customer nothing a percentage does not.
        lines = [
            f"*Job card {job.job_no}*\n{vehicle} ({reg})",
            "",
            f"*Stage*\n{stage}",
            f"*Progress*\n{progress}% complete",
        ]
        note = STAGE_CUSTOMER_TEXT.get(job.stage, "")
        if note:
            lines += ["", note]
        blocking = [p for p in job.job_parts if p.is_blocking]
        if blocking:
            names = ", ".join(p.description for p in blocking[:3])
            lines += ["", f"*Waiting on parts*\n{names}"]
        if job.promised_date:
            lines += ["", f"*Promised date*\n{job.promised_date.strftime('%d %b %Y')}"]
        if job.stage == "READY":
            invoice = job.outstanding_invoice
            if invoice and invoice.balance > 0:
                lines += ["", f"*Balance due*\n{invoice.currency} {invoice.balance:,.2f}"]
            lines += ["", "Please bring your collection slip and ID."]

        self.conv.ctx_set(last_job_no=job.job_no)
        db.session.commit()
        return [text("\n".join(lines)), more_menu_reply()]

    # ── booking flow ─────────────────────────────────────────────────────
    def _menu_book(self) -> list[dict]:
        self.conv.state = "BOOK_SERVICE"
        db.session.commit()
        return [service_list_reply(self.lang, "bsvc")]

    def _input_book_service(self, raw: str) -> list[dict]:
        service = match_service(raw)
        if not service:
            try:
                idx = int(re.sub(r"\D", "", raw) or 0)
                if 1 <= idx <= len(SERVICE_NAMES):
                    service = SERVICE_NAMES[idx - 1]
            except ValueError:
                service = None
        if not service:
            return [service_list_reply(self.lang, "bsvc")]
        self.conv.ctx_set(service=service)
        self.conv.state = "BOOK_DATE"
        db.session.commit()
        return [self._day_list(service)]

    def _day_list(self, service: str) -> dict:
        """The next six days. Saturday is called out honestly — the shop runs a
        skeleton crew, so "Limited" is information, not a warning."""
        today = date.today()
        rows = []
        for offset in range(1, 7):
            day = today + timedelta(days=offset)
            rows.append({
                "id": f"day:{day.isoformat()}",
                "title": day.strftime("%a %d %b"),
                "description": "Available" if day.weekday() < 5 else "Limited (Saturday)",
            })
        return {"type": "list", "body": f"{service} — which day suits you?",
                "button": "Choose day", "sections": [{"title": "Next 6 days", "rows": rows}]}

    def _slot_taken(self, day: date, slot: str) -> int:
        """How many vehicles are already expected in this slot.

        Cancelled and no-show appointments are excluded, so losing a booking
        gives the time back instead of holding it for ever.
        """
        return Booking.query.filter(
            Booking.slot_date == day,
            Booking.slot_time == slot,
            Booking.status.in_(("REQUESTED", "CONFIRMED", "ATTENDED")),
        ).count()

    def _time_list(self, day: date) -> dict | None:
        """The times still free on ``day``, or None when the whole day is gone."""
        rows = []
        for slot in BOOKING_SLOTS:
            taken = self._slot_taken(day, slot)
            if taken >= BOOKING_SLOT_CAPACITY:
                continue
            rows.append({
                "id": f"bslot:{day.isoformat()}:{slot}",
                "title": slot,
                "description": "Free" if not taken
                               else f"{taken} of {BOOKING_SLOT_CAPACITY} taken",
            })
        if not rows:
            return None
        return {"type": "list",
                "body": f"{day.strftime('%a %d %b')} — what time suits you?",
                "button": "Choose time",
                # WhatsApp trims a list at ten rows; BOOKING_SLOTS is nine.
                "sections": [{"title": "Available times", "rows": rows[:10]}]}

    def _input_book_date(self, raw: str) -> list[dict]:
        day = self._parse_book_date(raw)
        service = self.conv.ctx_get("service") or "Your service"
        if not day:
            # A day we could not read, or one already past. Re-offer the picker
            # rather than silently booking the customer into tomorrow.
            return [text("Please choose one of these days."), self._day_list(service)]

        self.conv.ctx_set(book_date=day.isoformat())
        slots = self._time_list(day)
        if not slots:
            return [text(f" {day.strftime('%a %d %b')} is fully booked. "
                         "Would another day work?"), self._day_list(service)]

        self.conv.state = "BOOK_TIME"
        db.session.commit()
        return [slots]

    def _input_book_time(self, raw: str) -> list[dict]:
        """Accept a tapped row, "09:00" or "9am" — people type both."""
        payload = raw.split(":", 1)[1] if raw.startswith("bslot:") else raw
        wanted = payload.strip().lower()
        slot = None

        match = re.search(r"(\d{1,2}):(\d{2})\s*$", wanted)
        if match:
            candidate = f"{int(match.group(1)):02d}:{match.group(2)}"
            slot = candidate if candidate in BOOKING_SLOTS else None
        else:
            ampm = re.fullmatch(r"(\d{1,2})\s*(am|pm)", wanted)
            if ampm:
                hour = int(ampm.group(1)) % 12 + (12 if ampm.group(2) == "pm" else 0)
                candidate = f"{hour:02d}:00"
                slot = candidate if candidate in BOOKING_SLOTS else None

        day = self._parse_book_date(self.conv.ctx_get("book_date"))
        if not slot:
            slots = self._time_list(day) if day else None
            if not slots:
                return self._fallback()
            return [text("Sorry, that is not one of our times."), slots]

        self.conv.ctx_set(book_time=slot)
        self.conv.state = "BOOK_CONTACT"
        db.session.commit()
        return [text(
            f"*{day.strftime('%a %d %b')} at {slot}* — noted.\n\n"
            "Almost done — please send your *name* and a contact number "
            "(or type *skip* to use this WhatsApp number)."
        )]

    def _input_book_contact(self, raw: str) -> list[dict]:
        name = raw.strip()[:120]
        if name.lower() in {"skip", "none", "-"}:
            customer = self.conv.customer or self._customer_by_wa()
            name = customer.name if customer else f"WhatsApp +{self.conv.wa_id}"
        return self._create_lead(name)

    # ── payment proof ────────────────────────────────────────────────────
    def _menu_pay(self) -> list[dict]:
        """Bank details, then wait for the screenshot.

        Customers send EcoCash confirmations whether or not we ask, and nothing
        used to catch them: the picture landed in the chat and the payment was
        never recorded until somebody noticed. Now the flow files it against the
        invoice and puts a verification task on the front desk.
        """
        self.conv.state = "PAYMENT_PROOF"
        db.session.commit()
        invoice = self._customer_invoice()
        owing = ""
        if invoice:
            owing = (f"\n\n*Outstanding*\n{invoice.invoice_no} — "
                     f"{invoice.currency} {invoice.balance:,.2f}")
        return [text(
            f"*How to pay*\n\n"
            f"{self.cfg['BANK_DETAILS']}\n"
            f"EcoCash: {self.cfg['ECONET_NUMBER']}{owing}\n\n"
            "If you have already paid, send a photo or screenshot of the confirmation "
            "and our front desk will verify it and send your receipt."
        )]

    def _lookup_invoice(self, raw: str) -> Invoice | None:
        match = re.search(r"\b(INV-\d{4}-\d{3,5})\b", raw or "", re.I)
        if not match:
            return None
        return Invoice.query.filter(
            db.func.upper(Invoice.invoice_no) == match.group(1).upper()
        ).first()

    def _customer_invoice(self) -> Invoice | None:
        """The invoice a payment proof most likely belongs to: the newest one
        that is still owing. A settled invoice is not something to pay again."""
        customer = self.conv.customer or self._customer_by_wa()
        if not customer:
            return None
        rows = (Invoice.query.filter(Invoice.customer_id == customer.id)
                .order_by(Invoice.id.desc()).limit(20).all())
        for invoice in rows:
            if invoice.status != "CANCELLED" and invoice.balance and invoice.balance > 0:
                return invoice
        return None

    def _input_payment_proof_text(self, raw: str) -> list[dict]:
        """They typed instead of sending a picture. Nudge, but stay in the state so
        the screenshot they send next is still recognised as payment proof."""
        invoice = self._lookup_invoice(raw) or self._customer_invoice()
        if invoice:
            return [text(
                f"Send a photo or screenshot of the payment for *{invoice.invoice_no}* "
                f"(balance *{invoice.currency} {invoice.balance:,.2f}*) and our front desk "
                "will verify it."
            )]
        return [text(
            "I need a picture — send the EcoCash confirmation or a photo of the "
            "deposit slip. Type *menu* if you would rather do something else."
        )]

    def _attach_payment_proof(self, media_url: str, media_name: str | None = None) -> list[dict]:
        invoice = self._customer_invoice()
        if not invoice:
            self.conv.state = "MAIN_MENU"
            db.session.commit()
            return [text(
                "Thank you. I could not find an invoice with a balance against this "
                "number, so I have left it with our front desk — they will pick it up "
                "from this chat."
            ), more_menu_reply()]

        customer = self.conv.customer or self._customer_by_wa()
        db.session.add(PaymentProof(
            invoice_id=invoice.id,
            customer_id=customer.id if customer else None,
            filename=media_url.rsplit("/", 1)[-1][:255] or "proof",
            url=media_url,
            note=(media_name or "Sent via WhatsApp")[:255],
        ))
        db.session.add(Task(
            title=f"Verify payment proof — {invoice.invoice_no}",
            detail="\n".join([
                f"Proof of payment sent on WhatsApp by "
                f"{customer.name if customer else 'a customer'}.",
                f"WhatsApp +{self.conv.wa_id}",
                f"Invoice {invoice.invoice_no}: balance "
                f"{invoice.currency} {invoice.balance:,.2f}",
                "Check it against the bank/EcoCash statement, then record the payment.",
            ]),
            category="Front desk",
            priority="HIGH",
            status="OPEN",
            due_date=date.today(),
            custodian_id=self._front_desk_user_id(),
        ))

        self.conv.state = "MAIN_MENU"
        db.session.commit()
        return [text(
            f"Proof received for {invoice.invoice_no}.\n\n"
            f"*Balance on record:* {invoice.currency} {invoice.balance:,.2f}\n\n"
            "Our front desk will check it against our statement and send your receipt. "
            "If anything does not match up, we will call you."
        ), more_menu_reply()]

    # ── warranty claims ──────────────────────────────────────────────────
    def _menu_warranty(self) -> list[dict]:
        """Not just the small print: open a claim and give it an owner.

        This used to answer with the warranty text and nothing else, so a genuine
        comeback ("the paint is lifting already") was a chat message somebody had
        to remember. Now the next thing the customer says becomes a workshop task
        tied to their job card.
        """
        from ..constants import WARRANTY_TEXT

        self.conv.state = "WARRANTY_CLAIM"
        db.session.commit()
        job = self._find_job()
        context = (f"We have *{job.job_no}* on file for this number."
                   if job else
                   "I could not match a job card to this number — send the job number "
                   "or registration if you have it.")
        return [text(
            f"{WARRANTY_TEXT}\n\n"
            f"{context}\n\n"
            "If something has gone wrong, tell me what it is and send a photo or a PDF "
            "if you can. I will log it for the workshop manager."
        )]

    def _input_warranty_text(self, raw: str) -> list[dict]:
        return self._log_warranty_claim(description=raw.strip()[:500])

    def _log_warranty_claim(self, *, description: str = "",
                            media_url: str | None = None,
                            media_name: str | None = None) -> list[dict]:
        """One open claim per conversation: further detail is added to it rather
        than opening a second ticket."""
        customer = self.conv.customer or self._customer_by_wa()
        job = self._find_job()
        marker = f"WhatsApp +{self.conv.wa_id}"

        task = db.session.get(Task, self.conv.ctx_get("warranty_task_id") or 0)
        is_new = task is None or task.status == "DONE"
        if is_new:
            task = Task(
                title=f"Warranty claim — {job.job_no if job else 'unmatched job card'}",
                detail="\n".join([
                    f"Claim raised on WhatsApp by "
                    f"{customer.name if customer else marker}.",
                    marker,
                    f"Job card: {job.job_no}" if job
                    else "No job card matched this phone number — find it before booking "
                         "anything in.",
                ]),
                category="Workshop",
                priority="HIGH",
                status="OPEN",
                due_date=date.today(),
                job_id=job.id if job else None,
                custodian_id=self._front_desk_user_id(),
            )
            db.session.add(task)
            db.session.flush()
            self.conv.ctx_set(warranty_task_id=task.id)

        if description:
            task.detail = f"{task.detail or ''}\nCustomer says: {description}"
        if media_url:
            task.detail = (f"{task.detail or ''}\nAttachment: "
                           f"{media_name or media_url}")
            if job:
                # On the job card the photo sits next to the original damage,
                # which is exactly where the person assessing the claim wants it.
                db.session.add(JobPhoto(
                    job_id=job.id,
                    filename=media_url.rsplit("/", 1)[-1][:255] or "warranty",
                    url=media_url, kind="WARRANTY",
                    caption=(media_name or "Warranty claim via WhatsApp")[:255],
                    source="whatsapp",
                ))

        db.session.commit()
        opening = "Warranty claim logged" if is_new else "Added to your warranty claim"
        return [text(
            f"{opening}"
            + (f" against {job.job_no}" if job else "") + ".\n\n"
            "Our workshop manager will review it and come back to you. Keep sending "
            "photos here if that helps, or type *menu* when you are done."
        ), more_menu_reply()]

    # ── feedback ─────────────────────────────────────────────────────────
    def _record_feedback(self, choice: str) -> list[dict]:
        """A tapped rating button, from the post-collection ask.

        Two id shapes arrive. Inside the service window the buttons are ours, so
        they carry the job id (`rate:17:5`). On the approved ``job_feedback``
        template the payload is fixed when the template is approved and can only
        be `rate:5`, so the job resolves against the thread — which is the one we
        just asked about.
        """
        parts = choice.split(":")
        try:
            if len(parts) >= 3:
                job = db.session.get(JobCard, int(parts[1]))
                rating = int(parts[2])
            else:
                job = self._find_job()
                rating = int(parts[1])
        except (IndexError, ValueError):
            return self._fallback()

        if not job or rating not in {1, 3, 5}:
            return [text("Thank you — I have passed that on to the workshop."),
                    more_menu_reply()]

        job.feedback_rating = rating
        job.feedback_at = utcnow()
        # The body of the button ("Poor") is sent through as text_body but is not
        # the customer's words, so it is not stored as feedback_text.
        customer = job.customer
        name = (customer.name.split(" ")[0] if customer and customer.name else "there")

        if rating == 1:
            # A bad rating is the only early warning that a job is coming back,
            # so it gets a task rather than a line in a report.
            db.session.add(Task(
                title=f"Follow up unhappy customer — {job.job_no}",
                detail=("\n".join([
                    f"{customer.name if customer else 'Customer'} rated the job *Poor*.",
                    f"WhatsApp +{self.conv.wa_id}",
                    "Call them before they tell everyone else.",
                ])),
                category="Front desk", priority="HIGH", status="OPEN",
                due_date=date.today(), job_id=job.id,
                custodian_id=self._front_desk_user_id(),
            ))
            job.feedback_text = "Rated Poor via WhatsApp"
        elif rating == 5:
            job.feedback_text = "Rated Excellent via WhatsApp"
        else:
            job.feedback_text = "Rated Okay via WhatsApp"

        db.session.commit()

        if rating == 1:
            body = (f"Thank you for telling us, {name} — and sorry. The workshop owner "
                    "has been told and somebody will call you today.")
        elif rating == 5:
            body = f"That means a lot, {name} — thank you."
        else:
            body = (f"Thanks for the honest answer, {name}. If there is anything we "
                    "should put right, just reply here.")
        return [text(body), more_menu_reply()]

    # ── helpers ──────────────────────────────────────────────────────────
    def _customer_by_wa(self) -> Customer | None:
        if self.conv.customer_id:
            return db.session.get(Customer, self.conv.customer_id)
        tail = self.conv.wa_id[-9:]
        return Customer.query.filter(
            db.or_(Customer.phone.like(f"%{tail}%"), Customer.whatsapp.like(f"%{tail}%"))
        ).first()

    def _find_job(self) -> JobCard | None:
        """Best-effort: find the customer's most recent open job."""
        last = self.conv.ctx_get("last_job_no")
        if last:
            job = JobCard.query.filter_by(job_no=last).first()
            if job:
                return job
        customer = self._customer_by_wa()
        if not customer:
            return None
        jobs = JobCard.query.filter_by(customer_id=customer.id).order_by(JobCard.id.desc()).all()
        for job in jobs:
            if job.is_open:
                return job
        return jobs[0] if jobs else None

    def _lookup_job(self, raw: str) -> JobCard | None:
        if not raw:
            return None
        match = JOB_PATTERN.search(raw.upper())
        if match:
            job = JobCard.query.filter_by(job_no=match.group(1).upper()).first()
            if job:
                return job
        reg_match = REG_PATTERN.search(raw.upper().replace(" ", ""))
        candidate = reg_match.group(1) if reg_match else raw.strip().upper()
        if len(candidate) >= 3:
            vehicle = Vehicle.query.filter(
                db.func.upper(db.func.replace(Vehicle.reg_no, " ", "")) == candidate.replace(" ", "")
            ).first()
            if vehicle:
                jobs = JobCard.query.filter_by(vehicle_id=vehicle.id).order_by(JobCard.id.desc()).all()
                for job in jobs:
                    if job.is_open:
                        return job
                return jobs[0] if jobs else None
        return None

    def _fallback(self, message: str | None = None) -> list[dict]:
        self.reset_strikes = False
        strikes = int(self.conv.ctx_get("strikes", 0)) + 1
        self.conv.ctx_set(strikes=strikes)
        db.session.commit()
        if strikes >= self.FALLBACK_LIMIT:
            self.conv.ctx_set(strikes=0)
            return self._menu_human()
        return [
            text(message or t("invalid_choice", self.lang)),
            main_menu_reply(self.company, self.lang),
        ]


def handle_inbound(
    conversation: WaConversation,
    *,
    text_body: str | None = None,
    interactive_id: str | None = None,
    media_url: str | None = None,
    media_name: str | None = None,
    flow_response: dict | None = None,
    app=None,
) -> list[dict]:
    """Run one conversational turn for an inbound message."""
    return IntentRouter(conversation, app=app).handle(
        text_body=text_body, interactive_id=interactive_id, media_url=media_url,
        media_name=media_name, flow_response=flow_response,
    )


class _ServiceIds:
    """Expose service ids for the list rows (used by tests)."""

    @staticmethod
    def rows() -> list[str]:
        return [f"svc:{name}" for name in SERVICE_NAMES]


__all__ = [
    "IntentRouter", "handle_inbound", "detect_intent", "match_service",
    "main_menu_reply", "service_list_reply", "SERVICE_BY_CODE",
]
