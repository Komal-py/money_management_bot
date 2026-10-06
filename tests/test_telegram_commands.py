from datetime import datetime, timezone

import pytest

from budget_bot.telegram.commands import HELP_TEXT, CommandError, parse_command

NOW = datetime(2026, 10, 6, tzinfo=timezone.utc)


def parse(text):
    return parse_command(text, NOW, "Asia/Kolkata")


@pytest.mark.parametrize("text,action", [
    ('/income 1000 Salary', {"type": "income", "amount_inr": "1000", "description": "Salary", "date_expression": None}),
    ('/bucket "Holiday Fund"', {"type": "create_bucket", "name": "Holiday Fund"}),
    ('/allocate 500 Travel', {"type": "allocate", "amount_inr": "500", "bucket_name": "Travel"}),
    ('/transfer 100 Travel Food', {"type": "transfer", "amount_inr": "100", "source_bucket": "Travel", "destination_bucket": "Food"}),
    ('/expense 400 Travel "Metro ride" 2026-10-05', {"type": "expense", "amount_inr": "400", "bucket_name": "Travel", "description": "Metro ride", "date_expression": "2026-10-05"}),
    ('/undo', {"type": "undo", "last": True}),
    ('/undo last', {"type": "undo", "last": True}),
    ('/undo abc', {"type": "undo", "transaction_id": "abc"}),
    ('/correct abc amount=350 bucket=Food date=yesterday "description=Lunch meal"', {"type": "correct", "transaction_id": "abc", "changes": {"amount_inr": "350", "bucket_name": "Food", "date_expression": "yesterday", "description": "Lunch meal"}}),
    ('/target Travel 2000', {"type": "set_target", "bucket_name": "Travel", "amount_inr": "2000"}),
    ('/target Travel off', {"type": "set_target", "bucket_name": "Travel", "remove": True}),
])
def test_actions(text, action):
    result = parse(text)
    assert result == {"kind": "mutation", "actions": [action], "query": None,
                      "command": text.split()[0][1:], "args": __import__('shlex').split(text)[1:]}


@pytest.mark.parametrize('text,missing', [('/income 100', ['description']), ('/expense 10 Travel', ['description']), ('/expense 10', ['bucket_name', 'description']), ('/expense 10 Travel 2026-10-05', ['description']), ('/income 100 yesterday', ['description'])])
def test_missing_fields_are_recoverable(text, missing):
    with pytest.raises(CommandError) as error:
        parse(text)
    assert error.value.code == 'missing_fields'
    assert error.value.missing_fields == missing


@pytest.mark.parametrize('text', ['/income 1e3 Salary', '/income -1 Salary', '/expense 1.001 Travel Metro', '/correct x mystery=1', '/correct x amount=1 amount=2', '/balance extra', '/undo x y', '/expense 4 Travel "unterminated', '/opening 100', '/wat', '/target Travel -1', '/spending 2026-10-06 2026-10-01', '/timezone Invalid/Zone'])
def test_invalid_commands(text):
    with pytest.raises(CommandError):
        parse(text)


@pytest.mark.parametrize('text,kind', [('/start invite-code', 'setup'), ('/invite', 'admin'), ('/users', 'admin'), ('/revoke 123', 'admin'), ('/timezone Asia/Kolkata', 'timezone'), ('/cancel', 'cancel'), ('/help', 'help')])
def test_nonfinancial(text, kind):
    result = parse(text)
    assert result['kind'] == kind
    assert result['actions'] == []


@pytest.mark.parametrize('text,report,period', [('/balance', 'balances', 'today'), ('/calendar', 'calendar', 'month'), ('/spending week Travel', 'spending', 'week'), ('/spending 2026-10-01 2026-10-06 Travel', 'spending', 'range')])
def test_queries(text, report, period):
    result = parse(text)
    assert result['query']['report'] == report
    assert result['query']['period'] == period
    assert result['actions'] == []


def test_mentions_bounds_and_help():
    assert parse('/balance@BudgetBot')['command'] == 'balance'
    for command in ['income', 'bucket', 'allocate', 'expense', 'transfer', 'undo', 'correct', 'target', 'balance', 'spending', 'calendar', 'start', 'invite', 'users', 'revoke', 'timezone', 'cancel', 'help']:
        assert '/' + command in HELP_TEXT
    with pytest.raises(CommandError):
        parse('/income 10 ' + 'x' * 241)
    with pytest.raises(CommandError):
        parse('/help ' + 'x' * 4096)
