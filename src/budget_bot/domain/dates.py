"""Bookkeeping dates resolve only against a trusted aware receipt timestamp."""

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import BudgetError


def local_today(received_at: datetime, timezone: str) -> date:
    if not isinstance(received_at, datetime) or received_at.utcoffset() is None:
        raise BudgetError("invalid_date", "Receipt timestamp must be timezone-aware")
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        raise BudgetError("invalid_timezone", "Unknown timezone") from None
    return received_at.astimezone(zone).date()


def resolve_date(expression, received_at: datetime, timezone: str) -> date:
    today = local_today(received_at, timezone)
    if expression is None or expression == "today":
        return today
    if expression == "yesterday":
        return today - timedelta(days=1)
    if isinstance(expression, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", expression):
        try:
            return date.fromisoformat(expression)
        except ValueError:
            pass
    raise BudgetError("invalid_date", "Use today, yesterday, or an ISO YYYY-MM-DD date")
