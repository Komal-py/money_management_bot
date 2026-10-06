# W4 integrated transport hardening evidence

Baseline: worker/w4, clean at start; existing targeted command/transport tests: 52 passed. No saved untracked work was present. Read REQUIREMENTS and CONTRACTS. Only owned transport/tests/evidence changed; commands.py and telegram/__init__.py already meet their existing tested contract and remain unchanged. Controller/calendar/storage were inspected, not edited.

## Changes and regression evidence

Five exact regressions were added before the source fix and returned 5 failed, 11 passed: durable receipt lost on restart; JSON button lists using callback_data rejected; hung callback acknowledgement blocked ingress; long replies not split durably; rejected lease acknowledgement counted as delivery. Minimal transport changes made these green.

Transport uses the actual nested inbox payload and parses aware received_at ISO strings (including database-local offsets), retaining malformed records and failed handlers without blocking later records. Callback acknowledgement has a two-second asyncio deadline; failure does not discard durable handling. PTB markup is serialized before JSONB persistence; egress accepts normalized data buttons, callback_data lists and PTB inline_keyboard dictionaries. Long replies are split before atomic complete_update, conservatively at 4096 UTF-16 units without splitting a codepoint; only the last chunk gets buttons. Legacy oversized outbox rows are also split at send time. Rejected fenced acknowledgements are not counted.

Previously passing behavior verified rather than rewritten: no webhook deletion/drop pending; both factory SDK requests disable redirects; receive timeout still drains durable inbox; duplicate handling; save-before-handle; handler retry; run restart after cycle failure; send failure retry; ack failure replay. Financial commands remain action proposals with no direct commit calls.

## Actual verification

Windows bash, project venv, no dependency installation:

    export PYTHONPATH="$PWD/src" BUDGET_TEST_SCHEMA=test_w4
    C:/Users/FL_LPT-657/Projects/telegram_bot/.venv/Scripts/python.exe -m pytest tests/test_telegram_store_integration.py tests/test_telegram_transport.py tests/test_telegram_commands.py -q --tb=short

Final result: 64 passed in 10.78s, including four real PostgreSQL BudgetStore tests. BUDGET_TEST_DATABASE_URL came only from supplied environment; fixture asserts test_w4, never reads credentials from files. Store has 5s lock, 15s statement and 20s idle transaction limits. Fixture clears only transport tables in test_w4 before/after tests; synthetic owner/proposal records remain in that isolated schema, with the proposal cancelled. No financial confirmation/ledger commit performed.

Real store exercises SDK-normalized nested payloads, original receipt instant, duplicate durable receipt, failed handler and new transport instance restart, atomic JSONB chunk outbox, lease/backoff/fencing, durable offset despite handler failure, and a missing update ID filled later. Money-before-onboarding is rejected by the real planner; bucket command yields a pending proposal without changing snapshot.

    C:/Users/FL_LPT-657/Projects/telegram_bot/.venv/Scripts/python.exe -m ruff check src/budget_bot/telegram/commands.py src/budget_bot/telegram/transport.py src/budget_bot/telegram/__init__.py tests/test_telegram*.py
    git diff --check

Both passed. No full suite, live Telegram/provider traffic, token, new database, role, or server. Network controlled through PTB BaseRequest; database is the approved existing test database.

## Concrete parent/storage boundaries against this baseline

1. Storage pending_outbox sorts due_at/id (random UUID), not update/position, and claims independently. Split chunks are durable and complete but can be delivered out of sequence; Confirm can arrive before earlier review chunks. Parent/W2 must enforce per-reply ordered delivery and prevent later chunks overtaking a failed earlier chunk. Integration deliberately compares sent content without claiming order. Legacy oversized single-row retries repeat all chunks after partial remote delivery (at-least-once).
2. Storage contiguous cursor remains at a genuinely absent ID indefinitely. Telegram filtered updates can have legitimate numeric gaps and IDs can reset after inactivity. Test verifies baseline 10,12 leaves offset 11, then receipt 11 advances to 13; it does NOT claim genuine absent-gap recovery. Transport must not invent receipt or advance beyond store authority. Storage needs an atomic fetched-batch/high-water receipt API acknowledging known returned gaps, with a defined reset policy. No unsupported API or storage patch added.
3. pending_updates is not bot-scoped; complete_update selects by update_id alone and rejects same ID across bots. Current single-bot runtime works; multi-bot inbox draining needs bot-scoped claim/completion APIs, not transport guessing another bot's identity.
4. Controller baseline only routes rev callbacks, help, mutation, balances and spending. Missing routes: calendar query/cal/day callbacks; setup callbacks/start redemption; admin invite/users/revoke; timezone/cancel; natural-language/clarification. Parent owns those routes and private identity checks. No controller/calendar changes made, and no claim that these routes work.
5. Controller recomputes message date for command proposals; callback decisions receive the durable receipt instant from transport. Parent should review confirmation-expiry policy for delayed callback processing (processing time versus receipt), without relaxing store expiry checks.

Parent integrates and publishes; this worker does not claim a finished app or live verification.
