"""SDK privacy failure boundary with real PTB over synthetic controlled HTTP."""
import logging
from datetime import datetime, timezone

import pytest
from telegram import Bot

from budget_bot.telegram.transport import TelegramTransport
from test_telegram_transport import Controller, Request, Store

NOW = datetime(2026, 10, 6, tzinfo=timezone.utc)
PRIVATE = 'SYNTHETIC_PRIVATE_CALLBACK_CONTENT'


def malformed():
    return {'update_id': 10, 'callback_query': {
        'id': 'synthetic', 'from': {'id': 7, 'is_bot': False, 'first_name': PRIVATE},
        'data': PRIVATE}}


async def test_real_sdk_parse_log_is_sanitized_before_every_handler(caplog):
    request, store = Request(), Store()
    request.updates = [malformed()]
    observed = []

    class Handler(logging.Handler):
        def emit(self, record):
            observed.append((record.getMessage(), record.args, record.exc_info,
                             record.exc_text, record.stack_info))

    logger = logging.getLogger('telegram.Bot')
    handler = Handler()
    logger.addHandler(handler)
    old_filters = tuple(logger.filters)
    try:
        async with Bot('123:TEST', request=request, get_updates_request=request) as bot:
            with pytest.raises(TypeError):
                await TelegramTransport(bot, store, Controller()).poll_once(NOW)
        assert PRIVATE not in caplog.text
        assert observed and PRIVATE not in repr(observed)
        assert all(not args and not exc and not text and not stack
                   for _, args, exc, text, stack in observed)
        assert store.polling_offset(99) == 10 and not store.inbox
        assert tuple(logger.filters) == old_filters
    finally:
        logger.removeHandler(handler)


async def test_repeated_parse_failure_restores_logger_configuration():
    request, store = Request(), Store()
    request.updates = [malformed()]
    logger = logging.getLogger('telegram.Bot')
    before = (tuple(logger.filters), tuple(logger.handlers), logger.level, logger.propagate)
    async with Bot('123:TEST', request=request, get_updates_request=request) as bot:
        transport = TelegramTransport(bot, store, Controller())
        for _ in range(2):
            with pytest.raises(TypeError):
                await transport.poll_once(NOW)
            assert (tuple(logger.filters), tuple(logger.handlers), logger.level, logger.propagate) == before
    assert not store.inbox and not store.outbox
