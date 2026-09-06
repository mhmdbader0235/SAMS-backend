"""Locale validators: timezone must be a real IANA zone, currency a real ISO-4217 code."""

import pytest
from pydantic import ValidationError

from app.core.schemas import SchoolProfileUpdateRequest, UserPreferencesUpdateRequest
from app.domains.school.locale import ISO_4217_MINOR_UNITS, is_valid_currency, minor_units_for


def test_unknown_timezone_rejected():
    with pytest.raises(ValidationError):
        SchoolProfileUpdateRequest(timezone="Not/AZone")


def test_known_timezone_accepted():
    req = SchoolProfileUpdateRequest(timezone="Asia/Amman")
    assert req.timezone == "Asia/Amman"


def test_unknown_currency_rejected():
    with pytest.raises(ValidationError):
        SchoolProfileUpdateRequest(currency="XYZ")


def test_known_currency_accepted_and_uppercased():
    req = SchoolProfileUpdateRequest(currency="jod")
    assert req.currency == "JOD"


def test_none_values_pass_through():
    req = SchoolProfileUpdateRequest()
    assert req.timezone is None
    assert req.currency is None


def test_user_preferences_timezone_validated():
    with pytest.raises(ValidationError):
        UserPreferencesUpdateRequest(preferred_timezone="Not/AZone")
    req = UserPreferencesUpdateRequest(preferred_timezone="America/New_York")
    assert req.preferred_timezone == "America/New_York"


def test_minor_units_table_has_named_exceptions():
    assert ISO_4217_MINOR_UNITS["JOD"] == 3
    assert ISO_4217_MINOR_UNITS["JPY"] == 0
    assert ISO_4217_MINOR_UNITS["KWD"] == 3
    assert ISO_4217_MINOR_UNITS["BHD"] == 3
    assert ISO_4217_MINOR_UNITS["USD"] == 2
    assert ISO_4217_MINOR_UNITS["EUR"] == 2
    assert ISO_4217_MINOR_UNITS["GBP"] == 2
    assert ISO_4217_MINOR_UNITS["CLF"] == 4


def test_is_valid_currency_helper():
    assert is_valid_currency("usd") is True
    assert is_valid_currency("XYZ") is False
    assert is_valid_currency(None) is False


def test_minor_units_for_helper():
    assert minor_units_for("jod") == 3
