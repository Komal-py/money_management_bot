"""Startup/lifecycle checks: real approved PostgreSQL, controlled Telegram SDK."""
import asyncio
import importlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from telegram import Bot
from telegram.request import BaseRequest

from budget_bot.settings import load_settings
from budget_bot.storage import BudgetStore


ROOT = Path(__file__).resolve().parents[1]


def settings():
    assert os.environ['BUDGET_TEST_SCHEMA'] == 'test_w1'
    return load_settings(None, {
        'DATABASE_URL': os.environ['BUDGET_TEST_DATABASE_URL'],
        'TELEGRAM_BOT_TOKEN': '123:TEST', 'TELEGRAM_ADMIN_USER_ID': str(uuid4().int % (2**50) + 1),
        'AI_ENABLED': 'false', 'BUDGET_SCHEMA': 'test_w1', 'BUDGET_WORKFLOW_SCHEMA': 'test_w1',
    })


class TelegramRequest(BaseRequest):
    def __init__(self, webhook=''):
        self.calls = []
        self.webhook = webhook
        self.closed = 0

    @property
    def read_timeout(self):
        return 5

    async def initialize(self):
        pass

    async def shutdown(self):
        self.closed += 1

    async def do_request(self, url, method, request_data=None, **kwargs):
        name = url.rsplit('/', 1)[1]
        self.calls.append(name)
        if name == 'getMe':
            result = {'id': 99, 'is_bot': True, 'first_name': 'Synthetic', 'username': 'SyntheticBot'}
        elif name == 'getWebhookInfo':
            result = {'url': self.webhook, 'has_custom_certificate': False, 'pending_update_count': 0}
        else:
            raise AssertionError('Unexpected live-style API: ' + name)
        return 200, json.dumps({'ok': True, 'result': result}).encode()


def test_runtime_import_has_no_file_or_network_side_effects(monkeypatch):
    import budget_bot.settings as module
    def forbidden(*args, **kwargs):
        pytest.fail('Import must not read credential configuration')
    monkeypatch.setattr(module, 'dotenv_values', forbidden)
    main = importlib.import_module('budget_bot.main')
    importlib.reload(main)
    assert callable(main.main)


def test_cli_help_and_offline_config_check(tmp_path, capsys):
    from budget_bot.main import main
    with pytest.raises(SystemExit) as exit_info:
        main(['--help'])
    assert exit_info.value.code == 0
    path = tmp_path / '.env'
    path.write_text('DATABASE_URL=postgresql+psycopg://u:SYNTHETIC_SECRET@localhost/db\n'
                    'TELEGRAM_BOT_TOKEN=123:SYNTHETIC_SECRET\nTELEGRAM_ADMIN_USER_ID=12345\nAI_ENABLED=false\n')
    assert main(['--env-file', str(path), '--check-config']) == 0
    output = capsys.readouterr().out
    assert 'Configuration valid' in output and 'SYNTHETIC_SECRET' not in output


def test_cli_invalid_config_is_redacted(tmp_path, capsys):
    from budget_bot.main import main
    path = tmp_path / '.env'
    path.write_text('DATABASE_URL=sqlite:///SYNTHETIC_SECRET\n'
                    'TELEGRAM_BOT_TOKEN=123:SYNTHETIC_SECRET\nTELEGRAM_ADMIN_USER_ID=12345\nAI_ENABLED=false\n')
    assert main(['--env-file', str(path), '--check-config']) == 1
    assert 'SYNTHETIC_SECRET' not in ''.join(capsys.readouterr())


async def test_runtime_real_services_ai_disabled_and_graceful_shutdown():
    from budget_bot.main import build_runtime, migrate
    config = settings()
    migrate(config, ROOT)
    request = TelegramRequest()
    bot = Bot('123:TEST', request=request, get_updates_request=request)
    async with build_runtime(config, project_dir=ROOT, bot=bot) as runtime:
        assert runtime.workflow.interpreter is None
        assert runtime.controller.onboarding is runtime.onboarding
        assert runtime.controller.reports is runtime.reports
        assert runtime.controller.access is runtime.access
        user = runtime.store.get_user(config.telegram_admin_user_id)
        snapshot = runtime.store.get_snapshot(user['owner_id'])
        assert snapshot['transactions'] == [] and snapshot['onboarded'] is False
        reply = await runtime.controller.handle({'message': {
            'chat': {'id': config.telegram_admin_user_id, 'type': 'private'},
            'from': {'id': config.telegram_admin_user_id, 'is_bot': False}, 'text': '/help'}},
            __import__('datetime').datetime.now(__import__('datetime').timezone.utc))
        assert reply[0]['text']
        stop = asyncio.Event()
        stop.set()
        await runtime.transport.run(stop)
    assert request.calls == ['getMe', 'getWebhookInfo']
    assert request.closed >= 1


async def test_existing_webhook_is_refused_without_deleting_updates():
    from budget_bot.main import build_runtime, migrate
    config = settings()
    migrate(config, ROOT)
    request = TelegramRequest('https://example.test/existing')
    bot = Bot('123:TEST', request=request, get_updates_request=request)
    with pytest.raises(ValueError, match='webhook'):
        async with build_runtime(config, project_dir=ROOT, bot=bot):
            pytest.fail('Existing webhook must not start a poller')
    assert request.calls == ['getMe', 'getWebhookInfo'] and request.closed >= 1
    store = BudgetStore(config.database_url, config.schema_name)
    try:
        assert store.get_user(config.telegram_admin_user_id) is None
    finally:
        store.close()
