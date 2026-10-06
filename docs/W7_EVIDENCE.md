# W7 report/calendar component evidence

Scope: only reports.py, telegram/calendar.py, tests/test_reports*.py and this file.
No shared contracts, package __init__, locks, controller or sibling source changed.
This follow-up uses the existing approved test database, schema test_w7 only.
Startup git status was clean on worker/w7; there was no saved untracked work to review.

## Earlier component-only TDD (retained historical evidence)

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
- Receipt-month spending warns at equality or excess of the target effective in that month.
- Monday month grid, leap days, adjacent-year navigation and bounded years 0001 through 9999.
- Strict callback parser for cal:YYYY-MM and day:YYYY-MM-DD[:page]; rejects invalid dates, noncanonical pages and payloads over 64 UTF-8 bytes. Page range is 0..999999.
- Eight-entry day pages, deterministic ISO-date/ID ordering, full-day totals and bucket breakdown on every page, previous/next/back buttons.
- Owner supplied explicitly to store by caller; calendar callbacks cannot supply owner IDs. No mutation methods called; doubles verify unchanged input facts.

## Real PostgreSQL/domain follow-up

Environment set in bash: `export PYTHONPATH="$PWD/src" BUDGET_TEST_SCHEMA=test_w7`.
Interpreter for all commands: `C:/Users/FL_LPT-657/Projects/telegram_bot/.venv/Scripts/python.exe`.
Credentials came only from BUDGET_TEST_DATABASE_URL, never a fixture literal or command value.

Actual commands/results:

1. `python.exe -m pytest tests/test_reports.py tests/test_reports_calendar.py -q`
   Baseline before edits: 36 passed in 4.52s.
2. `python.exe -m pytest tests/test_reports_integration.py -q --tb=short`
   RED: 1 failed, 4 passed in 10.74s. January incorrectly inherited May's current target.
   Real persisted target history shape assertions passed before the failed report assertion.
3. Minimal report fix: select latest (effective_month, revision) not after the report month;
   no applicable history means no target; removal is None, not fallback to current settings.
   Existing double warning test now supplies the actual dated history shape.
4. `python.exe -m pytest tests/test_reports.py tests/test_reports_calendar.py tests/test_reports_integration.py -q --tb=short`
   GREEN: 41 passed in 12.40s. Added verification-only integer boundary cases and week/read-only checks:
   58 passed in 9.24s. These passing behaviors required no implementation changes.
5. `python.exe -m ruff check src/budget_bot/services/reports.py src/budget_bot/telegram/calendar.py tests/test_reports.py tests/test_reports_calendar.py tests/test_reports_integration.py`
   First run caught one unused date import; removed it. Final scoped Ruff: All checks passed.
6. Final rerun of command 4 after import cleanup: 58 passed in 7.38s.
   `git diff --check`: exit 0. Only owned files modified/new.

The five PostgreSQL cases use the actual BudgetStore and domain planner, no monkeypatch/doubles.
Only test_w7 tables are initialized. Unique fictional users preserve existing rows; no truncation,
schema drop, shared tables, new database/role/server, or dependency installation. Test fixtures
assert search_path=test_w7, lock_timeout=5s, statement_timeout=15s. Database setup failure
suppresses connection exception details. Fictional records remain in test_w7 for audit review.

Verified through propose then confirm (including duplicate-confirm replay):

- Opening, allocation and 17 one-paise expenses; proposal alone leaves snapshot unchanged.
- Correction moves amount/date/bucket to Food on leap day, retaining logical ID/revision 2;
  undo leaves its logical transaction inactive. Report totals exclude both original versions.
- March total 17 paise, February day 2 paise, cross-month week/range 19 paise;
  current available 981 paise and Travel balance -1 paise.
- A separate invited owner with the same bucket name has only its own 99-paise expense.
- Calendar pages contain exactly 8/8/1 unique entries, unchanged full-day totals,
  deterministic replay, bounded owner-free callbacks, empty day and invalid page handling.
- Snapshots for both owners and counts of every application table remain unchanged by reads.
- Actual target versions expose bucket_name/effective_month/amount/target/revision:
  February revisions 1/2 (1 then 2 paise), April removal revision 3, May revision 4 (1 paise).
  January has no target, February selects revision 2, March carries it, April remains removed
  despite May's current target. No current-target fallback into history.
- Negative/subpaise inputs -0.01, 0.001, 0.009 fail before any table-count/snapshot change.
- Unit boundaries additionally verify signed integer formatting through signed-bigint minimum
  and above float precision, rejecting bool/float/string/None rather than coercing or rounding.

## Remaining boundaries

- Calendar source needed no changes: real-store checks passed as written.
- Domain format_money exists and uses ₹-0.01; this report's established spelling is -₹0.01.
  The local formatter also rejects non-integer values. Retained it rather than change sibling
  code or observable report formatting; both use exact integer arithmetic.
- Day/partial-range summaries do not invent monthly target comparisons; monthly reports use history.
- Controller routing/private-chat authentication remains parent-owned and untested here.
  Parent must supply the authenticated current owner; callbacks never encode owner identity.
- Mutation-time domain warnings, concurrent read coherence across separate snapshot/spending
  calls, full-suite integration and production/live Telegram are not claimed by these tests.
- No live providers, Telegram calls, network writes, full suite, or publishing performed.
