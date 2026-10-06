# W7 report/calendar component evidence

Scope: only reports.py, telegram/calendar.py, tests/test_reports*.py and this file.
No shared contracts, package __init__, locks, controller, external services or DB changed.

## Incremental TDD (actual local execution)

Every test run used:

`PYTHONPATH="$PWD/src" C:/Users/FL_LPT-657/Projects/telegram_bot/.venv/Scripts/python.exe -m pytest tests/test_reports*.py -q`

1. Reports RED: collection failed with ModuleNotFoundError budget_bot.services (exit 2).
2. Reports GREEN: 11 passed in 0.86s (exit 0).
3. Calendar RED: collection failed with ModuleNotFoundError budget_bot.telegram.calendar (exit 2).
4. Calendar GREEN: 34 passed in 1.12s (exit 0).
5. Target warning increment RED: 1 failed, 35 passed in 1.96s (exit 1); assertion proved missing warning.
6. Final GREEN: 36 passed in 0.97s (exit 0).
7. Ruff on both owned implementations and both test files: All checks passed (exit 0).
8. git diff --check: exit 0.

## Delivered component behavior

- Exact integer paise formatting, including negative one paise and integers above float precision.
- Deterministic bucket ordering, pool/bucket/total virtual available balance, current monthly targets and negative warnings.
- Spending uses store-provided aggregate facts; inclusive today, Monday week, month and explicit range.
- Aware receipt timestamp anchored to snapshot timezone, including UTC/local day and year crossings; no wall clock.
- Case-insensitive canonical bucket filter; invalid dates/ranges/buckets rejected before spending lookup.
- Empty results explicitly show zero and no active expenses.
- Current receipt-month spending warns at equality or excess of current target.
- Monday month grid, leap days, adjacent-year navigation and bounded years 0001 through 9999.
- Strict callback parser for cal:YYYY-MM and day:YYYY-MM-DD[:page]; rejects invalid dates, noncanonical pages and payloads over 64 UTF-8 bytes. Page range is 0..999999.
- Eight-entry day pages, deterministic ISO-date/ID ordering, full-day totals and bucket breakdown on every page, previous/next/back buttons.
- Owner supplied explicitly to store by caller; calendar callbacks cannot supply owner IDs. No mutation methods called; doubles verify unchanged input facts.

## Integration boundaries and gaps (not full integration evidence)

- Tests use an injected synchronous store double with the exact frozen snapshot/expense/result field shapes. No PostgreSQL execution or actual W2 revision-selection verification performed. Active expense/undo exclusion is the spending API's responsibility; renderers do not reconstruct ledger truth.
- No controller routing/private-chat integration changed or tested here. Parent must validate callbacks with parse_callback and pass authenticated current owner to day_view.
- W1 domain.money is absent in this worktree. Local integer-only formatting is executable without waiting on W1. Parent can unify it with W1 format_money once available; malformed money types fail instead of coercion.
- Frozen get_snapshot/spending APIs expose current bucket targets but no dated target-history schema. Historical ranges/day reports deliberately do not retrofit current targets. Actual historical target reporting requires parent/store contract integration; no history shape or DB truth was guessed.
- Target warnings here are report feedback only. Planner/store must enforce their own mutation-time target/negative warnings. The current-month comparison uses the provided receipt and current snapshot setting; historical receipt replay with later settings cannot recover historical targets from these APIs.
- No model, network, live Telegram, provisioning, full suite or production-service verification performed.
