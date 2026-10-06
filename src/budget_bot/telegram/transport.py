"""Durable PTB ingress/egress adapter; financial decisions belong to controller.

The caller owns Bot initialization/shutdown and BudgetStore lifecycle. Store
methods are the synchronous frozen BudgetStore API. Only the store controls
outbox lease expiry/backoff; each claimed item is attempted once per cycle.
"""
import asyncio
import logging
from datetime import datetime, timezone

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.request import HTTPXRequest

logger = logging.getLogger(__name__)


def create_bot(token):
    """Build the maintained SDK client without startup or network calls."""
    return Bot(
        token,
        request=HTTPXRequest(httpx_kwargs={'follow_redirects': False}),
        get_updates_request=HTTPXRequest(httpx_kwargs={'follow_redirects': False}),
    )


def _keyboard(value):
    if value is None or value == []:
        return None
    if isinstance(value, InlineKeyboardMarkup):
        return value
    if isinstance(value, dict):
        return InlineKeyboardMarkup.de_json(value, None)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(text=button['text'], callback_data=button['data']) for button in row]
        for row in value
    ])


class TelegramTransport:
    def __init__(self, bot, store, controller):
        self.bot = bot
        self.store = store
        self.controller = controller

    async def poll_once(self, now):
        """Persist fetched updates before handling; return completed inbox count.

        Receiving duplicates is deliberately delegated to the store unique key.
        A receive timeout does not prevent retrying already durable inbox work.
        Store failures propagate: no offset can be advanced by this adapter.
        """
        offset = self.store.polling_offset(self.bot.id)
        try:
            updates = await self.bot.get_updates(
                offset=offset, limit=100, timeout=0,
                allowed_updates=['message', 'callback_query'],
            )
        except TelegramError:
            logger.warning('Telegram receive failed; durable inbox retained')
            updates = []
        for update in updates:
            self.store.save_update(self.bot.id, update.update_id, update.to_dict(), now)
        completed = 0
        for record in self.store.pending_updates(limit=100):
            payload = record['payload']
            callback = payload.get('callback_query')
            if callback and callback.get('id'):
                try:
                    await self.bot.answer_callback_query(callback['id'])
                except TelegramError:
                    logger.warning('Callback answer failed; continuing durable handling')
            try:
                replies = await self.controller.handle(payload, now)
                self.store.complete_update(record['update_id'], replies, now)
            except Exception:
                # Do not include exception text, updates, token, or financial data.
                logger.warning('Inbox handling failed; update retained')
                continue
            completed += 1
        return completed

    async def deliver_once(self, now):
        """One attempt per leased message; remote timeouts can yield duplicates.

        The exact claim token is passed to both ack/fail (no unfenced fallback).
        An ack failure is not acknowledged locally and the lease must expire;
        the next send may repeat a remotely delivered message (at-least-once).
        """
        delivered = 0
        for item in self.store.pending_outbox(now, limit=20):
            try:
                await self.bot.send_message(
                    chat_id=item['chat_id'], text=item['text'],
                    reply_markup=_keyboard(item['keyboard']),
                )
            except Exception:
                logger.warning('Outbox delivery failed; retry retained')
                self.store.fail_outbox(item['id'], item['lease_token'], now)
                continue
            self.store.ack_outbox(item['id'], item['lease_token'], now)
            delivered += 1
        return delivered

    async def run(self, stop_event):
        """Bounded polling cycles with interruptible delay; no Bot startup here."""
        while not stop_event.is_set():
            now = datetime.now(timezone.utc)
            try:
                await self.poll_once(now)
            except Exception:
                logger.warning('Ingress cycle failed; durable state retained')
            try:
                await self.deliver_once(now)
            except Exception:
                logger.warning('Egress cycle failed; durable state retained')
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=1.0)
            except TimeoutError:
                pass
