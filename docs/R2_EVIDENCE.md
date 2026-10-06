# R2 ingress/access recovery evidence

## Scope and interfaces

Owned changes only: `src/budget_bot/controller.py`, `tests/test_controller_ingress.py`, `tests/test_controller_lifecycle.py`, this evidence file. Existing `tests/test_controller.py` remains unchanged.

Constructor remains `BudgetController(store, workflow, onboarding, reports, access, command_parser=None, *, conversation=None)`. The sole public addition is optional keyword-only `conversation`; omitted construction lazily imports `ConversationRouter(store, workflow, reports)`. Dispatch uses exactly `dispatch(owner_id, text, received_at, now, *, query=None, callback_data=None)` and `menu()` from the frozen contract. Local tests inject only this R3 boundary; no conversation production implementation was added/copied.

Trusted actor and chat IDs must both be positive, non-bool integers below 2**63 and equal in a private chat; bot senders are rejected before store access. Callbacks use their own sender and message, never top-level message identity. Unknown actors can redeem only an unmodified `/start <URL-safe-token>`; malformed quoted, suffixed, multiargument, bot-qualified and case-changed presentations cannot consume the invitation. Revoked identities never redeem or resume.

Registered `/start`, setup answers/buttons, access-only admin commands, timezone, cancellation (including graph questions without stored proposals), commands, NL/query/calendar handoff are wired. Setup reviews use W5 deterministic rendering and exact revision-bound Confirm/Edit/Cancel buttons. Confirm always passes through workflow.decide; duplicate confirmations stay immutable. Only BudgetError/CommandError become safe replies, not unexpected database/transport exceptions.

Setup Edit uses the existing workflow owner advisory lock plus onboarding draft transaction lock, then validates draft step/request and pending owner/request/revision/expiry before cancelling the old proposal and starting opening questions. Controller's private helpers currently cooperate with existing OnboardingService `_lock`, `_load`, `_cancel_review`, `_new_state`, `_save`, `_prompt`, and BudgetWorkflow `_locked`; no unowned API was changed. This implementation detail should be retained or replaced with an equivalent atomic public service operation by the coordinator, not replaced by a racy check followed by handle('edit'). Cancelled-result replay marks only the exact matching setup draft cancelled and cannot cancel a newer review.

## Environment and actual TDD execution

All commands ran from `C:/Users/FL_LPT-657/Projects/telegram_bot/.worktrees/r2` with:

- `PYTHONPATH=C:/Users/FL_LPT-657/Projects/telegram_bot/.worktrees/r2/src`
- `BUDGET_TEST_SCHEMA=test_w2`
- inherited `BUDGET_TEST_DATABASE_URL` (never printed or passed as argv)
- Python `C:/Users/FL_LPT-657/Projects/telegram_bot/.venv/Scripts/python.exe`

Below, `PYTHON` denotes that exact executable. All fixtures use unique explicit synthetic Telegram IDs and real BudgetStore/OnboardingService/AccessService/ReportService/PostgreSQL with actual BudgetWorkflow LangGraph and its permitted in-memory checkpointer. Initialization is scoped/idempotent; no truncate/drop or shared audit-data deletion. OperationalError injection models outages at existing store boundaries; it is not a production stub or claimed live network test.

1. Preserved regression first: `PYTHON -m pytest tests/test_controller_ingress.py -q` -> 8 failed, 7 passed (6.13s). Intended failures: redemption, revoked reply, five strict actor cases, callback sender fallback. Minimal ingress implementation then identical command -> 15 passed (2.08s).
2. New lifecycle tests first: `PYTHON -m pytest tests/test_controller_lifecycle.py -q` -> 10 failed (3.44s). Missing routing meant setup never proposed, admin/timezone/cancel/query paths were absent and DB timezone failures were not reached. Minimal lifecycle implementation then `PYTHON -m pytest tests/test_controller.py tests/test_controller_ingress.py tests/test_controller_lifecycle.py -q` -> 1 failed, 30 passed (8.74s). Remaining failure was the test expecting a rupee glyph, whereas authoritative W5 renderer uses `INR`; corrected the assertion to exact existing rendered amounts, not production financial semantics.
3. New old-cancel replay regression first: `PYTHON -m pytest tests/test_controller_lifecycle.py -q` -> 1 failed, 10 passed (5.99s). A returned old cancellation reset a newer setup review. Exact request-bound draft transition fix then combined three-file command -> 32 passed (5.06s).
4. Ruff first run exposed 31 F811 fixture-import shadow warnings in the new lifecycle tests. Replaced imports with explicit fixture aliases (no rule suppression); final Ruff passes.
5. New current-question cancellation regression first: `PYTHON -m pytest tests/test_controller_lifecycle.py -k cancel_current_workflow -q` -> 1 failed, 11 deselected (4.43s), because /cancel left the real graph question open. Added workflow.answer('/cancel') with only no_question fallback.
6. New callback message-source regression first: `PYTHON -m pytest tests/test_controller_ingress.py -k callback_chat -q` -> 1 failed, 15 deselected (2.07s). Top-level private message incorrectly masked group callback origin. Callback message source fix applied. (A chained current-question run after this command did not execute because the first command failed.)
7. Added preservation checks for foreign Confirm/Edit/Cancel and DB failures at get_user, redeem_invite, propose, confirm. Combined command -> 3 failed, 38 passed (8.58s); all ownership replies and mutation guards passed, but direct proposal/readback dictionary comparison differed in equivalent PostgreSQL timezone serialization. Changed comparison to before/after database readbacks; no production change for this test artifact.
8. Final actual command: `PYTHON -m pytest tests/test_controller.py tests/test_controller_ingress.py tests/test_controller_lifecycle.py -q` -> **41 passed, 0 failed** (5.85s), including current-question and callback-source regressions. `PYTHON -m ruff check src/budget_bot/controller.py tests/test_controller*.py` -> **All checks passed**. `git diff --check` -> exit 0.

Coverage includes strict invites/replay, revoked access, trusted callback identity, onboarding no-write review, exact Confirm and duplicate, Back and safe invalid answer, exact/stale/cross-owner Edit, state-revision renewal, stale Cancel replay, cancellation/restart, admin member denial and metadata-only output, timezone, financial command review/cancel, graph question cancellation, R3 seam forwarding, foreign financial review isolation, retryable database errors.

## Remaining integration boundaries

R3 production NL/report/calendar implementation and lazy default constructor path were not exercised here: only the frozen injected dispatch seam is tested. Coordinator must merge and verify the actual assembled default path and remove the historical calendar expected failure in its owned acceptance boundary. These local tests do not claim full application acceptance, persistent-checkpoint restart acceptance, release, CI, live Telegram/AI execution or deployment.

No changes to dependencies/locks, financial semantics, credentials, Hermes configuration, unowned source, server/container provisioning or owner finances. No sibling polling, full suite, build or push. Historical baseline counts are not this work's evidence. Additional controller test files must be included in coordinator/R5 exhaustive suite accounting.
