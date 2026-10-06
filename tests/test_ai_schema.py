import pytest
from budget_bot.ai.schemas import Interpretation, interpretation_schema
from pydantic import ValidationError


def payload(actions=None, **kw):
    return dict(schema_version=1, kind='mutation', actions=actions or [], missing_fields=[],
                clarification_question=None, query=None, **kw)


@pytest.mark.parametrize('action', [
    {'type': 'income', 'amount_inr': '1000', 'description': 'Salary', 'date_expression': None},
    {'type': 'allocate', 'amount_inr': '500', 'bucket_name': 'Travel'},
    {'type': 'transfer', 'amount_inr': '20', 'source_bucket': 'Travel', 'destination_bucket': 'Food'},
    {'type': 'expense', 'amount_inr': '20.50', 'bucket_name': 'Travel', 'description': 'Metro', 'date_expression': 'yesterday'},
    {'type': 'create_bucket', 'name': 'Travel'},
    {'type': 'undo', 'last': True, 'reference': None},
    {'type': 'correct', 'reference': 'metro yesterday', 'changes': {'amount_inr': '30'}},
    {'type': 'set_target', 'bucket_name': 'Travel', 'amount_inr': '2000', 'remove': False},
])
def test_all_supported_actions(action):
    assert Interpretation.model_validate(payload([action])).actions[0].type == action['type']


@pytest.mark.parametrize('report', ['balances', 'spending', 'calendar'])
@pytest.mark.parametrize('period', ['today', 'week', 'month', 'range'])
def test_all_queries(report, period):
    value = payload()
    value.update(kind='query', query=dict(report=report, period=period,
                 start='2026-10-01' if period == 'range' else None,
                 end='2026-10-06' if period == 'range' else None, bucket_name=None))
    assert Interpretation.model_validate(value).query.report == report


@pytest.mark.parametrize('action', [
    {'type': 'opening', 'amount_inr': '500'},
    {'type': 'income', 'amount_inr': 1.5, 'description': 'Salary'},
    {'type': 'income', 'amount_inr': '1e3', 'description': 'Salary'},
    {'type': 'undo', 'transaction_id': 'invented'},
    {'type': 'income', 'amount_inr': '10', 'description': 'Salary', 'owner_id': 'victim'},
])
def test_unsafe_actions_rejected(action):
    with pytest.raises(ValidationError):
        Interpretation.model_validate(payload([action]))


def test_schema_is_strict_recursively():
    def check(node):
        if isinstance(node, dict):
            if node.get('type') == 'object':
                assert node['additionalProperties'] is False
                assert set(node['required']) == set(node['properties'])
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)
    check(interpretation_schema())


def test_kind_consistency_and_batch_bound():
    for value in [payload(), payload([{'type': 'create_bucket', 'name': 'A'}] * 9),
                  dict(payload(), kind='query')]:
        with pytest.raises(ValidationError):
            Interpretation.model_validate(value)


@pytest.mark.parametrize('version', [True, 1.0, '1'])
def test_version_requires_literal_integer(version):
    value = payload([{'type': 'create_bucket', 'name': 'Travel'}])
    value['schema_version'] = version
    with pytest.raises(ValidationError):
        Interpretation.model_validate(value)


@pytest.mark.parametrize('action', [
    {'type': 'create_bucket', 'name': '   '},
    {'type': 'expense', 'amount_inr': '10', 'bucket_name': 'Travel', 'description': '  '},
    {'type': 'expense', 'amount_inr': '10', 'bucket_name': 'Travel', 'description': 'Metro',
     'date_expression': '2026-02-30'},
])
def test_blank_labels_and_impossible_dates_rejected(action):
    with pytest.raises(ValidationError):
        Interpretation.model_validate(payload([action]))


def test_query_range_requires_dashed_iso_dates():
    value = dict(payload(), kind='query', query=dict(report='spending', period='range',
                                                    start='20261001', end='20261006'))
    with pytest.raises(ValidationError):
        Interpretation.model_validate(value)
