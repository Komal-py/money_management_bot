from datetime import date, datetime, timezone

import pytest

from budget_bot.domain import dates
from budget_bot.domain.errors import BudgetError


def test_dates_use_trusted_receipt_timezone_and_reject_ambiguous_dates():
    received = datetime(2026, 1, 31, 20, tzinfo=timezone.utc)
    assert dates.local_today(received, "Asia/Kolkata") == date(2026, 2, 1)
    assert dates.resolve_date(None, received, "Asia/Kolkata") == date(2026, 2, 1)
    assert dates.resolve_date("yesterday", received, "Asia/Kolkata") == date(2026, 1, 31)
    assert dates.resolve_date("2024-02-29", received, "UTC") == date(2024, 2, 29)
    for expression in ("03/04", "2025-02-29", "2026-2-01", 1):
        with pytest.raises(BudgetError):
            dates.resolve_date(expression, received, "UTC")
    with pytest.raises(BudgetError):
        dates.local_today(received.replace(tzinfo=None), "UTC")
    with pytest.raises(BudgetError):
        dates.local_today(received, "Not/AZone")


def test_yesterday_before_supported_calendar_is_a_domain_error():
    with pytest.raises(BudgetError) as exc:
        dates.resolve_date("yesterday", datetime.min.replace(tzinfo=timezone.utc), "UTC")
    assert exc.value.code == "invalid_date"


@pytest.mark.parametrize("received,zone", [
    (datetime.min.replace(tzinfo=timezone.utc), "America/New_York"),
    (datetime.max.replace(tzinfo=timezone.utc), "Asia/Kolkata"),
])
def test_timezone_conversion_outside_calendar_is_a_domain_error(received, zone):
    with pytest.raises(BudgetError) as exc:
        dates.local_today(received, zone)
    assert exc.value.code == "invalid_date"
