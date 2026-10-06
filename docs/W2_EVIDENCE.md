# W2 storage verification

Scope: storage source, Alembic, storage tests, this evidence only. No transport/controller/settings/conftest edits, dependency installation, full-suite run, live provider calls, or publishing. Session started on worker/w2 with a clean git status; no saved untracked files were present. Existing 19 saved storage tests were retained and reviewed before edits.

## Execution environment

Windows host, bash terminal. Interpreter:
`C:/Users/FL_LPT-657/Projects/telegram_bot/.venv/Scripts/python.exe`

Session setup: `export PYTHONPATH="$PWD/src" BUDGET_TEST_SCHEMA=test_w2`.
The test URL was supplied through `BUDGET_TEST_DATABASE_URL`; it is not included here. All database work used the existing approved database and schema `test_w2`. Fixtures assert the schema before touching tables. Connections enforce search_path=test_w2, lock_timeout=5s, statement_timeout=15s, idle_in_transaction_session_timeout=20s. No database, role, or server was created.

## Red / green evidence

Commands below use the interpreter path above as PYTHON (notation only, not an additional executable).

1. Before editing: `PYTHON -m pytest tests/test_storage.py -q --tb=short`
   Actual result: 19 passed in 35.99s. These tests double financial planning and are PostgreSQL storage/contract evidence, not production planner integration evidence.
2. Added `tests/test_storage_planner.py`, whose fixture restores the actual `budget_bot.domain.planner.plan` before any planning.
   `PYTHON -m pytest tests/test_storage_planner.py -q --tb=short`
   Actual initial result: 3 failed, 4 passed in 18.73s. Exact failures: reviewed target IDs replaced at commit; offset stalled at 41 after durably storing update 900; pending_updates rejected bot_id.
3. After retaining reviewed target IDs, isolated target test:
   `PYTHON -m pytest tests/test_storage_planner.py::test_real_ordered_targets_keep_reviewed_ids_and_month_revisions -q --tb=short`
   Actual result: 1 failed in 7.41s. The November target stored revision 4 while the actual planner reviewed revision 1. This drove month-scoped revision persistence and its constraint migration.
4. After fixes: targeted set initially 26 passed in 50.18s, scoped Ruff passed, diff check passed.
5. Additional rollback/migration/report tests: one assertion compared equivalent timestamp strings with different UTC offsets (database session timezone versus initial in-memory review). Changed that test to compare persisted pending state before and after failure; no production timezone change was made.
6. Final command:
   `PYTHON -m pytest tests/test_storage.py tests/test_storage_planner.py -q --tb=short && PYTHON -m ruff check src/budget_bot/storage alembic tests/test_storage.py tests/test_storage_planner.py && git diff --check`
   Actual result: 30 passed in 41.46s; All checks passed; git diff --check exit 0.

## Changes

- Persist exact reviewed target metadata UUIDs instead of minting replacement target IDs.
- Preserve every ordered target action, including removal, with revisions scoped to bucket/effective_month as the actual planner does.
- Migration `0002_target_month_revision` replaces only the target unique constraint. It preserves all existing target audit rows and revision values. Downgrade deliberately fails transactionally if month-local revisions collide under the old uniqueness rule; it never renumbers immutable history. Original migration and audit/source-funds guards remain unchanged.
- Durable polling offset advances monotonically to highest durably received update ID + 1. Numeric Telegram ID gaps no longer stall acknowledgement. Older/replayed updates remain durable and cannot regress the cursor. Inbox receipt and cursor advancement stay in the same transaction.
- Backward-compatible keyword-only `bot_id` filters on pending_updates and complete_update permit exact bot inbox scope. Unscoped completion still rejects duplicate IDs across bots rather than guessing.
- Storage test fixture applies migrations to test_w2 before truncating only application-owned tables. The saved gap assertion now expects the corrected high-water mark; saved migration assertion names the new head. No other saved test coverage was removed.

## Actual planner integration versus doubles

The 19 saved tests retain local_plan, including deliberately unsafe planner doubles for defense-in-depth tests. The 11 additional PostgreSQL tests use a fixture wired to the actual planner. Seven exercise real financial planning or target migration with real-planner records; four exercise inbox/outbox persistence directly and do not claim financial-planning coverage.

Verified without production changes:

- Multiple corrections and undo of one logical transaction in a single batch: revisions 1–4 retained, exact previous-state chain, original transaction/batch IDs, restored balances, undone expense excluded from reports.
- Corrected financial batch undo uses current versions in reverse order. Undo/correction requiring unavailable source funds is rejected with the actual BudgetError code and no pending/financial writes.
- Corrections move current report amount/date/bucket; old date/bucket no longer contributes; negative expense balances remain permitted with warnings and no implicit funding.
- Edited/stale callbacks cannot commit old reviews; state-revision changes renew review; reviewed identities survive revalidation; ownership and expiry checked; committed replay/cancel returns immutable prior result.
- Injected final result insertion failure rolls back accounts, transactions, every audit/ledger/target/metadata row, and request status; retry commits successfully with actual monthly-target warnings.
- Injected inbox insertion failure cannot advance offset.
- Injected outbox insertion failure cannot complete inbox or leave partial replies. Lease expiry boundary rejects both ack and fail; reclaimed lease rejects the old token. Saved tests additionally verify concurrent disjoint claims and atomic malformed-reply rollback.
- Migration roundtrip preserves existing audit; unsafe downgrade rollback preserves rows and migration head. Saved tests cover schema/timeout settings and direct SQL append-only, ownership, opening, source-funds, and audited-current guards.

## Concrete integration limits

- `src/budget_bot/telegram/transport.py` currently calls pending_updates and complete_update without bot_id. Parent/W4 must adopt the optional bot_id filters for multi-bot ingress isolation; this worker did not edit transport. Storage API support and database isolation are tested, but transport use is not claimed.
- Receipt-offset semantics require the poller to durably save returned Telegram updates before requesting the next offset; current storage tests do not claim live Telegram verification.
- Coordinator must apply Alembic head for existing deployed schemas; initialize creates tables but is not an upgrade substitute for an existing old constraint.
- No full-suite, controller/workflow integration, deployment, provider, price, or finished-app claim. Parent integrates and publishes.
