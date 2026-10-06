"""Money parsing and formatting - exact paise arithmetic.

Amounts stored as signed integer paise.
Input: strings with optional ₹, commas, up to 2 decimal places.
Output: "₹X.YY" format.
"""

import re

from .errors import BudgetError


# Regex for parsing money strings
MAX_AMOUNT = 99999999999
_MONEY_RE = re.compile(
    r"^(?:₹\s*)?(?P<amount>(?:[0-9]+|[1-9][0-9]{0,2}(?:,[0-9]{3})+|"
    r"[1-9][0-9]?(?:,[0-9]{2})*,[0-9]{3})(?:\.[0-9]{1,2})?)$"
)


def parse_money(value: str, allow_zero: bool = False) -> int:
    """Convert amount string to integer paise.
    
    Args:
        value: String amount (e.g., "100", "₹100.50", "1,000")
        allow_zero: Whether zero is acceptable (default False for opening)
    
    Returns:
        int: Amount in paise (rupees * 100)
    
    Raises:
        BudgetError: If value is invalid (wrong type, format, too many decimals, etc.)
    """
    # Reject non-string types: bool, float, int, None
    if isinstance(value, bool):
        raise BudgetError("invalid_amount", "Boolean not valid for amount")
    if isinstance(value, float):
        raise BudgetError("invalid_amount", "Float not valid for amount")
    if not isinstance(value, str):
        raise BudgetError("invalid_amount", "Amount must be string")
    
    value = value.strip()
    
    if not value:
        raise BudgetError("invalid_amount", "Empty amount")
    
    # Check for exponent notation
    if "e" in value.lower():
        raise BudgetError("invalid_amount", "Exponential notation not allowed")
    
    # Check for inf/nan
    lowered = value.lower()
    if "inf" in lowered or "nan" in lowered:
        raise BudgetError("invalid_amount", "Non-finite values not allowed")
    
    # Parse with regex
    match = _MONEY_RE.match(value)
    if not match:
        raise BudgetError("invalid_amount", f"Invalid amount format: {value}")
    
    amount_str = match.group("amount")
    
    # Remove commas (thousand separators)
    amount_str = amount_str.replace(",", "")
    
    whole, _, fraction = amount_str.partition(".")
    if len(whole.lstrip("0")) > 9:
        raise BudgetError("invalid_amount", "Amount exceeds INR 999999999.99")
    paise = int(whole) * 100 + int(fraction.ljust(2, "0") or "0")
    if paise > MAX_AMOUNT:
        raise BudgetError("invalid_amount", "Amount exceeds INR 999999999.99")
    
    # Check zero
    if paise == 0 and not allow_zero:
        raise BudgetError("invalid_amount", "Zero amount not allowed")
    
    return paise


def format_money(paise: int) -> str:
    """Format paise as ₹X.YY string.
    
    Args:
        paise: Amount in paise (can be negative)
    
    Returns:
        str: Formatted amount with ₹ symbol, negative sign if applicable,
             and exactly 2 decimal places.
    """
    sign = "-" if paise < 0 else ""
    abs_paise = abs(paise)
    rupees = abs_paise // 100
    remaining_paise = abs_paise % 100
    return f"₹{sign}{rupees}.{remaining_paise:02d}"
