"""Tests for domain.money module - TDD cycle evidence."""

import pytest
from hypothesis import given, settings, strategies as st
from budget_bot.domain.money import parse_money, format_money
from budget_bot.domain.errors import BudgetError


# ============================================================================
# TEST: parse_money - Basic valid amounts
# ============================================================================
def test_parse_money_valid_whole_rupees():
    """Whole rupee amounts convert to paise correctly."""
    assert parse_money("100") == 10000  # ₹100 = 10000 paise
    assert parse_money("0", allow_zero=True) == 0
    assert parse_money("1") == 100
    assert parse_money("999999") == 99999900


def test_parse_money_valid_with_decimal():
    """Decimal amounts convert correctly with 2 decimal precision."""
    assert parse_money("100.50") == 10050
    assert parse_money("0.01") == 1
    assert parse_money("0.99") == 99
    assert parse_money("123.45") == 12345


def test_parse_money_valid_with_rupees_symbol():
    """Amounts with ₹ symbol parse correctly."""
    assert parse_money("₹100") == 10000
    assert parse_money("₹ 100.50") == 10050
    assert parse_money("₹1000") == 100000


def test_parse_money_valid_with_commas():
    """Amounts with Indian comma format parse correctly."""
    assert parse_money("1,000") == 100000
    assert parse_money("10,000") == 1000000
    assert parse_money("1,00,000") == 10000000
    assert parse_money("10,00,000.50") == 100000050


def test_parse_money_strips_whitespace():
    """Whitespace around amount is stripped."""
    assert parse_money("  100  ") == 10000
    assert parse_money("  ₹ 1,000.50 ") == 100050


# ============================================================================
# TEST: parse_money - Rejections per spec
# ============================================================================
def test_parse_money_rejects_bool():
    """Boolean values are rejected."""
    with pytest.raises(BudgetError) as exc:
        parse_money(True)
    assert exc.value.code == "invalid_amount"


def test_parse_money_rejects_float():
    """Float values are rejected (avoid precision issues)."""
    with pytest.raises(BudgetError) as exc:
        parse_money(100.50)
    assert exc.value.code == "invalid_amount"


def test_parse_money_rejects_exponent_notation():
    """Scientific/exponent notation rejected."""
    with pytest.raises(BudgetError) as exc:
        parse_money("1e3")
    assert exc.value.code == "invalid_amount"


def test_parse_money_rejects_nonfinite():
    """inf/nan rejected."""
    with pytest.raises(BudgetError) as exc:
        parse_money("infinity")
    with pytest.raises(BudgetError) as exc:
        parse_money("nan")
    assert exc.value.code == "invalid_amount"


def test_parse_money_rejects_more_than_two_decimals():
    """More than 2 decimal places rejected."""
    with pytest.raises(BudgetError) as exc:
        parse_money("100.123")
    assert exc.value.code == "invalid_amount"


def test_parse_money_rejects_negative():
    """Negative amounts rejected (expense indicates direction)."""
    with pytest.raises(BudgetError) as exc:
        parse_money("-100")
    assert exc.value.code == "invalid_amount"


def test_parse_money_rejects_zero_when_not_allowed():
    """Zero rejected when allow_zero=False."""
    with pytest.raises(BudgetError) as exc:
        parse_money("0", allow_zero=False)
    assert exc.value.code == "invalid_amount"


def test_parse_money_allows_zero_when_allowed():
    """Zero allowed when allow_zero=True."""
    assert parse_money("0", allow_zero=True) == 0


def test_parse_money_rejects_empty():
    """Empty string rejected."""
    with pytest.raises(BudgetError) as exc:
        parse_money("")
    assert exc.value.code == "invalid_amount"


def test_parse_money_rejects_invalid_chars():
    """Non-numeric characters rejected."""
    with pytest.raises(BudgetError) as exc:
        parse_money("abc")
    assert exc.value.code == "invalid_amount"


# ============================================================================
# TEST: format_money
# ============================================================================
def test_format_money_basic():
    """Paise formats to ₹ with 2 decimals."""
    assert format_money(10000) == "₹100.00"
    assert format_money(10050) == "₹100.50"
    assert format_money(1) == "₹0.01"
    assert format_money(0) == "₹0.00"
    assert format_money(1234567) == "₹12345.67"


def test_format_money_negative():
    """Negative paise formats with negative sign."""
    assert format_money(-10000) == "₹-100.00"
    assert format_money(-50) == "₹-0.50"


def test_money_validates_grouping_and_supported_range_without_rounding():
    for value in ("1,,000", "12,34", "₹₹1", "1000000000", "999999999.999"):
        with pytest.raises(BudgetError):
            parse_money(value)
    assert parse_money("999999999.99") == 99999999999
    assert parse_money("99,99,99,999.99") == 99999999999


@pytest.mark.parametrize("suffix,expected", [("1.01", 101), ("0", 0)])
def test_leading_zero_padding_does_not_escape_exact_money_parser(suffix, expected):
    assert parse_money("0" * 5000 + suffix, allow_zero=True) == expected


@settings(max_examples=100, derandomize=True, database=None)
@given(paise=st.integers(0, 99999999999))
def test_supported_amounts_round_trip_without_rounding(paise):
    assert parse_money(format_money(paise), allow_zero=True) == paise


@pytest.mark.parametrize("value", ["1000000000.00", "1,000,000,000.00", "1,00,00,00,000.00", "0" * 5000 + "1000000000"])
def test_money_limit_rejects_one_paise_above_max_in_all_supported_groupings(value):
    with pytest.raises(BudgetError) as exc:
        parse_money(value)
    assert exc.value.code == "invalid_amount"
