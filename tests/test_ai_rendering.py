"""Render actual planner output, not fabricated model financial facts."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from budget_bot.ai import render_balances, render_result, render_review
from budget_bot.domain.planner import plan

NOW = datetime(2026, 10, 6, 20, tzinfo=timezone.utc)


def snapshot():
    return dict(owner_id='private-owner', revision=1, onboarded=True, timezone='Asia/Kolkata',
                pool=100000, opening_date='2026-10-01', transactions=[], targets=[], buckets={
                    'Travel': dict(id='private-travel', balance=10000, target=20000),
                    'Food': dict(id='private-food', balance=50000, target=None)})


def review_for(actions, state=None):
    planned = plan(state or snapshot(), actions, NOW)
    return dict(status='pending', request_id='private-request', owner_id='private-owner',
                revision=1, plan=planned, actions=actions, expires_at='2026-10-06T20:30:00+00:00')


def test_expense_review_exact_labels_local_date_projected_and_warnings():
    review = review_for([dict(type='expense', amount_inr='400.50', description='Metro',
                              bucket_name='Travel', date_expression='yesterday')])
    original = deepcopy(review)
    text = render_review(review)
    for expected in ['Expense', 'Amount: INR 400.50', 'Description: Metro', 'Bucket: Travel',
                     'Date: 2026-10-06', 'Timezone: Asia/Kolkata', 'Projected balances',
                     'Travel: INR -300.50', 'Confirm', 'Edit', 'Cancel', '2026-10-07 02:00']:
        assert expected in text
    for warning in review['plan']['warnings']:
        assert warning in text
    assert 'private-' not in text and 'None' not in text and 'null' not in text and '{' not in text
    assert review == original
    assert render_review(review) == text


def test_ordered_directions_and_committed_remaining_balances():
    review = review_for([
        dict(type='income', amount_inr='100', description='Gift'),
        dict(type='allocate', amount_inr='200', bucket_name='Travel'),
        dict(type='transfer', amount_inr='50', source_bucket='Food', destination_bucket='Travel'),
        dict(type='expense', amount_inr='10', bucket_name='Travel', description='Bus'),
    ])
    text = render_review(review)
    assert text.index('1. Income') < text.index('2. Allocation') < text.index('3. Transfer') < text.index('4. Expense')
    for direction in ['New income → Pool', 'Pool → Travel', 'Food → Travel', 'Travel → Spending']:
        assert direction in text
    result = dict(status='committed', snapshot=review['plan']['snapshot'],
                  warnings=['A backend warning'], summary=review['plan']['summary'])
    committed = render_result(result)
    assert 'Recorded' in committed and 'Travel: INR 340.00' in committed
    assert 'A backend warning' in committed and 'Projected' not in committed
    assert 'None' not in committed and '{' not in committed


def test_balances_stable_order_and_no_internal_dump():
    state = snapshot()
    text = render_balances(state)
    assert 'Pool: INR 1000.00' in text
    assert text.index('Food: INR 500.00') < text.index('Travel: INR 100.00')
    assert 'Total virtual money: INR 1600.00' in text
    assert 'Monthly target: INR 200.00' in text
    assert 'Timezone: Asia/Kolkata' in text
    assert all(token not in text for token in ['private-', 'None', 'null', '{', 'revision'])


def test_committed_result_labels_transaction_facts_without_unrelated_history():
    review = review_for([dict(type='expense', amount_inr='40', description='Metro', bucket_name='Travel')])
    state = review['plan']['snapshot']
    batch = state['transactions'][0]['batch_id']
    state['transactions'].append(dict(state['transactions'][0], batch_id='unrelated-batch', description='Unrelated'))
    text = render_result(dict(status='committed', batch_id=batch, snapshot=state,
                              summary=review['plan']['summary'], warnings=review['plan']['warnings']))
    for expected in ['Description: Metro', 'Amount: INR 40.00', 'Bucket: Travel',
                     'Date: 2026-10-07', 'Direction: Travel → Spending']:
        assert expected in text
    assert 'Unrelated' not in text and batch not in text


def test_committed_correction_resolves_backend_summary_reference_only():
    initial = review_for([dict(type='expense', amount_inr='40', description='Metro', bucket_name='Travel')])
    state = initial['plan']['snapshot']
    identifier = state['transactions'][0]['id']
    corrected = review_for([dict(type='correct', transaction_id=identifier, changes={'amount_inr': '35'})], state)
    text = render_result(dict(status='committed', batch_id='new-correction-batch',
                              snapshot=corrected['plan']['snapshot'], summary=corrected['plan']['summary'], warnings=[]))
    assert 'Description: Metro' in text and 'Amount: INR 35.00' in text
    assert identifier not in text


def test_correction_and_undo_show_human_facts_not_authority_ids():
    initial = review_for([dict(type='expense', amount_inr='40', bucket_name='Travel', description='Metro')])
    state = initial['plan']['snapshot']
    identifier = state['transactions'][0]['id']
    corrected = review_for([dict(type='correct', transaction_id=identifier,
                                changes={'amount_inr': '35', 'bucket_name': 'Food'})], state)
    text = render_review(corrected)
    for expected in ['Correction', 'Before', 'After', 'INR 40.00', 'INR 35.00', 'Travel', 'Food', 'Metro']:
        assert expected in text
    assert identifier not in text
    undone = review_for([dict(type='undo', transaction_id=identifier)], state)
    text = render_review(undone)
    assert 'Undo' in text and 'Spending → Travel' in text and 'INR 40.00' in text
    assert identifier not in text


def test_opening_bucket_and_target_metadata_render_without_null():
    state = snapshot()
    state.update(onboarded=False, pool=0, buckets={})
    review = review_for([
        dict(type='opening', amount_inr='0'), dict(type='create_bucket', name='Travel'),
        dict(type='set_target', bucket_name='Travel', amount_inr='200'),
        dict(type='set_target', bucket_name='Travel', remove=True),
    ], state)
    text = render_review(review)
    for expected in ['Opening money', 'INR 0.00', 'Create bucket', 'Travel', 'Monthly target', 'INR 200.00', 'off']:
        assert expected in text
    assert 'None' not in text and 'null' not in text


@pytest.mark.parametrize('status,expected', [('cancelled', 'Cancelled'), ('expired', 'expired'), ('stale', 'changed')])
def test_noncommitted_result_never_claims_recorded(status, expected):
    result = {'status': status}
    if status == 'stale':
        result.update(review_for([dict(type='income', amount_inr='10', description='Gift')]))
        result['status'] = status
    text = render_result(result)
    assert expected in text and 'Recorded' not in text
