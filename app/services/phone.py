"""Phone numbers: the one place that decides what a dialled number actually is.

Every outbound WhatsApp message is addressed by normalising a number somebody
typed, so the country code used for a local number is the difference between a
message that arrives and one that silently goes nowhere. It used to be hard-coded
``"263"`` in two separate files (``whatsapp_client`` and ``Customer.wa_number``)
which could — and did — drift apart: one knew about the ``00`` international
prefix and the other did not.

Now both read this module, and this module reads ``DEFAULT_COUNTRY_CODE`` from
the app config. Change that setting and the desk's phone fields, the number
stored on a customer, and the number the bot dials all move together.

Rules, in order — they are what a person actually types:

===========================  =========================  ==========================
Typed                        Becomes                    Why
===========================  =========================  ==========================
``+263 77 555 0555``         ``263775550555``           already international
``00263775550555``           ``263775550555``           ``00`` is the access code
``0775550555``               ``263775550555``           ``0`` is the trunk prefix
``775550555``                ``263775550555``           a bare national number
``+44 7911 123456``          ``447911123456``           left alone — not ours
===========================  =========================  ==========================
"""
from __future__ import annotations

import re

from ..constants import (
    COUNTRIES,
    COUNTRY_CODES,
    COUNTRY_NAMES,
    DEFAULT_COUNTRY_CODE,
)

# A national number this long or shorter, with no international prefix and no
# leading zero, is assumed to be local rather than foreign. Zimbabwe's national
# number is 9 digits; anything longer that does not start with a known code is
# left exactly as typed, because guessing would be worse than passing it through.
NATIONAL_MAX_DIGITS = 9


def dial_codes() -> list[dict]:
    """The dropdown list: code, ISO country and name, most-used first."""
    return [dict(c) for c in COUNTRIES]


def default_country_code() -> str:
    """The configured dial code, or the constant if there is no app context.

    Read from config rather than baked in so a shop outside Zimbabwe changes one
    environment variable instead of hunting for a literal. An unknown value falls
    back rather than being trusted: a typo in ``.env`` must not make every
    outbound number dial a country that does not exist.
    """
    try:
        from flask import current_app

        configured = str(current_app.config.get("DEFAULT_COUNTRY_CODE") or "").strip()
    except (ImportError, RuntimeError):  # no app context (script, some tests)
        configured = ""
    configured = re.sub(r"\D", "", configured)
    return configured if configured in COUNTRY_NAMES else DEFAULT_COUNTRY_CODE


def country_name(code: str) -> str:
    return COUNTRY_NAMES.get(re.sub(r"\D", "", code or ""), "")


def normalise_msisdn(raw: str | None, *, default_cc: str | None = None) -> str:
    """A phone number as bare international digits, e.g. ``263775550555``.

    Returns ``""`` for nothing usable rather than raising: this runs inside the
    send path and inside a model property, and a blank number is a customer with
    no number on file, not a crash.
    """
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    digits = re.sub(r"\D", "", text)
    if not digits:
        return ""

    cc = default_cc or default_country_code()

    # A leading "+" is the customer telling us the country outright — trust it.
    if text.startswith("+"):
        return digits
    # "00" is the international access code, so what follows is already absolute.
    if text.startswith("00"):
        return digits[2:]
    # A single leading zero is the national trunk prefix, not part of the number:
    # 0775550555 is 263 775550555. "+0..." cannot reach here, it is handled above.
    # A lone "0" is somebody who typed the prefix and stopped — that is nothing,
    # not the country code on its own.
    if digits.startswith("0"):
        rest = digits[1:]
        return cc + rest if rest else ""
    if digits.startswith(cc):
        return digits
    if len(digits) <= NATIONAL_MAX_DIGITS:
        return cc + digits
    return digits


def split_msisdn(raw: str | None, *, default_cc: str | None = None) -> dict:
    """A number separated into its country code and national part, for an input.

    The desk's phone fields ask for the code and the number separately, so what is
    stored (``+263775550555``) has to be taken apart again when the form opens for
    editing — otherwise the operator sees the country code repeated in the box and
    the dropdown on the wrong country.
    """
    cc = default_cc or default_country_code()
    text = str(raw or "").strip()
    digits = re.sub(r"\D", "", text)
    if not digits:
        return {"country_code": cc, "national": ""}

    explicit = text.startswith("+") or text.startswith("00")
    body = digits[2:] if text.startswith("00") else digits

    if explicit:
        # Longest first, so 263 wins over 26 and 267 is not read as 26.
        for code in sorted(COUNTRY_CODES, key=len, reverse=True):
            if body.startswith(code):
                return {"country_code": code, "national": body[len(code):]}
        return {"country_code": cc, "national": body}

    if body.startswith("0"):
        return {"country_code": cc, "national": body.lstrip("0")}

    for code in sorted(COUNTRY_CODES, key=len, reverse=True):
        # The remainder has to look like a real national number, or "1" would
        # swallow the front of any number beginning with a 1.
        if body.startswith(code) and len(body) - len(code) >= 5:
            return {"country_code": code, "national": body[len(code):]}

    return {"country_code": cc, "national": body}


def format_msisdn(raw: str | None, *, default_cc: str | None = None) -> str:
    """A number in the compact international form we store: ``+263775550555``.

    Deliberately not prettier than that. National grouping rules differ per
    country, and a wrong guess (Zimbabwe's ``77 555 0555`` applied to a UK number)
    reads as a data-entry mistake rather than a formatting one. It also keeps the
    column searchable: the desk's customer search is a substring match, and
    ``+263 775550555`` would not be found by somebody typing the digits straight
    through.
    """
    digits = normalise_msisdn(raw, default_cc=default_cc)
    return f"+{digits}" if digits else ""


def is_dialable(raw: str | None, *, default_cc: str | None = None) -> bool:
    """Whether this could plausibly reach a phone at all.

    WhatsApp wants 7 to 15 digits internationally, and the bot must not attempt a
    send to a half-typed number: the failure is a rejected API call, which reads
    to the desk as "the system is broken".
    """
    digits = normalise_msisdn(raw, default_cc=default_cc)
    return 7 <= len(digits) <= 15


__all__ = [
    "dial_codes",
    "default_country_code",
    "country_name",
    "normalise_msisdn",
    "split_msisdn",
    "format_msisdn",
    "is_dialable",
]
