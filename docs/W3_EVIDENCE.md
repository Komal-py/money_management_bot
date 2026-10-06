# W3 setup/access evidence

Scope: `src/budget_bot/services/onboarding.py`, `access.py`, package docstring `__init__.py`, `tests/test_onboarding.py`, and this evidence only. Saved untracked files were read and retained/extended. No sibling source, shared configuration, lock files, controller, or confirmation implementation changed.

## Implemented contract

- Explicit `OnboardingService(store).initialize()` uses `store.engine`, a private SQLAlchemy metadata object, and only the configured schema's `setup_states` table. Call after store initialization. Constructor performs no DDL. The store owns engine disposal.
- `handle(owner_id, text, now)`, `back(owner_id, now)`, `cancel(owner_id, now)`, and `active(owner_id)` are available. Responses contain text, keyboard, review, and done. Invalid values raise `BudgetError` without advancing the draft; the parent should render the safe error and retain the current question.
- Durable phases: opening, name, allocation, more, review, cancelled. `/start` resumes. `/restart` and Edit invalidate the draft's pending proposal and start questions again. Back from review cancels that proposal and returns to add-more/review; Back from allocation/name revisits prior questions. Cancellation leaves a tombstone to ignore late setup buttons. Completed owners get a done response, never another opening.
- Exact money parser, zero opening/zero allocation, trimmed case-insensitive unique names, reserved pool rejection, source-fund checks, maximum eight ordered actions. Zero allocation is omitted rather than submitted as an invalid zero allocation action. A full batch requires review before additional buckets can be created through normal budgeting after setup.
- Every public draft operation checks active owner through the actual store. Owner-scoped setup advisory locks serialize draft changes; setup never holds the store's user-row lock across a nested store call. Store connection settings bound lock waits to 5 seconds, statements to 15 seconds, idle transactions to 20 seconds.
- Draft request UUID is durable before proposal handoff. Repeated/concurrent review and reconstruction retain the same request and exact plan identities. Interrupted draft-save after proposal commit recovers the pending store proposal. Expiry does not silently renew a review. A confirmation that wins during resume returns completed rather than falsely claiming no writes.
- Only `store.propose` creates reviews. This service never confirms or writes accounts, transactions, postings, or onboarding status. Review keyboard is empty: parent/W6 owns rendering and Confirm/Edit/Cancel binding. Tests exercise store confirmation explicitly as an external authority.
- AccessService accepts parser command names `invite`, `users`, `revoke` and a list of string args. All requests are administrator-gated through access-only store APIs. Listing renders Telegram ID, access status, and role only. Revoke validates a positive integer Telegram ID, does not expose financial records, and preserves store protection for administrators. `/start` redemption remains a parent call to `store.redeem_invite`.

## Execution and TDD

Only supplied `BUDGET_TEST_DATABASE_URL` was used, with `BUDGET_TEST_SCHEMA=test_w3`. No URL/credential was printed, embedded, or loaded from a file. A shell nonempty check preceded pytest, so the shared conftest's fallback secret-file read was not reached. All data is isolated in test_w3, with unique synthetic owners per test; no schema drop, database/role creation, provider, Telegram, install, or full-suite run.

Environment set in bash:

    export PYTHONPATH='C:/Users/FL_LPT-657/Projects/telegram_bot/.worktrees/w3/src'
    export BUDGET_TEST_SCHEMA=test_w3
    export PGOPTIONS='-c lock_timeout=3000 -c statement_timeout=10000'

The actual BudgetStore applies its own bounded connection timeouts noted above.

Saved targeted baseline, before edits:

    test -n "$BUDGET_TEST_DATABASE_URL" && 'C:/Users/FL_LPT-657/Projects/telegram_bot/.venv/Scripts/python.exe' -m pytest tests/test_onboarding.py -q --tb=short

Result: 1 passed. An earlier Python `-c` environment-presence guard was blocked by the tool; the shell `test -n` alternative above succeeded. No provider calls were involved.

Incremental regressions and fixes:

- Guided flow: 2 failed / 1 passed because answers never left opening; implemented transitions and proposal handoff. Green: 3 passed. PostgreSQL returned equivalent expiry instants with different offsets, so replay tests compare aware instants rather than raw offset spelling.
- Validation/navigation: 8 failed / 11 passed (bad names accepted, missing bounds/Back); green: 19 passed.
- Lifecycle/recovery: 4 failed / 20 passed (interrupted handoff, expiry, misplaced callback, saved partial state); green: 24 passed.
- Access: 11 failed / 24 deselected because module was missing; implemented against actual APIs; green: 35 passed.
- Reserved name/concurrency/reconstruction: 1 failed / 41 passed; minimal reserved-name fix; green: 42 passed. Existing reconstruction, concurrency and no-confirm behavior passed without new implementation.
- Confirmation/resume race: 1 failed / 48 passed; completed-state handling fix; final green: 49 passed in 15.37 seconds.

Final commands actually executed:

    'C:/Users/FL_LPT-657/Projects/telegram_bot/.venv/Scripts/python.exe' -m pytest tests/test_onboarding.py -q --tb=short
    'C:/Users/FL_LPT-657/Projects/telegram_bot/.venv/Scripts/python.exe' -m ruff check src/budget_bot/services/onboarding.py src/budget_bot/services/access.py src/budget_bot/services/__init__.py tests/test_onboarding.py
    git diff --check

Results: 49 passed; Ruff all checks passed; diff check exit 0. Scoped Ruff/diff checks also followed each green implementation increment.

## Parent integration / remaining boundaries

- Parent must call setup initialize, route active drafts and setup callbacks, render safe validation errors, and hand returned store reviews to W6 without reproposing them under a new request ID. W6 review rendering, persistent graph resume, and Telegram ingress were not integrated or exercised in this worker.
- Parent performs invite redemption and displays completed setup's menu/calendar. Access responses are `{text, keyboard}`; setup responses additionally contain `review` and `done`.
- The frozen `handle(owner_id,text,now)` API has no update ID. Shared transport/inbox/controller must deduplicate repeated text updates and bind navigation to the current session. This service guarantees proposal handoff replay and ignores misplaced setup buttons, not exactly-once consumption of arbitrary text answers or old unversioned Back/Cancel buttons across a restart.
- All tests use real PostgreSQL and the actual store/planner. Monkeypatches only inject interruption/race timing or prohibit financial methods in access tests. No W6 end-to-end, live-app, provider, or deployment claim is made.
