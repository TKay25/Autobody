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
from datetime import date, datetime, timedelta
from decimal import Decimal

from flask import current_app

from ..constants import (
    APPEARANCE_SERVICES,
    BOOKING_EXPECTED_STATUSES,
    BOOKING_SLOT_CAPACITY,
    BOOKING_SLOTS,
    SERVICES,
    SERVICE_BY_CODE,
    SERVICE_BY_NAME,
    SERVICE_NAMES,
    STAGE_CUSTOMER_TEXT,
    STAGE_LABELS,
    STAGE_PROGRESS,
)
from ..extensions import db
from ..models import (Booking, BookingPhoto, Customer, Estimate, Invoice, JobCard, JobPhoto,
                      Payment, PaymentProof, Task, Vehicle, WaConversation, utcnow)
from . import bookings as booking_ops
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
    "QUOTE_SERVICE", "QUOTE_DESC", "QUOTE_CONTACT", "QUOTE_EMAIL",
    "TRACK_REF", "BOOK_SERVICE", "BOOK_DATE", "BOOK_TIME", "BOOK_CONTACT",
    "PAYMENT_PROOF", "WARRANTY_CLAIM",
}

T = {
    # Said once, when we first speak to somebody in a session. Greeting a customer
    # by name is the difference between a workshop and a vending machine, so the
    # name is bolded — WhatsApp renders *name* as bold. Plain text otherwise:
    # there is a test in the suite that no message carrying an emoji ever leaves
    # this service, and it is house style rather than an accident.
    #
    # The opening address is its own string because a number we hold no name for
    # has none to use — "Hi *WhatsApp*" is worse than no name at all.
    "greeting": {
        "en": "{hi}\n\n"
              "Welcome to *{company}*. We do panel beating, spray painting, "
              "detailing, ceramic coating, paint protection film and vinyl "
              "wrapping.\n\n"
              "Tell us what you need and the front desk will call you back.",
        "sn": "{hi}\n\n"
              "Takugamuchirai ku*{company}*. Tinoita panel beating, kupenda, "
              "kuchenesa, ceramic coating, PPF nevinyl wrapping.\n\n"
              "Tiudzei zvamunoda, vekumberi vachakufonerei.",
        "nd": "{hi}\n\n"
              "Siyakwamukela ku*{company}*. Senza panel beating, ukupenda, "
              "ukuhlanza, ceramic coating, PPF kanye ne-vinyl wrapping.\n\n"
              "Sitshele okudingayo, abangaphambili bazokufonela.",
    },
    "greeting_named": {
        "en": "Hi *{name}*,",
        "sn": "Mhoro *{name}*,",
        "nd": "Sawubona *{name}*,",
    },
    "greeting_anon": {
        "en": "Hello,",
        "sn": "Mhoro,",
        "nd": "Sawubona,",
    },
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
    # Nobody is reporting damage when they ask for a coating or a valet, and
    # "describe the damage" reads as though the bot has not heard which service
    # they picked. Same question, worded for the work.
    "ask_desc_appearance": {
        "en": "Tell us about the vehicle — make, model, and what you have in "
              "mind. A photo or two helps us give you a firm price.",
        "sn": "Tiudzei nezvemota — rudzi, mucherechedzo, nezvamunoda. Mifananidzo "
              "inotibatsira kupa mutengo wakasimba.",
        "nd": "Sitshele ngemoto — uhlobo, imodeli, lalokho onakho engqondweni. "
              "Izithombe zisiza ukunikeza intengo eqinile.",
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
# Approve / decline first, deliberately. The loop below returns the FIRST match,
# and a customer answering a quotation writes things like "approve the quote" —
# with `quote` ahead of these that message opened the quote flow instead of
# approving the quotation they already have.
INTENT_PATTERNS = [
    ("approve", r"\b(approve|approved|approval|authorise|authorize|go ahead|proceed|accept)\b"),
    ("decline", r"\b(decline|declined|reject|refuse|do not proceed|don't proceed|cancel it)\b"),
    ("book", r"\b(book|booking|appointment|slot|schedule|bhuka)\b"),
    ("track", r"\b(track|status|progress|where is|how far|ready|collection|kupi)\b"),
    ("quote", r"\b(quote|quotation|estimate|price|cost|how much|charge|mari)\b"),
    ("hours", r"\b(hours|open|opening|closing|time|what time)\b"),
    ("location", r"\b(where|location|address|directions|find you|kupi)\b"),
    ("human", r"\b(human|agent|person|talk to|speak to|manager|call me|phone)\b"),
    ("services", r"\b(services|what do you do|offerings|menu of)\b"),
    ("warranty", r"\b(warranty|guarantee|guarantee period)\b"),
    # A greeting must match at the start with anything after it. Requiring the
    # *whole* message meant "hello again" and "hi there" matched nothing and fell
    # through to "Sorry, I did not understand that" — a rotten answer to somebody
    # who had just said hello. Anything carrying a real request is caught by an
    # earlier pattern (this list is scanned in order), so only greetings reach it.
    ("menu", r"^(menu|main menu|start)\b"
             r"|^\s*(hi+|hey+|hello+|hie|hiya|mhoro|sawubona|"
             r"good\s+(morning|afternoon|evening|day))\b"),
    ("stop", r"\b(stop|unsubscribe|opt out)\b"),
]


# Wording that can only mean the *appointment*, never the quotation. A bare
# "cancel" belongs to the estimate decline path — `decline` already owns that
# word — so every phrase here names the appointment. Getting this wrong in the
# other direction would be far worse: a customer who typed "cancel my booking"
# used to be told a member of staff would discuss their quotation.
BOOKING_MOVE_PHRASES = (
    r"move (my|the|our) (appointment|booking)",
    r"(change|shift|reschedule|postpone) (my|the|our)?\s*(appointment|booking|slot|time)",
    r"\breschedule\b",
    r"\bmove it\b",
)
BOOKING_CANCEL_PHRASES = (
    r"cancel (my|the|our) (appointment|booking|slot)",
    r"\bcancel appointment\b",
    r"(can'?t|cannot|won'?t|will not) (make|attend) it\b",
    r"not (coming|going to make it)\b",
)


def detect_intent(text: str) -> str | None:
    low = (text or "").strip().lower()
    if not low:
        return None
    for intent, pattern in INTENT_PATTERNS:
        if re.search(pattern, low):
            return intent
    return None


def _flow_key(key: str) -> str:
    """A payload key reduced to something comparable.

    Meta's builder names components itself (``What_do_you_need_11da7f``) and the
    completion payload key carries the screen and component index, so an answer
    arrives as ``screen_0_What_do_you_need_0``. Case, spaces, punctuation and that
    prefix are all noise when the question is "which answer is the service?".
    """
    stripped = re.sub(r"^screen[_\-\s]*\d+[_\-\s]*", "", str(key), flags=re.I)
    return re.sub(r"[^a-z0-9]", "", stripped.lower())


# How a Flow's answer might be labelled, per field we read. Only consulted when
# the payload does not use our own names — see IntentRouter._flow_value.
_FLOW_HINTS: dict[str, tuple[str, ...]] = {
    "service": ("whatdoyouneed", "service", "servicetype", "need"),
    "vehicle": ("vehiclemakemodel", "makeandmodel", "vehicle", "model", "make"),
    "reg_no": ("registrationnumber", "registration", "regno", "platenumber", "plate"),
    "preferred_date": ("whichday", "preferreddate", "bookdate", "date"),
    "preferred_time": ("whattime", "preferredtime", "booktime", "time"),
    "notes": ("anythingweshouldknow", "anythingelse", "notes", "comment"),
    "contact_email": ("email", "contactemail"),
    "contact_name": ("yourname", "contactname", "fullname", "name"),
    "damage": ("describe", "damage", "description", "details"),
}


def _flow_answer_text(value) -> str:
    """A Flow answer as trimmed text, or "" when there is nothing readable."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(item) for item in value)
    return str(value).strip()


def _flow_media_value(value) -> bool:
    """True for a picker's answer rather than a typed one.

    Meta returns Photo / Document Picker entries as ``[{id, file_name, …}]`` under
    the component's name. They are downloaded separately, so they must never be
    read as text — a key called "Photos of the damage" would otherwise satisfy a
    lookup for the damage *description* and put a wall of JSON on the record.
    """
    return (isinstance(value, list) and bool(value)
            and all(isinstance(item, dict) for item in value))


def match_flow_service(raw: str) -> str | None:
    """Resolve a Flow's service answer, which may be decorated.

    A Dropdown returns the option **id**, not its title, and Meta's Flow builder
    generates those ids from the titles — so what arrives is
    ``1_Panel_Beating_&_Spray_Painting`` or ``0_Autobody`` rather than the plain
    name. Matching only the plain name meant every enquiry from a form silently
    fell back to the default service: a wrong price on a quotation, not an error
    anybody would notice.

    Compared on letters and digits alone, so "Auto Body", "Autobody" and
    "0_Autobody" are the same service.
    """
    low = (raw or "").strip()
    if not low:
        return None
    if low in SERVICE_NAMES:
        return low
    if low.upper() in SERVICE_BY_CODE:
        return SERVICE_BY_CODE[low.upper()]["name"]

    # The builder prefixes its generated ids with the option's index:
    # "0_Autobody", "1_Panel_Beating_&_Spray_Painting". Drop that first — the
    # index is positional, so it would otherwise defeat the comparison entirely.
    cleaned = re.sub(r"^\d+[\s_\-.]*", "", low)
    squashed = re.sub(r"[^a-z0-9]", "", cleaned.lower())
    if not squashed:
        return None
    for name in SERVICE_NAMES:
        if re.sub(r"[^a-z0-9]", "", name.lower()) == squashed:
            return name
    return match_service(low)


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

    **"Get a quote" and "Book a service" used to be separate rows.** They are one
    journey — tell us what you need and the desk calls you back — so they are one
    row now, and the service choice that used to be hidden behind them is on the
    screen the customer lands on. Two rows that lead to the same desk, ask
    overlapping questions and differ only in wording is how a menu stops being
    readable.

    The ``menu: "main"`` marker is how :meth:`IntentRouter.handle` finds this
    reply to hang the greeting on, rather than guessing at its button label.
    """
    body = t("menu_prompt", lang)
    if prefix:
        body = prefix + "\n\n" + body
    rows = [
        {"id": "m_enquiries", "title": "Enquiries",
         "description": "What we do, and a call back"},
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
        booking_form_id = (current_app.config or {}).get("WA_FLOW_BOOKING_ID") or ""
    except RuntimeError:      # no app context, e.g. a text-only unit test
        booking_form_id = ""
    if booking_form_id:
        # Directly under "Enquiries": the same journey, but the customer picks the
        # day rather than waiting for a call.
        rows.insert(1, {"id": "m_bform", "title": "Book an appointment",
                        "description": "Pick a day and time on a form"})

    return {
        "type": "list",
        "menu": "main",
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
            {"id": "m_enquiries", "title": "Make an enquiry"},
            {"id": "m_track", "title": "Track my repair"},
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
        replies = [
            more_menu_reply(self.lang) if r.get("menu") == "more" else r
            for r in replies
        ]

        return self._with_greeting(replies)

    def _with_greeting(self, replies: list[dict]) -> list[dict]:
        """Say hello, once, on the first menu of a session.

        The greeting rides *inside* the main menu's body rather than being a
        message of its own. Two reasons: it can never arrive on its own because
        the menu failed to render, and every caller that shows the menu gets the
        greeting for free without threading a name through a dozen call sites.

        Only the main menu qualifies. Greeting somebody again every time they tap
        "back" would be the opposite of friendly.
        """
        greeting = self._greeting()
        if not greeting:
            return replies
        for reply in replies:
            if reply.get("menu") == "main":
                reply["body"] = f"{greeting}\n\n{reply['body']}"
                return replies
        return replies

    def _greeting(self) -> str:
        """The greeting line, or "" when we have already said hello recently.

        A customer who comes back after a fortnight is starting a new
        conversation, not continuing the last one, so the clock is the session
        window. Inside it, they are mid-conversation and a second "welcome to
        Topclass" reads as though the bot reset itself.
        """
        now = utcnow()
        last = self.conv.ctx_get("greeted_at")
        if last:
            try:
                since = now - datetime.fromisoformat(last)
            except (TypeError, ValueError):
                since = None
            if since is not None and since < timedelta(hours=self.session_window_hours):
                return ""
        first = self._greeting_name()
        # "Hi *WhatsApp*" is what dropping the fallback straight into the greeting
        # produced for a number we hold no name for. Say hello plainly instead.
        hi = (t("greeting_named", self.lang, name=first) if first
              else t("greeting_anon", self.lang))
        self.conv.ctx_set(greeted_at=now.isoformat())
        db.session.commit()
        return t("greeting", self.lang, hi=hi, company=self.company)

    def _greeting_name(self) -> str:
        """The first name to greet somebody by, or "" when we hold none.

        ``_wa_name()`` falls back to the number so a *record* is never written
        with a blank name. That fallback is right for a record and wrong here.
        """
        name = (self.conv.profile_name
                or (self.conv.customer.name if self.conv.customer else "")
                or "").strip()
        if not name:
            customer = self._customer_by_wa()
            name = (customer.name if customer else "").strip()
        if not name or name.startswith("WhatsApp +"):
            return ""
        return name.split()[0]

    @property
    def session_window_hours(self) -> int:
        try:
            return int(self.cfg.get("WA_SESSION_WINDOW_HOURS") or 24)
        except (TypeError, ValueError):
            return 24

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

    def _attach_flow_media(self, media: list[dict]) -> int:
        """Fold a Flow's own attachments into the pending-media list.

        A picker's files are downloaded by the webhook and arrive here as
        ``{url, name}``. They go onto the *same* ``pending_media`` list a photo
        sent in the chat lands on, so ``_create_lead`` writes one kind of
        ``BookingPhoto`` for both routes — the desk cannot tell, and does not need
        to.

        Deliberately silent: a chat attachment is acknowledged one at a time, but
        these arrived as part of a form the customer just submitted, and the form
        already gets a confirmation. Returns how many were kept.
        """
        if not media:
            return 0
        entries = self._pending_media()
        for item in media:
            url = item.get("url")
            if not url:
                continue
            name = (item.get("name") or "")[:120]
            entries.append({
                "url": url,
                "name": name,
                "kind": self._media_kind(url, name),
                "caption": "",
            })
        kept = entries[-self.MAX_PENDING_MEDIA:]
        self.conv.ctx_set(pending_media=kept)
        db.session.commit()
        return len(kept)

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
        """Offer the enquiry Flow on its own.

        Kept for a menu row cached on a customer's phone from before the enquiry
        journey replaced it — tapping it should still open the form rather than
        nothing. New conversations reach the same form through "Enquiries", which
        explains the service first.
        """
        flow_id = self.cfg.get("WA_FLOW_ENQUIRY_ID") or ""
        if not flow_id:
            # No form configured on this install. Fall back to the chat enquiry
            # rather than opening nothing.
            return self._menu_quote()

        self.conv.state = "MAIN_MENU"
        db.session.commit()
        return [self._enquiry_flow_reply(flow_id)]

    def _menu_book_form(self) -> list[dict]:
        """Offer the booking Flow.

        The chat booking flow works, but it takes six messages to book an
        appointment and a customer who stops halfway leaves a half-filled
        context. The form does the same work in one screen and arrives as a
        single payload — so it is offered first, with the chat flow still there
        for anyone who would rather just type.
        """
        flow_id = self.cfg.get("WA_FLOW_BOOKING_ID") or ""
        if not flow_id:
            # No booking form built on this install. The chat flow is the honest
            # fallback — it exists and it finishes the job.
            return self._menu_book()

        self.conv.state = "MAIN_MENU"
        db.session.commit()
        return [{
            "type": "flow",
            "body": "Pick a service, a day and a time, and we will confirm your "
                    "appointment. It takes about a minute.",
            "flow_id": flow_id,
            "flow_token": "booking",
            "screen": self.cfg.get("WA_FLOW_BOOKING_SCREEN") or "BOOKING",
            "header": "Booking form",
            "footer": self.company,
        }]

    def _handle_flow(self, flow: dict) -> list[dict]:
        """A completed WhatsApp Flow.

        The answers are written into the conversation context and handed to the
        same ``_create_lead`` the chat path uses, which already attaches every
        photo and PDF the customer sent before or alongside the form.

        A Flow can also return files of its own, from a Photo Picker or Document
        Picker on the form. The webhook downloads each one before this runs, so
        they arrive in ``flow["media"]`` and are attached here — the same list a
        chat attachment goes into, so the desk sees one kind of attachment
        whichever route it came by.
        """
        token = str(flow.get("flow_token") or "").strip()
        data = flow.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        # Files the Flow itself collected (PhotoPicker / DocumentPicker). Taken
        # before the answers are read, so the record that gets raised already has
        # them in hand — the same list a chat photo would be in.
        self._attach_flow_media(flow.get("media") or [])
        # The token we set when sending the form is what routes the response.
        # Anything before the first colon names the form: "enquiry:2026-10-01".
        kind = token.split(":", 1)[0].strip().lower()

        # Meta owns the payload shape, so a garbled response_json yields no usable
        # answers at all. Raising an enquiry from that would put a hollow record on
        # the desk's list for every delivery Meta retries. The customer is better
        # served by being asked to resend. A picker does not count as an answer —
        # it is downloaded separately, and ``str([])`` is truthy, so a form that
        # carried nothing but an empty picker would otherwise look answered.
        if not any(_flow_answer_text(value) for key, value in data.items()
                   if not _flow_media_value(value)):
            current_app.logger.warning("Flow response carried no answers (%r)", token)
            return [text(
                "Sorry, that form came through empty. Please open it again and send it "
                "once more — or type *menu* if you would rather just chat."
            ), more_menu_reply(self.lang)]

        if kind in {"enquiry", "quote"}:
            return self._lead_from_flow(data)
        if kind in {"booking", "book"}:
            # Same answers, different question. A booking form asks which day
            # suits; the enquiry form asks what is wrong with the vehicle. Both
            # end up as a Booking record, but a booking must not be told to
            # "send photos of the damage" and an enquiry must not be told its
            # slot is held.
            return self._booking_from_flow(data)

        current_app.logger.warning("Flow response with an unknown token %r", token)
        return [text(
            "Thank you — we have your details and the front desk will be in touch."
        ), more_menu_reply(self.lang)]

    @classmethod
    def _flow_value(cls, data: dict, *names: str) -> str:
        """The first non-empty answer among ``names``, as trimmed text.

        Two passes, because the payload keys are not ours to choose. Meta's
        builder names components itself — ``What_do_you_need_11da7f`` — and the
        completion payload key carries the screen and component index, so the
        answer arrives as ``screen_0_What_do_you_need_0``. Demanding our own names
        meant a form that worked perfectly in the builder produced an enquiry with
        no service and no vehicle: priced against the default card and with nothing
        saying which car it was about.

        1. **Exactly**, for a payload that does use our names (and every test).
        2. **By meaning**, comparing on letters and digits alone — so case, spaces,
           punctuation and the ``screen_<n>_`` prefix are ignored and a key like
           ``whatdoyouneed0`` still answers a lookup for ``service``.

        A miss returns "" rather than raising: a field the builder named something
        we cannot place is a blank on the record, not a broken webhook.
        """
        for name in names:
            shown = _flow_answer_text((data or {}).get(name))
            if shown:
                return shown

        if not names:
            return ""

        # Picker answers are skipped: they are media, not text.
        answers = {
            _flow_key(key): value
            for key, value in (data or {}).items()
            if not _flow_media_value(value)
        }
        if not answers:
            return ""

        # Longest hint first, so "vehicle make model" is tried before "make" and
        # cannot be beaten to the answer by a substring of itself.
        candidates = set(names) | set(_FLOW_HINTS.get(names[0], ()))
        for hint in sorted(candidates, key=len, reverse=True):
            needle = _flow_key(hint)
            if not needle:
                continue
            for key, value in answers.items():
                if needle in key:
                    shown = _flow_answer_text(value)
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
        name = self._flow_value(data, "contact_name", "name") or self._wa_name()
        email = self._flow_value(data, "contact_email", "email")
        service = self._flow_value(data, "service", "service_type")
        vehicle = self._flow_value(data, "vehicle", "vehicle_model")
        # The form no longer asks for either of these: the plate is taken when the
        # vehicle actually arrives, and the make and model is what the form
        # collects instead. Both are still read, so a Flow that carries them —
        # or an install that has not been rebuilt yet — keeps working.
        reg = self._flow_value(data, "reg_no", "registration").upper().replace(" ", "")
        damage = self._flow_value(data, "damage", "description", "details")
        day = self._flow_value(data, "preferred_date", "date")
        slot = self._flow_value(data, "preferred_time", "time")

        # A Flow dropdown carries whatever the builder generated — an option id
        # like "0_Autobody", not the title. An unrecognised service would raise
        # inside quick_quote, and a *mis*-recognised one is worse: it prices the
        # job against the wrong card. Match what we can, then fall back.
        service = match_flow_service(service) or "Panel Beating & Spray Painting"

        # The make and model leads: with no registration on the form it is the
        # only thing on the record that says which vehicle this is about.
        notes = []
        if vehicle:
            notes.append(f"Vehicle: {vehicle}")
        notes.append(damage or "Submitted the enquiry form on WhatsApp")

        self.conv.ctx_set(
            # No "TBC" placeholder: a plate-less form raises a booking with no
            # vehicle rather than a vehicle called TBC. See :meth:`_create_lead`.
            reg=reg,
            service=service,
            vehicle=vehicle,
            damage="\n".join(notes),
            book_date=day or None,
            book_time=slot or None,
            contact_name=name,
            contact_email=email,
        )
        # Read BEFORE _create_lead, which clears the list on its way out.
        form_carried_files = bool(self._pending_media())

        replies = self._create_lead(name, email=email)

        if form_carried_files:
            # The form's own picker already collected the pictures, so asking for
            # them again reads as though nobody looked at what was sent. The
            # confirmation stands on its own.
            return replies

        # Otherwise ask: the damage pictures are the single most useful thing the
        # desk can receive. Slotted ahead of the catch-all menu so the order reads
        # confirmation → next step.
        invitation = text(
            "Send us photographs of the damage and we will quote faster.\n\n"
            "A picture of the whole panel and a close-up is enough, and any "
            "assessor's report or existing quotation can come as a PDF. "
            "Everything you send is attached to your enquiry."
        )
        return replies[:-1] + [invitation] + replies[-1:]

    def _booking_from_flow(self, data: dict) -> list[dict]:
        """Turn a submitted booking form into an appointment.

        Shares ``_create_lead`` with the enquiry path, because that is where the
        customer, the vehicle and the pending attachments are dealt with and a
        second copy would drift. The difference is the last line: an enquiry is
        told the desk will call, a booking is told when to turn up.

        The slot is checked against capacity **before** the booking is written,
        so a form that asks for a time the shop cannot take is not confirmed and
        then quietly overbooked. The form's dropdown cannot know how full a slot
        is; only we can.
        """
        name = self._flow_value(data, "contact_name", "name") or self._wa_name()
        email = self._flow_value(data, "contact_email", "email")
        reg = self._flow_value(data, "reg_no", "registration").upper().replace(" ", "")
        service = self._flow_value(data, "service", "service_type")
        day = self._flow_value(data, "preferred_date", "date")
        slot = self._flow_value(data, "preferred_time", "time")
        notes = self._flow_value(data, "notes", "damage", "description")

        service = match_flow_service(service) or "Panel Beating & Spray Painting"

        when = self._parse_book_date(day)
        taken = self._slot_taken(when, slot) if (when and slot) else 0
        slot_is_full = bool(when and slot and taken >= BOOKING_SLOT_CAPACITY)

        self.conv.ctx_set(
            reg=reg,
            service=service,
            damage=notes or "Booked an appointment on WhatsApp",
            book_date=day or None,
            book_time=slot or None,
            contact_name=name,
            contact_email=email,
        )
        replies = self._create_lead(name, email=email)

        # ``_create_lead``'s confirmation already prints the slot it recorded, so
        # this block carries only the decision the customer still needs: whether
        # the time they asked for is actually theirs, or the desk will come back
        # with an alternative.
        lines: list[str] = []
        if not when:
            # The form's date picker is required, so this means a payload we could
            # not read. Say so rather than promising a day nobody chose.
            lines.append("The day did not come through on the form, so expect a "
                         "message agreeing a date with you.")
        elif slot_is_full:
            lines.append(f"{slot} on {when.strftime('%a %d %b')} is already full "
                         f"({taken} booked), so expect the desk to offer you the "
                         "nearest alternative.")
        else:
            lines.append("Every appointment is confirmed by the desk, so watch for "
                         "a message. Reply *menu* if anything changes.")

        # Slotted ahead of the catch-all menu, the same shape as the enquiry path,
        # so the order reads confirmation → the one thing to know → what next.
        return replies[:-1] + [text("\n".join(lines))] + replies[-1:]

    # ── interactive replies (buttons / list rows) ────────────────────────
    def _handle_choice(self, choice: str) -> list[dict]:
        if choice.startswith("svc:"):
            # An old menu row, cached on a customer's phone before the enquiry
            # journey replaced the quote one. Same destination either way.
            return self._menu_enquiry_service(choice.split(":", 1)[1])
        if choice.startswith("enq:"):
            return self._menu_enquiry_service(choice.split(":", 1)[1])
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
            # The `quotation_share` and `quotation_ready` templates carry FIXED
            # button payloads — Meta cannot interpolate an estimate id into an
            # approved template — so the bare ids resolve against the quotation
            # we last sent on this thread.
            return self._decision_from_thread(approve=choice == "a_approve",
                                              download=choice == "doc_quote")
        if choice in {"doc_invoice", "doc_receipt"}:
            # Same reason: `invoice_share` and `receipt_share` carry one fixed
            # payload each, naming the document rather than a record id.
            return self._document_from_thread(invoice=choice == "doc_invoice")
        if choice.startswith("doc:"):
            return self._document_reply(choice)

        handlers = {
            "m_enquiries": self._menu_quote,
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
            "m_bform": self._menu_book_form,
            # The day-before reminder's buttons. Bare payloads, identical to the
            # ids on the approved `booking_reminder` template, so a tap means the
            # same thing either side of the 24-hour window.
            "b_move": self._booking_move,
            "b_cancel": self._booking_cancel,
            "b_cancel_yes": self._booking_cancel_confirm,
            "b_cancel_keep": self._booking_cancel_keep,
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

        # Someone who has gone quiet may type the request rather than tap the
        # reminder, and `decline` already owns the word "cancel". Checked before
        # the state handlers and the intents, exactly as the language switch is.
        booking_request = self._booking_request(raw)
        if booking_request is not None:
            return booking_request

        # A state that is waiting for data consumes the message first.
        state_handler = {
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
        if intent in {"approve", "decline"}:
            # The templates tell the customer to *reply* approve or decline, and
            # nothing used to catch it — the phrase fell through to the fallback,
            # so the instruction on the quotation was a dead end.
            return self._decision_from_thread(approve=intent == "approve")
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
        return [service_list_reply(self.lang, "enq"), main_menu_reply(self.company, self.lang)]

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

    def _booking_request(self, raw: str) -> list[dict] | None:
        """A typed request to move or cancel the appointment, or ``None``.

        Only phrases that name the appointment match. A bare *cancel* is left
        alone on purpose: it belongs to the quotation's decline path, and
        hijacking that would be a worse bug than asking someone to tap a button.
        """
        low = (raw or "").strip().lower()
        if not low:
            return None
        for pattern in BOOKING_MOVE_PHRASES:
            if re.search(pattern, low):
                return self._booking_move()
        for pattern in BOOKING_CANCEL_PHRASES:
            if re.search(pattern, low):
                return self._booking_cancel()
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
        if previous == "QUOTE_SERVICE":
            return [service_list_reply(self.lang, "enq")]
        if previous == "QUOTE_DESC":
            return [text(t(self._desc_prompt_key(self.conv.ctx_get("service") or ""),
                          self.lang))]
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
    def _decision_from_thread(self, *, approve: bool = False,
                              download: bool = False) -> list[dict]:
        """Resolve a quotation decision that arrived without an estimate id.

        Three things land here: a bare template payload (`a_approve`,
        `doc_quote`), a typed *approve* / *decline*, and nothing else — because a
        template's buttons are frozen when Meta approves it and cannot carry an
        id, and a typed reply has none either. All three resolve against the
        quotation we last sent on this conversation.
        """
        estimate_id = self.conv.ctx_get("last_estimate_id")
        if not estimate_id:
            return self._fallback()
        if download:
            return self._document_reply(f"doc:quote:{estimate_id}")
        return self._quotation_decision(str(estimate_id), approve=approve)

    def _document_from_thread(self, *, invoice: bool) -> list[dict]:
        """Resolve a bare Download payload against the thread's last document.

        ``invoice_share`` and ``receipt_share`` each carry one fixed payload —
        ``doc_invoice`` / ``doc_receipt`` — because a template's buttons cannot
        interpolate a record id. Whichever of those the notification last sent is
        recorded on the conversation, and that is what a tap resolves against.
        """
        record_id = self.conv.ctx_get("last_invoice_id" if invoice else "last_receipt_id")
        if not record_id:
            return self._fallback()
        return self._document_reply(f"doc:{'invoice' if invoice else 'receipt'}:{record_id}")

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
        """Start an enquiry: which service first.

        This used to ask for the registration number before anything else, and it
        was the first thing a customer saw after tapping "Get a quote". The
        enquiry *form* never asked for a plate — the make and model stands in and
        the plate is taken when the car actually arrives — so the chat was asking
        for information the form had already been told it did not need, and asking
        it before the customer even knew whether we were the right shop.
        """
        self.conv.state = "QUOTE_SERVICE"
        db.session.commit()
        return [service_list_reply(self.lang, "enq")]

    def _menu_enquiry_service(self, service: str) -> list[dict]:
        """What the service involves, then the way to actually enquire.

        The brief is the point of the row above it: a customer who has just been
        told what a ceramic coating is knows whether they want one, and can say so
        on the form instead of describing it to the desk over the phone.
        """
        if service not in SERVICE_NAMES:
            return self._fallback()
        self.conv.ctx_set(service=service)
        brief = self._service_brief(service)

        flow_id = self.cfg.get("WA_FLOW_ENQUIRY_ID") or ""
        if flow_id:
            self.conv.state = "MAIN_MENU"
            db.session.commit()
            return [text(brief), self._enquiry_flow_reply(flow_id)]

        # No Flow built on this install. Carry on in the chat with the service
        # already chosen, so the customer is still asked for only the rest.
        self.conv.state = "QUOTE_DESC"
        db.session.commit()
        return [text(brief + "\n\n" + t(self._desc_prompt_key(service), self.lang))]

    @staticmethod
    def _desc_prompt_key(service: str) -> str:
        """Which follow-up question fits the service just chosen."""
        return ("ask_desc_appearance" if service in APPEARANCE_SERVICES
                else "ask_desc")

    def _service_brief(self, service: str) -> str:
        """One service, described in the workshop's own words."""
        info = SERVICE_BY_NAME.get(service) or {}
        parts = [f"*{info.get('short') or service}*"]
        if info.get("blurb"):
            parts.append(info["blurb"])
        quote = quick_quote(service)
        if quote:
            parts.append(f"From *USD {quote['from_price']:.0f}* — the firm price "
                         "depends on what we find when we see the vehicle.")
        else:
            # Panel and paint is priced off the damage, and guessing at a number
            # before seeing the car is how a workshop loses a customer at the
            # counter. Say so rather than inventing a figure.
            parts.append("Priced off the damage, so we quote once we have seen "
                         "the vehicle — send photos and we will be close.")
        return "\n\n".join(parts)

    def _enquiry_flow_reply(self, flow_id: str) -> dict:
        """The enquiry Flow, as the webhook sends it."""
        return {
            "type": "flow",
            "body": "Fill this in and our front desk will call you back with a firm "
                    "quotation. It takes about a minute.",
            "flow_id": flow_id,
            "flow_token": "enquiry",
            "screen": self.cfg.get("WA_FLOW_ENQUIRY_SCREEN") or "QUESTION_ONE",
            "header": "Enquiry form",
            "footer": self.company,
        }

    def _start_quote_with_service(self, service: str) -> list[dict]:
        # Kept as the name the state handlers and the unstructured fallback call;
        # the journey itself is the brief-then-form one.
        return self._menu_enquiry_service(service)

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
            return [service_list_reply(self.lang, "enq")]
        return self._menu_enquiry_service(service)

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

    def _wa_name(self) -> str:
        """The best name we already hold for this customer.

        WhatsApp hands us the profile name on every inbound message
        (``contacts[].profile.name``, kept on the conversation), so a form does
        not need to ask for it. Asking for something the customer has already
        told WhatsApp reads as carelessness, and it is one more field to abandon.

        Falls back to the customer record, then to the number itself — a record
        must never be created with a blank name, which is what an empty
        ``contact_name`` used to produce.
        """
        customer = self.conv.customer or self._customer_by_wa()
        return (self.conv.profile_name
                or (customer.name if customer else "")
                or f"WhatsApp +{self.conv.wa_id}")

    def _create_lead(self, name: str, email: str = "") -> list[dict]:
        ctx = self.conv.context
        # A booking form has no registration field, and a detailing appointment
        # does not need one. "TBC" used to be invented here and then turned into a
        # real Vehicle row, so every plate-less booking littered the customer's
        # vehicles with one called TBC. No plate now means no vehicle, which the
        # model tolerates — Booking.vehicle_id is nullable.
        reg = (ctx.get("reg") or "").strip()
        if reg.upper() == "TBC":
            reg = ""
        service = ctx.get("service") or "Panel Beating & Spray Painting"
        damage = ctx.get("damage") or "See WhatsApp conversation"
        # The booking flow asks which day suits the customer. That answer used to be
        # collected and then discarded, so every WhatsApp booking landed on tomorrow.
        booked_for = self._parse_book_date(ctx.get("book_date"))
        booked_at = ctx.get("book_time") or None

        # Never write a blank name. A form that no longer asks for one (the
        # customer's name comes from WhatsApp) would otherwise create a customer
        # record with an empty name, which reads as a broken row on every screen
        # that shows it.
        name = (name or "").strip() or self._wa_name()

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

        vehicle = None
        if reg:
            vehicle = Vehicle.query.filter_by(customer_id=customer.id, reg_no=reg).first()
            if not vehicle:
                vehicle = Vehicle(customer_id=customer.id, reg_no=reg)
                db.session.add(vehicle)
                db.session.flush()

        booking = Booking(
            customer_id=customer.id,
            vehicle_id=vehicle.id if vehicle else None,
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
                            "contact_name", "contact_email", "pending_media", "vehicle")
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
        # Omitted rather than printed as "Vehicle: " — a booking made from the form
        # often has no plate, and an empty label reads like a missing field. The
        # make and model stands in when there is one, since with no registration
        # it is all that identifies the vehicle.
        vehicle_label = reg or (ctx.get("vehicle") or "").strip()
        vehicle_note = f"*Vehicle:* {vehicle_label}\n" if vehicle_label else ""
        return [
            text(
                f"Request logged, {customer.name.split()[0]}.\n\n"
                f"*Reference:* {booking.reference}\n"
                f"{vehicle_note}"
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
            Booking.status.in_(BOOKING_EXPECTED_STATUSES),
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

        # A typed time is accepted, but not one the floor cannot take. The picker
        # only offers free slots; typing is the way round that, and booking into
        # a full slot is the sort of thing that only shows up on the day.
        if day and self._slot_taken(day, slot) >= BOOKING_SLOT_CAPACITY:
            slots = self._time_list(day)
            if not slots:
                return [text(
                    f"{day.strftime('%a %d %b')} is fully booked. Would another day "
                    "work?"
                ), self._day_list(self.conv.ctx_get("service") or "Your service")]
            return [text(
                f"{slot} on {day.strftime('%a %d %b')} is already full. "
                "Please pick another time:"
            ), slots]

        # A move, not a new booking: the customer tapped *Move it*, so this
        # replaces the appointment they already have instead of adding a second.
        pending = self._pending_booking("reschedule_booking_id")
        if pending is not None and day:
            return self._apply_reschedule(pending, day, slot)

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

    # ── moving / cancelling an appointment ───────────────────────────────
    # Reached from the *Move it* and *Cancel appointment* buttons on the
    # day-before reminder, or from a typed phrase naming the appointment.
    def _customer_booking(self) -> Booking | None:
        """The appointment a customer means by "my booking".

        Their soonest one that is still expected and has not been. A cancelled or
        completed appointment is not something to move, and one that has already
        happened is not something to cancel, so offering either would be worse
        than saying we could not find it.
        """
        customer = self.conv.customer or self._customer_by_wa()
        if not customer:
            return None
        return (Booking.query
                .filter(Booking.customer_id == customer.id,
                        Booking.status.in_(BOOKING_EXPECTED_STATUSES),
                        Booking.slot_date >= date.today())
                .order_by(Booking.slot_date.asc(), Booking.slot_time.asc())
                .first())

    def _pending_booking(self, key: str) -> Booking | None:
        """The booking a tapped button refers to, re-read by id.

        By id rather than "the customer's next one", so a tap can only ever act
        on the appointment the customer was actually shown. If they booked a
        second vehicle in between, *that* one must not be the one cancelled.
        """
        booking_id = self.conv.ctx_get(key)
        try:
            booking = db.session.get(Booking, int(booking_id)) if booking_id else None
        except (TypeError, ValueError):
            # A corrupt context must degrade, never raise into the webhook.
            return None
        if booking is None or booking.status not in BOOKING_EXPECTED_STATUSES:
            return None
        return booking

    def _booking_move(self) -> list[dict]:
        """Start moving an appointment: collect the new day and time."""
        booking = self._customer_booking()
        if booking is None:
            return [text(
                "I could not find an appointment coming up against this number, so "
                "there is nothing for me to move. Our front desk will pick it up from "
                "this chat — or reply *book* to make a new one."
            ), more_menu_reply(self.lang)]

        # Which booking is being moved. The day and time that follow are
        # collected by the ordinary booking flow, and without this the customer
        # would finish with a *second* appointment beside the one they meant to
        # change.
        self.conv.ctx_set(reschedule_booking_id=booking.id, service=booking.service)
        self.conv.state = "BOOK_DATE"
        db.session.commit()
        return [text(
            f"Let's move *{booking.display_reference}* — currently "
            f"{booking_ops.slot_text(booking)}.\n\nWhich day suits you instead?"
        ), self._day_list(booking.service)]

    def _booking_cancel(self) -> list[dict]:
        """Ask first. An appointment is worth more than the tap that loses it."""
        booking = self._customer_booking()
        if booking is None:
            return [text(
                "I could not find an appointment coming up against this number, so "
                "there is nothing for me to cancel. Our front desk will pick it up "
                "from this chat."
            ), more_menu_reply(self.lang)]

        self.conv.ctx_set(cancel_booking_id=booking.id)
        db.session.commit()
        return [buttons(
            f"Cancel your appointment on *{booking_ops.slot_text(booking)}*?\n\n"
            f"Reference: {booking.display_reference}",
            [("b_cancel_yes", "Yes, cancel it"), ("b_cancel_keep", "No, keep it")],
            header="Cancel appointment",
        )]

    def _booking_cancel_confirm(self) -> list[dict]:
        """Only reached from *Yes, cancel it*."""
        booking = self._pending_booking("cancel_booking_id")
        self.conv.ctx_clear("cancel_booking_id", "reschedule_booking_id")
        self.conv.state = "MAIN_MENU"
        db.session.commit()

        if booking is None:
            return [text(
                "That appointment is no longer on the books, so I have not cancelled "
                "anything. Our front desk will pick it up from this chat."
            ), more_menu_reply(self.lang)]

        when = booking_ops.slot_text(booking)
        if not booking_ops.cancel(booking, by="customer"):
            return [text(
                f"*{when}* was already cancelled, so nothing has changed."
            ), more_menu_reply(self.lang)]

        return [text(
            f"Cancelled — {when} is clear. Nothing further is needed from you.\n\n"
            "Reply *book* whenever you would like another time."
        ), more_menu_reply(self.lang)]

    def _booking_cancel_keep(self) -> list[dict]:
        """*No, keep it* — say so, and leave the appointment alone."""
        booking = self._pending_booking("cancel_booking_id")
        self.conv.ctx_clear("cancel_booking_id")
        db.session.commit()
        if booking is None:
            return [text("Nothing has changed."), more_menu_reply(self.lang)]
        return [text(
            f"Kept — we will see you on *{booking_ops.slot_text(booking)}*."
        ), more_menu_reply(self.lang)]

    def _apply_reschedule(self, booking: Booking, day: date, slot: str) -> list[dict]:
        """Land the new day and time on the existing appointment."""
        previous = booking_ops.slot_text(booking)
        self.conv.ctx_clear("reschedule_booking_id", "service", "book_date", "book_time")
        self.conv.state = "MAIN_MENU"
        db.session.commit()

        # Shared with the desk's Reschedule dialog, so the customer and the front
        # desk cannot be given different answers for the same change.
        booking_ops.reschedule(booking, slot_date=day, slot_time=slot)
        return [text(
            f"Moved — *{booking.display_reference}* is now "
            f"{booking_ops.slot_text(booking)}.\n\n"
            f"It was {previous}."
        ), more_menu_reply(self.lang)]

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
