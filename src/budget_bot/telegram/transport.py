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
CALLBACK_ACK_TIMEOUT = 2.0


def _text_chunks(text):
    """Conservatively bound UTF-16 units without splitting a Unicode codepoint."""
    start, units = 0, 0
    for index, character in enumerate(text):
        size = 2 if ord(character) > 0xffff else 1
        if units + size > 4096:
            yield text[start:index]
            start, units = index, 0
        units += size
    yield text[start:]


def _durable_replies(replies):
    result = []
    for reply in replies:
        chunks = list(_text_chunks(reply['text']))
        keyboard = reply.get('keyboard')
        if isinstance(keyboard, InlineKeyboardMarkup):
            keyboard = keyboard.to_dict()
        for index, text in enumerate(chunks):
            result.append({**reply, 'text': text,
                           'keyboard': keyboard if index == len(chunks) - 1 else None})
    return result


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
        [InlineKeyboardButton(text=button['text'],
                              callback_data=button['data'] if 'data' in button else button['callback_data'])
         for button in row]
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
        for record in self.store.pending_updates(limit=100, bot_id=self.bot.id):
            try:
                payload = record['payload']
                received_at = record.get('received_at', now)
                if isinstance(received_at, str):
                    received_at = datetime.fromisoformat(received_at)
                if received_at.tzinfo is None or received_at.utcoffset() is None:
                    raise ValueError('Receipt must be timezone aware')
                callback = payload.get('callback_query')
                if callback and callback.get('id'):
                    try:
                        await asyncio.wait_for(
                            self.bot.answer_callback_query(callback['id']),
                            timeout=CALLBACK_ACK_TIMEOUT,
                        )
                    except (TelegramError, TimeoutError):
                        logger.warning('Callback answer failed; continuing durable handling')
                replies = await self.controller.handle(payload, received_at)
                self.store.complete_update(record['update_id'], _durable_replies(replies), now,
                                           bot_id=self.bot.id)
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
        for item in self.store.pending_outbox(now, limit=20, bot_id=self.bot.id):
            try:
                # Older durable records may predate ingress splitting. Replay of
                # such a record is at-least-once for the entire chunk sequence.
                chunks = list(_text_chunks(item['text']))
                for index, text in enumerate(chunks):
                    await self.bot.send_message(
                        chat_id=item['chat_id'], text=text,
                        reply_markup=_keyboard(item.get('keyboard')) if index == len(chunks) - 1 else None,
                    )
            except Exception:
                logger.warning('Outbox delivery failed; retry retained')
                self.store.fail_outbox(item['id'], item['lease_token'], now)
                continue
            if self.store.ack_outbox(item['id'], item['lease_token'], now) is False:
                logger.warning('Outbox acknowledgement rejected; lease fencing retained')
                continue
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
