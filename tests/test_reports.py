from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from budget_bot.services.reports import ReportService


class StoreDouble:
    """Contract-shaped owner-scoped store; never a substitute for DB verification."""

    def __init__(self):
        self.calls = []
        self.snapshot = {
            'owner_id': 'owner-a', 'revision': 1, 'onboarded': True,
            'timezone': 'Asia/Kolkata', 'pool': 101, 'opening_date': '2024-01-01',
            'buckets': {
                'Travel': {'id': 'travel-id', 'balance': -1, 'target': 200},
                'Food': {'id': 'food-id', 'balance': 9007199254740993, 'target': None},
            }, 'transactions': [], 'targets': [],
        }
        self.rows = [
            self.row('a', '2024-03-01', 'Travel', 201, 'Metro'),
            self.row('b', '2024-03-01', 'Food', 99, 'Lunch'),
            self.row('c', '2024-03-01', 'Travel', 500, 'Undone', active=False),
            self.row('d', '2024-03-01', 'Travel', 500, 'Allocation', kind='allocate'),
        ]

    @staticmethod
    def row(identity, day, bucket, amount, description, active=True, kind='expense'):
        return {'id': identity, 'batch_id': 'batch', 'type': kind, 'amount': amount,
                'bucket': bucket, 'source': None, 'destination': None,
                'description': description, 'date': day, 'active': active, 'revision': 1}

    def get_snapshot(self, owner):
        self.calls.append(('snapshot', owner))
        return deepcopy(self.snapshot)

    def spending(self, owner, start, end, bucket_name=None):
        self.calls.append(('spending', owner, start, end, bucket_name))
        rows = [deepcopy(row) for row in self.rows if row['active'] and row['type'] == 'expense'
                and start.isoformat() <= row['date'] <= end.isoformat()
                and (bucket_name is None or row['bucket'] == bucket_name)]
        by_bucket = {}
        for row in rows:
            by_bucket[row['bucket']] = by_bucket.get(row['bucket'], 0) + row['amount']
        return {'start': start.isoformat(), 'end': end.isoformat(),
                'by_bucket': by_bucket, 'total': sum(by_bucket.values()),
                'count': len(rows), 'expenses': rows}


def test_balances_exact_sorted_targets_negative_and_no_mutations():
    store = StoreDouble()
    before = deepcopy(store.snapshot)
    text = ReportService(store).balances('owner-a')
    assert 'Unallocated pool: ₹1.01' in text
    assert 'Food: ₹90071992547409.93' in text
    assert 'Travel: -₹0.01' in text
    assert 'Monthly target: ₹2.00' in text
    assert 'negative' in text.lower()
    assert 'Total available: ₹90071992547410.93' in text
    assert text.index('Food:') < text.index('Travel:')
    assert store.calls == [('snapshot', 'owner-a')]
    assert store.snapshot == before


@pytest.mark.parametrize(('period', 'start', 'end'), [
    ('today', date(2024, 3, 1), date(2024, 3, 1)),
    ('week', date(2024, 2, 26), date(2024, 3, 3)),
    ('month', date(2024, 3, 1), date(2024, 3, 31)),
])
def test_receipt_anchor_and_periods(period, start, end):
    store = StoreDouble()
    receipt = datetime(2024, 2, 29, 20, tzinfo=timezone.utc)
    text = ReportService(store).spending('owner-a', period, receipt)
    assert ('spending', 'owner-a', start, end, None) in store.calls
    assert 'Total spent: ₹3.00' in text
    assert '2 expenses' in text
    assert 'Food: ₹0.99' in text and 'Travel: ₹2.01' in text
    assert 'Undone' not in text


def test_range_bucket_and_empty_day():
    store = StoreDouble()
    service = ReportService(store)
    text = service.spending('owner-a', 'range', datetime(2024, 3, 2, tzinfo=timezone.utc),
                            '2024-03-01', '2024-03-01', 'travel')
    assert 'Total spent: ₹2.01' in text
    assert ('spending', 'owner-a', date(2024, 3, 1), date(2024, 3, 1), 'Travel') in store.calls
    assert 'No active expenses' in service.day('owner-a', '2024-02-29')
    assert 'Total spent: ₹0.00' in service.day('owner-a', '2024-02-29')


@pytest.mark.parametrize(('period', 'start', 'end', 'bucket'), [
    ('year', None, None, None), ('range', '2024-03-02', '2024-03-01', None),
    ('range', None, '2024-03-01', None), ('today', None, None, 'Unknown'),
    ('range', '2024-2-01', '2024-03-01', None),
])
def test_invalid_report_input(period, start, end, bucket):
    store = StoreDouble()
    with pytest.raises(ValueError):
        ReportService(store).spending('owner-a', period,
            datetime(2024, 3, 1, tzinfo=timezone.utc), start, end, bucket)
    assert not any(call[0] == 'spending' for call in store.calls)


def test_naive_receipt_rejected():
    with pytest.raises(ValueError):
        ReportService(StoreDouble()).spending('owner-a', 'today', datetime(2024, 3, 1))


def test_current_month_target_warning_not_applied_to_historical_range():
    store = StoreDouble()
    service = ReportService(store)
    receipt = datetime(2024, 3, 2, tzinfo=timezone.utc)
    text = service.spending('owner-a', 'month', receipt)
    assert 'Warning: Travel monthly spending ₹2.01 reached or exceeded target ₹2.00.' in text
    store.snapshot['buckets']['Travel']['target'] = 201
    assert 'reached or exceeded' in service.spending('owner-a', 'month', receipt)
    assert 'target' not in service.spending('owner-a', 'range', receipt, '2024-03-01', '2024-03-01')


def test_timezone_week_crosses_year_and_does_not_use_wall_clock():
    store = StoreDouble()
    store.snapshot['timezone'] = 'America/Los_Angeles'
    ReportService(store).spending('owner-a', 'week', datetime(2024, 1, 1, 1, tzinfo=timezone.utc))
    assert ('spending', 'owner-a', date(2023, 12, 25), date(2023, 12, 31), None) in store.calls
