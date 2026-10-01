"""Phone numbers: one country code, shared by the desk and the WhatsApp bot.

The bug these guard against is silent. A customer saved as ``0775550555`` looks
perfectly correct on screen, and every message the bot tries to send to it is
rejected — the desk sees no error, only a customer who never replies.
"""
from __future__ import annotations

from app.services import phone


# ── the rules ────────────────────────────────────────────────────────────────
def test_the_ways_a_zimbabwean_number_gets_typed_all_land_on_one_number():
    """Every spelling of the same number must normalise identically.

    This is what the bot dials, so two spellings producing two different strings
    is two different customers as far as WhatsApp is concerned.
    """
    expected = "263775550555"
    for typed in ("+263 77 555 0555", "0775550555", "077 555 0555",
                  "00263775550555", "263775550555", "775550555"):
        assert phone.normalise_msisdn(typed) == expected, typed


def test_a_foreign_number_is_left_exactly_as_given():
    """A leading plus is the customer telling us the country — do not 'fix' it.

    Re-homing a UK number under +263 would send the message nowhere, and the
    customer would never know it had been attempted.
    """
    assert phone.normalise_msisdn("+44 7911 123456") == "447911123456"
    assert phone.normalise_msisdn("+1 415 555 0132") == "14155550132"
    assert phone.normalise_msisdn("+27 79 112 3456") == "27791123456"


def test_a_bare_national_number_is_assumed_local():
    """Nine digits with no prefix is how people write it without the zero."""
    assert phone.normalise_msisdn("775550555") == "263775550555"


def test_nothing_usable_normalises_to_nothing_rather_than_raising():
    """It runs in the send path and in a model property — a blank number is a
    customer with no number on file, not a crash."""
    for junk in (None, "", "   ", "not a number", "0", "+"):
        assert phone.normalise_msisdn(junk) == "", junk


def test_the_country_code_comes_from_config(app):
    """A shop outside Zimbabwe changes one setting, not a literal in two files.

    Both the desk's phone fields and the bot's dialling read this, which is the
    whole point — they used to be separate hard-coded "263"s that could drift.
    """
    assert phone.default_country_code() == "263"

    app.config["DEFAULT_COUNTRY_CODE"] = "27"
    try:
        assert phone.default_country_code() == "27"
        # A South African number written without its code is now dialled as one.
        assert phone.normalise_msisdn("0791123456") == "27791123456"
        assert phone.format_msisdn("0791123456") == "+27791123456"
    finally:
        app.config["DEFAULT_COUNTRY_CODE"] = "263"


def test_a_typo_in_the_setting_falls_back_instead_of_dialling_nowhere(app):
    """An unknown code must not become the code every number is dialled with."""
    app.config["DEFAULT_COUNTRY_CODE"] = "9999"
    try:
        assert phone.default_country_code() == "263"
    finally:
        app.config["DEFAULT_COUNTRY_CODE"] = "263"


def test_splitting_rejoins_to_the_same_number():
    """The desk's fields take a number apart and put it back together on save.

    If those two disagree, opening a customer and pressing Save quietly rewrites
    their number to something else.
    """
    for stored in ("+263775550555", "0775550555", "00263775550555",
                   "+447911123456", "2779112345"):
        parts = phone.split_msisdn(stored)
        rejoined = phone.normalise_msisdn(f"+{parts['country_code']}{parts['national']}")
        assert rejoined == phone.normalise_msisdn(stored), stored


def test_a_longer_country_code_is_matched_before_a_shorter_one():
    """263 must not be read as Zambia's 260 or South Africa's 27.

    Longest-first matching is the difference between the right customer and a
    number that gains or loses a digit.
    """
    assert phone.split_msisdn("263775550555")["country_code"] == "263"
    assert phone.split_msisdn("260977555555")["country_code"] == "260"
    assert phone.split_msisdn("2779112345")["country_code"] == "27"
    # "1" is a real code, but it must not swallow the front of any 1-prefixed
    # national number.
    assert phone.split_msisdn("0775550555")["country_code"] == "263"


def test_a_half_typed_number_is_not_dialable():
    """The bot must not attempt a send it knows will be rejected."""
    assert phone.is_dialable("0775550555") is True
    assert phone.is_dialable("+447911123456") is True
    assert phone.is_dialable("0775") is False
    assert phone.is_dialable("") is False
    assert phone.is_dialable("12345678901234567890") is False


def test_the_default_code_is_first_in_the_dropdown():
    """The first entry is what the UI falls back to, so it must be the default."""
    codes = phone.dial_codes()
    assert codes[0]["iso"] == "ZW"
    assert codes[0]["code"] == phone.default_country_code()
    assert all({"code", "iso", "name"} <= set(c) for c in codes)
