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
    SERVICE_BY_CODE,
    SERVICE_NAMES,
    STAGE_CUSTOMER_TEXT,
    STAGE_LABELS,
    STAGE_PROGRESS,
)
from ..extensions import db
from ..models import Booking, Customer, JobCard, Vehicle, WaConversation
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
    "QUOTE_REG", "QUOTE_SERVICE", "QUOTE_DESC", "QUOTE_CONTACT",
    "TRACK_REF", "BOOK_SERVICE", "BOOK_DATE", "BOOK_CONTACT",
}

T = {
    "welcome": {
        "en": "Hello {name}! 👋 Welcome to {company}.\nWe are Masters of Restoration — panel beating, spray painting, detailing, ceramic coating and PPF.\n\nHow can we help you today?",
        "sn": "Mhoro {name}! 👋 Takugamuchirai ku{company}.\nTinogadzira mota — panel beating, kupenda, kuchenesa, ceramic coating nePPF.\n\nTingakubatsirai sei nhasi?",
        "nd": "Sawubona {name}! 👋 Siyakwamukela ku{company}.\nSilungisa izimoto — panel beating, ukupenda, ukuhlanza, ceramic coating nePPF.\n\nSingakusiza njani lamuhla?",
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
        "en": "Briefly describe the damage or what you need done. You can also send photos 📷.",
        "sn": "Tsanangurai muchidimbu kukuvara kwemota. Munogonawo kutumira mifananidzo 📷.",
        "nd": "Chaza kafitshane umonakalo. Ungathumela futhi izithombe 📷.",
    },
    "ask_ref": {
        "en": "Please send your job number (e.g. TC-2026-0007) or vehicle registration.",
        "sn": "Tumirai nhamba yejobi (semuenzaniso TC-2026-0007) kana nhamba yemota.",
        "nd": "Thumela inombolo yomsebenzi (isb. TC-2026-0007) kumbe inombolo yemota.",
    },
    "not_found": {        "en": "I could not find anything with that reference. Please check and try again, or type *menu*.",
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
        "en": "Thank you for choosing {company}. Drive safely! 🚗",
        "sn": "Tinotenda nekusarudza {company}. Fambai zvakanaka! 🚗",
        "nd": "Siyabonga ngokukhetha {company}. Hamba kuhle! 🚗",
    },
    "lang_hint": {
        "en": "🌐 Reply *Shona* or *Ndebele* at any time to switch language.",
        "sn": "🌐 Pindurai *English* kana *Ndebele* chero nguva kushandura mutauro.",
        "nd": "🌐 Phendula *English* kumbe *Shona* noma nini ukushintsha ulimi.",
    },
    "lang_set": {
        "en": "Language set to English. ✅",
        "sn": "Mutauro washandurwa kuShona. ✅",
        "nd": "Ulimi lushintshiwe lwaba isiNdebele. ✅",
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
    body = t("menu_prompt", lang) + "\n\n" + t("lang_hint", lang)
    if prefix:
        body = prefix + "\n\n" + body
    return {
        "type": "list",
        "body": body,
        "button": "Start",
        "sections": [{"title": company, "rows": [
            {"id": "m_quote", "title": "Get a quote",
             "description": "Send damage photos, get a price"},
            {"id": "m_book", "title": "Book a service",
             "description": "Detailing, ceramic coating, PPF"},
            {"id": "m_track", "title": "Track my repair",
             "description": "Job number or registration"},
            {"id": "m_services", "title": "Our services & prices"},
            {"id": "m_human", "title": "Talk to a person"},
            {"id": "m_lang", "title": "🌐 Language"},
            {"id": "m_info", "title": "Contact details"},
        ]}],
    }


def service_list_reply(lang: str, id_prefix: str = "svc") -> dict:
    """The service picker.

    ``id_prefix`` exists because the booking flow needs its own ids: the quote
    flow claimed ``svc:``, so tapping a service while booking was routed into the
    quote flow instead and a booking could never actually be completed by tap.
    """
    rows = [
        {"id": f"{id_prefix}:{name}", "title": name[:24],
         "description": f"From USD {quick_quote(name)['from_price']:.0f}"}
        for name in SERVICE_NAMES
        if quick_quote(name)
    ]
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
        "body": t("more_prompt", lang) + "\n\n" + t("lang_hint", lang),
        "button": "Options",
        "sections": [{"title": "What next?", "rows": [
            {"id": "m_quote", "title": "Get a quote"},
            {"id": "m_track", "title": "Track my repair"},
            {"id": "m_book", "title": "Book a service"},
            {"id": "m_services", "title": "Our services"},
            {"id": "m_human", "title": "Talk to a person"},
            {"id": "m_lang", "title": "🌐 Language"},
            {"id": "m_info", "title": "Contact details"},
        ]}],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Router
# ─────────────────────────────────────────────────────────────────────────────
class IntentRouter:
    """Processes one inbound message and returns the replies to send."""

    FALLBACK_LIMIT = 2

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
               media_url: str | None = None) -> list[dict]:
        raw = (text_body or "").strip()
        choice = (interactive_id or "").strip()

        if self.conv.human_takeover:
            # A human is handling this thread; the bot stays silent.
            return []

        if media_url:
            replies = self._handle_media(media_url)
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

    # ── media (damage photos) ────────────────────────────────────────────
    def _handle_media(self, media_url: str) -> list[dict]:
        self.conv.ctx_set(pending_media=(self.conv.ctx_get("pending_media") or []) + [media_url])
        job = self._find_job()
        if job:
            from ..models import JobPhoto

            db.session.add(JobPhoto(
                job_id=job.id, filename=media_url.rsplit("/", 1)[-1], url=media_url,
                kind="DAMAGE", caption="Sent via WhatsApp", source="whatsapp",
            ))
            db.session.commit()
            return [text("📷 Photo received and attached to your job card, thank you!")]
        return [text(
            "📷 Thank you, we received your photo. Send your registration number so we can "
            "attach it to the right vehicle, or type *menu*."
        )]

    # ── interactive replies (buttons / list rows) ────────────────────────
    def _handle_choice(self, choice: str) -> list[dict]:
        if choice.startswith("svc:"):
            return self._start_quote_with_service(choice.split(":", 1)[1])
        if choice.startswith("bsvc:"):
            return self._input_book_service(choice.split(":", 1)[1])
        if choice.startswith("day:"):
            return self._input_book_date(choice.split(":", 1)[1])
        if choice.startswith("lang:"):
            return self._input_lang(choice.split(":", 1)[1])
        if choice.startswith("a_approve:"):
            return self._quotation_decision(choice.split(":", 1)[1], approve=True)
        if choice.startswith("a_decline:"):
            return self._quotation_decision(choice.split(":", 1)[1], approve=False)

        handlers = {
            "m_quote": self._menu_quote,
            "m_track": self._menu_track,
            "m_book": self._menu_book,
            "m_human": self._menu_human,
            "m_info": self._menu_info,
            "m_menu": self._go_main_menu,
            "m_services": self._menu_services,
            "m_lang": self._menu_lang,
            "a_approve": lambda: [text(
                "Great — thank you for approving. We will order the parts and start work. "
                "We will keep you posted at every stage. 🚗"
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
            "TRACK_REF": self._input_track_ref,
            "BOOK_SERVICE": self._input_book_service,
            "BOOK_DATE": self._input_book_date,
            "BOOK_CONTACT": self._input_book_contact,
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
                f"🕐 We are open *{self.cfg['COMPANY_HOURS']}*.\n"
                f"Call: {self.cfg['COMPANY_TEL']} / {self.cfg['COMPANY_MOBILE']}"
            )] + [more_menu_reply()]
        if intent == "location":
            return [text(
                f"📍 {self.cfg['COMPANY_ADDRESS']}\n\n"
                f"Tel: {self.cfg['COMPANY_TEL']}\nWeb: {self.cfg['COMPANY_WEBSITE']}"
            )] + [more_menu_reply()]
        if intent == "services":
            return self._menu_services()
        if intent == "warranty":
            from ..constants import WARRANTY_TEXT

            return [text(f"🛡️ {WARRANTY_TEXT}")] + [more_menu_reply()]
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
            return [text("You are subscribed again. ✅")] + [main_menu_reply(self.company, self.lang)]

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
            f"*{self.company}*\n"
            f"📍 {self.cfg['COMPANY_ADDRESS']}\n"
            f"🕐 {self.cfg['COMPANY_HOURS']}\n"
            f"📞 {self.cfg['COMPANY_TEL']} / {self.cfg['COMPANY_MOBILE']}\n"
            f"✉️ {self.cfg['COMPANY_EMAIL']}\n"
            f"🌐 {self.cfg['COMPANY_WEBSITE']}"
        ), more_menu_reply()]

    def _menu_services(self) -> list[dict]:
        lines = ["*Our services*", ""]
        for idx, name in enumerate(SERVICE_NAMES, start=1):
            quote = quick_quote(name)
            price = f" — from USD {quote['from_price']:.0f}" if quote else ""
            lines.append(f"{idx}. {name}{price}")
        lines += ["", "Reply with a number or the service name to get a quote."]
        return [text("\n".join(lines)), main_menu_reply(self.company, self.lang)]

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
        """Map "Shona", "chishona", "sn" … onto a language code."""
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
        if previous == "TRACK_REF":
            return [text(t("ask_ref", self.lang))]
        if previous == "BOOK_SERVICE":
            return self._menu_book()
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
                    f"🎉 Thank you, {name} — quotation *{estimate.reference}* is approved.\n\n"
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

    def _menu_human(self) -> list[dict]:
        self.conv.human_takeover = True
        self.conv.state = "HUMAN"
        db.session.commit()
        return [text(t("handoff", self.lang, hours=self.cfg["COMPANY_HOURS"]))]

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
            return [text(f"{service} — noted. 👍\n\n" + t("ask_desc", self.lang))]
        self.conv.state = "QUOTE_REG"
        db.session.commit()
        return [text(f"{service} — noted. 👍\n\n" + t("ask_reg", self.lang))]

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
        name = raw.strip()[:120]
        if name.lower() in {"skip", "none", "-"}:
            customer = self.conv.customer or self._customer_by_wa()
            name = customer.name if customer else f"WhatsApp +{self.conv.wa_id}"
        return self._create_lead(name)

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

    def _create_lead(self, name: str) -> list[dict]:
        ctx = self.conv.context
        reg = ctx.get("reg") or "TBC"
        service = ctx.get("service") or "Panel Beating & Spray Painting"
        damage = ctx.get("damage") or "See WhatsApp conversation"
        # The booking flow asks which day suits the customer. That answer used to be
        # collected and then discarded, so every WhatsApp booking landed on tomorrow.
        booked_for = self._parse_book_date(ctx.get("book_date"))

        customer = self.conv.customer or self._customer_by_wa()
        if not customer:
            customer = Customer(
                name=name, phone=f"+{self.conv.wa_id}", whatsapp=f"+{self.conv.wa_id}",
                notes="Created by WhatsApp bot", whatsapp_opt_in=True,
            )
            db.session.add(customer)
            db.session.flush()
            self.conv.customer_id = customer.id
        elif name and customer.name.startswith("WhatsApp +"):
            customer.name = name

        notes = f"Auto-created from WhatsApp.\n{damage}"
        if booked_for:
            notes += f"\nPreferred day: {booked_for.isoformat()}"

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
            slot_time=None,
            status="REQUESTED",
            source="whatsapp",
            notes=notes,
            quoted_from=Decimal(str(quick_quote(service)["from_price"])),
        )
        db.session.add(booking)
        self.conv.state = "MAIN_MENU"
        self.conv.ctx_clear("reg", "service", "damage", "book_date")
        db.session.commit()

        estimate_note = ""
        if service in {"Car Detailing", "Ceramic Coating", "Paint Protection Film",
                       "Car Vinyl Wrapping"}:
            estimate_note = f"\nIndicative price: *from USD {booking.quoted_from:.0f}*."
        day_note = (f"*Preferred day:* {booked_for.strftime('%a %d %b %Y')}\n"
                    if booked_for else "")
        return [
            text(
                f"✅ Request logged, {customer.name.split()[0]}.\n\n"
                f"*Reference:* {booking.reference}\n"
                f"*Vehicle:* {reg}\n"
                f"*Service:* {service}{estimate_note}\n"
                f"{day_note}\n"
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
        bar_filled = round(progress / 10)
        bar = "▰" * bar_filled + "▱" * (10 - bar_filled)
        lines = [
            f"*Job card {job.job_no}*",
            f"🚗 {job.vehicle.title if job.vehicle else 'Vehicle'} ({job.vehicle.reg_no if job.vehicle else '-'})",
            "",
            f"*Stage:* {STAGE_LABELS.get(job.stage, job.stage)}",
            f"{bar} {progress}%",
            "",
            STAGE_CUSTOMER_TEXT.get(job.stage, ""),
        ]
        blocking = [p for p in job.job_parts if p.is_blocking]
        if blocking:
            lines += ["", f"⚠️ Waiting on {len(blocking)} part(s): "
                          + ", ".join(p.description for p in blocking[:3])]
        if job.promised_date:
            lines += ["", f"📅 Promised date: {job.promised_date.strftime('%d %b %Y')}"]
        if job.stage == "READY":
            invoice = job.outstanding_invoice
            if invoice and invoice.balance > 0:
                lines += ["", f"💰 Balance due: *{invoice.currency} {invoice.balance:,.2f}*"]
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
        today = date.today()
        rows = []
        for offset in range(1, 7):
            day = today + timedelta(days=offset)
            rows.append({
                "id": f"day:{day.isoformat()}",
                "title": day.strftime("%a %d %b"),
                "description": "Available" if day.weekday() < 5 else "Limited (Saturday)",
            })
        return [{"type": "list", "body": f"{service} — which day suits you?",
                 "button": "Choose day", "sections": [{"title": "Next 6 days", "rows": rows}]}]

    def _input_book_date(self, raw: str) -> list[dict]:
        self.conv.ctx_set(book_date=raw.strip()[:20])
        self.conv.state = "BOOK_CONTACT"
        db.session.commit()
        return [text(
            "Almost done — please send your *name* and a contact number (or type *skip* to use "
            "this WhatsApp number)."
        )]

    def _input_book_contact(self, raw: str) -> list[dict]:
        name = raw.strip()[:120]
        if name.lower() in {"skip", "none", "-"}:
            customer = self.conv.customer or self._customer_by_wa()
            name = customer.name if customer else f"WhatsApp +{self.conv.wa_id}"
        return self._create_lead(name)

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
    app=None,
) -> list[dict]:
    """Run one conversational turn for an inbound message."""
    return IntentRouter(conversation, app=app).handle(
        text_body=text_body, interactive_id=interactive_id, media_url=media_url,
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
