"""Additional fail-closed boundary regressions over the real SDK."""
import json
from datetime import datetime, timezone

import httpx
import pytest

from budget_bot.ai.provider import AIInterpreter
from budget_bot.ai.privacy import redact
from budget_bot.domain.errors import BudgetError
from test_ai_provider import NOW, client_for, expense, mutation, response


@pytest.mark.parametrize('text', ['Add 2,000.50 to Travel', 'Top up Travel by 200',
    'Put 100 into Travel', 'Add money to Travel from somewhere',
    'Received salary 500; add 100 to Travel'])
async def test_funding_ambiguity_cannot_be_overridden_by_model(monkeypatch, text):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response(mutation(dict(type='allocate', amount_inr='100', bucket_name='Travel'))))
    client = client_for(monkeypatch, handler)
    try:
        value = await client.interpret(text, ['Travel'], NOW, 'Asia/Kolkata')
        assert value['kind'] == 'clarification'
        assert value['missing_fields'] == ['funding_source']
        assert not calls
    finally:
        await client.close()


@pytest.mark.parametrize('body', [None, [], 'SECRET', {'status': 'completed', 'output': None}])
async def test_malformed_response_envelope_is_safe(monkeypatch, body):
    client = client_for(monkeypatch, lambda request: httpx.Response(200, json=body))
    try:
        with pytest.raises(BudgetError) as exc:
            await client.interpret('Spent 400 in Travel', ['Travel'], NOW, 'Asia/Kolkata')
        assert exc.value.code == 'invalid_model_output'
        assert 'SECRET' not in str(exc.value)
    finally:
        await client.close()


async def test_non_json_http_body_is_safe(monkeypatch):
    client = client_for(monkeypatch, lambda request: httpx.Response(
        200, content=b'SECRET not JSON', headers={'content-type': 'application/json'}))
    try:
        with pytest.raises(BudgetError) as exc:
            await client.interpret('Spent 400 in Travel', ['Travel'], NOW, 'Asia/Kolkata')
        assert exc.value.code == 'invalid_model_output'
        assert 'SECRET' not in str(exc.value)
    finally:
        await client.close()


@pytest.mark.parametrize('url', ['http://example.invalid/v1', 'https://user:pass@example.invalid',
    'https://example.invalid?key=secret', 'https://example.invalid/#secret'])
def test_only_clean_https_endpoints(url):
    with pytest.raises(BudgetError, match='HTTPS'):
        AIInterpreter(url, 'synthetic-test-key', 'test-model')


async def test_minimal_local_context_and_complete_query_contract(monkeypatch):
    seen = []
    value = dict(schema_version=1, kind='query', actions=[], missing_fields=[],
                 clarification_question=None, query=dict(report='balances', period='today',
                                                        start=None, end=None, bucket_name=None))
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=response(value))
    client = client_for(monkeypatch, handler)
    try:
        received = datetime(2026, 10, 6, 23, tzinfo=timezone.utc)
        result = await client.interpret('Balance', ['Travel'], received, 'Asia/Kolkata')
        context = json.loads(seen[0]['input'][0]['content'])
        assert context == dict(message='Balance', bucket_names=['Travel'], local_date='2026-10-07',
                               timezone='Asia/Kolkata')
        assert result == value
    finally:
        await client.close()


@pytest.mark.parametrize('value', [
    '400.50 on 2026-10-06', 'INR 1,23,456.78 on 2026-01-01', '100000000.00',
    'Salary 9876543210', 'from 2026-10-01 to 2026-10-06',
])
def test_redaction_preserves_money_and_dates(value):
    assert redact(value) == value


@pytest.mark.parametrize('value', ['email a@example.com', 'phone +91 98765 43210',
    '@someone', 'telegram_id=123456789', 'password=hunter2', 'api_key=synthetic-key',
    'sk-synthetic-secret', '123456789:ABCDEFGHIJKLMNOPQRSTUVXYZ',
    'https://example.invalid/?token=secret', 'Bearer synthetic-secret',
    'account number 123456789012', 'user_id=12345678', 'my name is Jane Doe; spent 400'])
def test_explicit_identifiers_redacted(value):
    result = redact(value)
    assert '[redacted]' in result
    for token in ['a@example', '98765', '@someone', '123456', 'hunter2', 'synthetic', 'Jane Doe']:
        assert token not in result


@pytest.mark.parametrize('action', [dict(type='opening', amount_inr='10'),
    dict(type='undo', transaction_id='untrusted-id'),
    dict(type='correct', transaction_id='untrusted-id', changes={'amount_inr': '10'})])
async def test_model_never_has_backend_authority(monkeypatch, action):
    client = client_for(monkeypatch, lambda request: httpx.Response(200, json=response(mutation(action))))
    try:
        with pytest.raises(BudgetError) as exc:
            await client.interpret('Do what the model says', ['Travel'], NOW, 'Asia/Kolkata')
        assert exc.value.code == 'invalid_model_output'
    finally:
        await client.close()


async def test_missing_bucket_lists_existing_and_creation_options(monkeypatch):
    client = client_for(monkeypatch, lambda request: httpx.Response(200, json=response(mutation(expense('Unknown')))))
    try:
        value = await client.interpret('Spent 400 on Metro', ['Food', 'Travel'], NOW, 'Asia/Kolkata')
        question = value['clarification_question']
        assert value['kind'] == 'clarification' and not value['actions']
        assert all(word in question for word in ['Food', 'Travel', 'create'])
    finally:
        await client.close()


async def test_redacted_bucket_resolves_only_to_unique_original(monkeypatch):
    original = 'Contact a@example.com'
    safe = redact(original)
    client = client_for(monkeypatch, lambda request: httpx.Response(200, json=response(mutation(expense(safe)))))
    try:
        value = await client.interpret('Spent 400 on Metro', [original], NOW, 'Asia/Kolkata')
        assert value['actions'][0]['bucket_name'] == original
        value = await client.interpret('Spent 400 on Metro', [original, 'Contact b@example.com'], NOW, 'Asia/Kolkata')
        assert value['kind'] == 'clarification' and value['actions'] == []
    finally:
        await client.close()


@pytest.mark.parametrize('action', [dict(type='undo', last=True),
    dict(type='undo', reference='Metro yesterday'),
    dict(type='correct', reference='Metro yesterday', changes={'amount_inr': '35'})])
async def test_nl_reference_drafts_never_return_backend_record_selectors(monkeypatch, action):
    client = client_for(monkeypatch, lambda request: httpx.Response(200, json=response(mutation(action))))
    try:
        value = await client.interpret('Undo or correct Metro yesterday', ['Travel'], NOW, 'Asia/Kolkata')
        assert value['kind'] == 'mutation' and len(value['actions']) == 1
        draft = value['actions'][0]
        assert draft['type'] == action['type']
        assert not {'transaction_id', 'batch_id', '_selected'} & set(draft)
        if action['type'] == 'correct':
            assert draft['changes'] == action['changes']
    finally:
        await client.close()


async def test_income_allocate_batch_remains_ordered_and_not_implicitly_funded(monkeypatch):
    actions = [dict(type='income', amount_inr='2000', description='Salary'),
               dict(type='allocate', amount_inr='2000', bucket_name='Travel')]
    client = client_for(monkeypatch, lambda request: httpx.Response(200, json=response(mutation(*actions))))
    try:
        value = await client.interpret('Add new income 2000 to Travel', ['Travel'], NOW, 'Asia/Kolkata')
        assert value == mutation(*actions)
    finally:
        await client.close()


@pytest.mark.parametrize('text', ['New income 2000', 'Create a bucket', 'Spent 400 on new shoes'])
async def test_unspecified_bucket_never_created_by_model(monkeypatch, text):
    client = client_for(monkeypatch, lambda request: httpx.Response(200, json=response(
        mutation(dict(type='create_bucket', name='Invented')))))
    try:
        value = await client.interpret(text, ['Travel'], NOW, 'Asia/Kolkata')
        assert value['kind'] == 'clarification' and value['actions'] == []
    finally:
        await client.close()


@pytest.mark.parametrize('text', ['phone 2026-10-06', 'mobile 400.50', 'phone INR 400.50'])
def test_contact_labels_do_not_eat_financial_dates_or_amounts(text):
    assert redact(text) == text


@pytest.mark.parametrize('key,model,timeout', [(None, 'test', 30), ('', 'test', 30),
    ('synthetic', '', 30), ('synthetic', 'test', 0), ('synthetic', 'test', float('inf'))])
def test_explicit_bounded_config_required_without_environment_fallback(key, model, timeout):
    with pytest.raises(BudgetError) as exc:
        AIInterpreter('https://example.invalid/v1', key, model, timeout)
    assert exc.value.code == 'invalid_provider_config'


async def test_explicit_bucket_creation_and_target_removal_are_planner_compatible(monkeypatch):
    actions = [dict(type='create_bucket', name='Travel'),
               dict(type='set_target', bucket_name='Travel', remove=True, amount_inr=None)]
    client = client_for(monkeypatch, lambda request: httpx.Response(200, json=response(mutation(*actions))))
    try:
        value = await client.interpret('Create Travel bucket and remove its target', [], NOW, 'Asia/Kolkata')
        assert value['actions'] == [actions[0], dict(type='set_target', bucket_name='Travel', remove=True)]
    finally:
        await client.close()


async def test_unsupported_prose_is_not_model_authority(monkeypatch):
    value = dict(schema_version=1, kind='unsupported', actions=[], missing_fields=[],
                 clarification_question='SECRET investment recommendation', query=None)
    client = client_for(monkeypatch, lambda request: httpx.Response(200, json=response(value)))
    try:
        result = await client.interpret('Buy stocks', ['Travel'], NOW, 'Asia/Kolkata')
        assert result['kind'] == 'unsupported' and result['actions'] == []
        assert 'not supported' in result['clarification_question']
        assert 'SECRET' not in result['clarification_question']
    finally:
        await client.close()
