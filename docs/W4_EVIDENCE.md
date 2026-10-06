# W4 command and durable transport evidence

## Delivered

- `src/budget_bot/telegram/commands.py`: frozen six-key parse_command envelope, shlex quoting, all W4 commands, bounded text/name/description fields, exact positive decimal amount strings, strict correction fields, reports/setup/access/timezone/help routing. Financial actions are proposals only. Missing income/expense description raises safe `CommandError(code="missing_fields")` with `.missing_fields`; trailing date alone is not a fabricated description. Allocate/transfer retain the specified action fields without invented business descriptions.
- `src/budget_bot/telegram/transport.py`: injected PTB Bot/store/controller; durable save before handler and next polling offset; duplicate receipt delegated to store uniqueness; handler/complete failure retains inbox; one attempt per outbox claim per cycle; exact lease-token ack/fail; remote timeout at-least-once replay; safe callback answer then controller handling; no webhook deletion/drop/startup. Factory explicitly disables HTTPX redirects on both SDK requests. Interruptible one-second run-cycle delay and bounded receive/inbox/outbox batches.
- `src/budget_bot/telegram/__init__.py`: command exports.
- `tests/test_telegram_commands.py`, `tests/test_telegram_transport.py`: deterministic tests using real PTB Bot/Update conversion, inline keyboard serialization, controlled BaseRequest and frozen store/controller doubles.

## Actual RED/GREEN execution

1. Command tests before implementation: collection RED, missing budget_bot.telegram. After command implementation: GREEN, 39 passed.
2. Transport tests before implementation: collection RED, missing budget_bot.telegram.transport. After implementation: GREEN, 45 passed; scoped Ruff passed.
3. Added fault tests and date-only missing-description regression: RED, 2 failed / 50 passed (date tokens were wrongly accepted as descriptions). Corrected parser: GREEN, 52 passed in 3.42s; scoped Ruff: All checks passed.

Final command, from W4 worktree:

    PYTHONPATH="$PWD/src" 'C:/Users/FL_LPT-657/Projects/telegram_bot/.venv/Scripts/python.exe' -m pytest tests/test_telegram*.py -q

Lint:

    'C:/Users/FL_LPT-657/Projects/telegram_bot/.venv/Scripts/python.exe' -m ruff check src/budget_bot/telegram/commands.py src/budget_bot/telegram/transport.py src/budget_bot/telegram/__init__.py tests/test_telegram_commands.py tests/test_telegram_transport.py

## Integration boundaries / gaps (not claimed verified)

- Real PostgreSQL BudgetStore and parent controller were unavailable in this worktree and are injected doubles. No database, live Telegram, full bot startup, services, or remote writes were exercised. Store unique receipt, lease claim/expiry/backoff/fencing and atomic inbox completion/outbox insertion must be verified by parent integration against W2.
- Transport expects pending inbox records `{update_id, payload}`; the frozen contract lists pending_updates but does not specify that record shape. Parent must align this shape. Store determines bot-scoping of pending records and offsets; transport assumes one bot per store/runtime.
- Caller initializes/closes Bot and owns store lifecycle. Parent owns private-chat authorization, owner-scoped undo/correction resolution, workflow review/confirm, timezone/date resolution and forwarding recoverable CommandError fields. Received_at/timezone are signature-compatible; parser leaves action dates unresolved for the domain.
- Retry is indefinite durable retention, not finite discard: bounded attempts/batches per cycle, store-managed retry schedule, no loss on exhausted attempts. Failed ack propagates and leaves a lease to expire; a subsequent remote send may duplicate a reply. No exactly-once Telegram delivery claim.
- create_bot supplies no-redirect SDK configuration; externally injected production Bots must use equivalent configuration. Factory test inspects actual PTB HTTPX clients; test request implementations make no live calls.
- No shared settings/dependencies/contracts, calendar.py, or sibling files modified. Only owned files are committed.
