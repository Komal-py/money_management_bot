# R2 stale setup callback fix evidence

## Scope and root cause

Repair worktree: `C:/Users/FL_LPT-657/Projects/telegram_bot/.worktrees/r2`, based on `be8419f`. No commit created; parent review is pending.

The independently supplied reproduction captured an actual Cancel button from an earlier draft, cancelled via public `/cancel`, created a new setup review, then clicked the old button. The previous unbound `setup:cancel` cancelled the newer pending review: `db.get_pending(owner)` became `None` instead of remaining equal to the captured pending review.

## Repair

- `OnboardingService.handle(owner_id, text, now, *, expected_request=None)` preserves the textual API and adds public draft fencing. When provided, the expected request is checked against the current noncancelled draft under the existing onboarding transaction advisory lock, before draft or review mutation; absent, replaced, cancelled, or completed drafts raise `BudgetError('stale_setup', ...)`.
- All emitted setup Back/Cancel/Review/Add more callbacks use `setup:<action>:<canonical-request-uuid>`, including expired-review Back/Cancel.
- The controller strictly parses allowed actions and canonical UUIDs, rejects unbound/malformed setup callbacks without resuming or cancelling a draft, and calls the public service API with `expected_request` in the same locked service operation.
- Public `/cancel` remains intentional current-interaction cancellation. Existing `rev:` Confirm/Edit/Cancel identifiers and validation paths are unchanged.
- Impacted lifecycle/ingress tests now use actual generated keyboard callback data. Original reproduction assertions remain intact.

## Executed RED/GREEN checks

All pytest commands ran from `C:/Users/FL_LPT-657/Projects/telegram_bot` using the parent-approved launcher and isolated `test_w2` schema. No connection URL was inspected or printed.

1. Independent RED reproduction:
   ```text
   python .private/run_tests.py r2 --schema test_w2 tests/test_controller_setup_replay.py -q --tb=short
   1 failed in 5.54s
   ```
   Expected failure: newer pending review was removed by the old generated Cancel button.
2. Public guard RED/GREEN:
   ```text
   python .private/run_tests.py r2 --schema test_w2 tests/test_controller_setup_replay.py -k 'public_expected or revalidated' -q --tb=short
   RED: 4 failed, 1 deselected in 5.32s
   GREEN: 4 passed, 1 deselected in 2.87s
   ```
   RED exposed the missing `expected_request` API. GREEN covers absent/replaced/cancelled drafts and a callback delayed before acquiring the setup lock while `/restart` replaces its draft.
3. Generated callback RED:
   ```text
   python .private/run_tests.py r2 --schema test_w2 tests/test_controller_setup_replay.py -k 'old_setup_cancel or all_setup_buttons' -q --tb=short
   2 failed, 4 deselected in 5.72s
   ```
   The original mutation failure remained; generated buttons lacked the draft UUID.
4. Expired-review callback RED:
   ```text
   python .private/run_tests.py r2 --schema test_w2 tests/test_controller_setup_replay.py -k expired_review -q --tb=short
   2 failed, 6 deselected in 5.83s
   ```
   Both Back and Cancel were unbound.
5. Strict rejection and other-draft callback RED:
   ```text
   python .private/run_tests.py r2 --schema test_w2 tests/test_controller_setup_replay.py -k 'unbound_or_malformed or other_draft' -q --tb=short
   22 failed, 8 deselected in 11.12s
   ```
   Includes unsafe unbound Back/Cancel, rejected malformed forms, and every setup action from stale/foreign drafts. Assertions verify unchanged draft/pending/financial state and no disclosed draft content.
6. Focused GREEN:
   ```text
   python .private/run_tests.py r2 --schema test_w2 tests/test_controller_setup_replay.py -q --tb=short
   30 passed in 9.43s
   ```
   Includes the original reproduction, atomic fencing, strict parser rejection, stale/foreign callbacks, generated canonical UUIDs, and the Telegram 64 UTF-8 byte limit.
7. Authorized controller regression GREEN:
   ```text
   python .private/run_tests.py r2 --schema test_w2 tests/test_controller.py tests/test_controller_ingress.py tests/test_controller_lifecycle.py tests/test_controller_setup_replay.py -q --tb=short
   71 passed in 14.15s
   ```
8. Ruff from the repair worktree:
   ```text
   C:/Users/FL_LPT-657/Projects/telegram_bot/.venv/Scripts/python.exe -m ruff check src/budget_bot/controller.py src/budget_bot/services/onboarding.py tests/test_controller_setup_replay.py tests/test_controller_ingress.py tests/test_controller_lifecycle.py
   All checks passed!
   ```
   `git diff --check` also passed.
9. Final verification after documentation/test-description updates reran the focused command followed by the authorized controller command:
   ```text
   30 passed in 9.93s
   71 passed in 15.36s
   ```
   Ruff again returned `All checks passed!`; `git diff --check` passed. Final worktree status contains only the six authorized repair files, and HEAD remains `be8419f`.

## Coordinator follow-up (after worker return)

- Independent fresh review initially failed the non-onboarded `cal:`/`day:` fallback: it called setup `/start` and could create or restart an absent/cancelled draft. All eight real PostgreSQL repros failed; `.coordination/r2-calendar-fallback-red.xml` is preserved.
- The callback fallback now returns a non-mutating instruction to send `/start`. Final controller suite: **79 passed** in `.coordination/r2-final-coordinator-green.xml`.
- Real backend onboarding suite first caught a stale expectation that a restarted prompt's callback UUID equals the old draft. Visible prompt/control labels remain the same; the test now requires different draft-fenced callback IDs on restart. Backend suite: **49 passed**, `.coordination/r2-final-onboarding-green.xml`.
- Full changed-file Ruff, whitespace/static added-line checks passed. A separate final 180-second low-effort review **passed**, no concerns/errors/suggestions; `.coordination/r2-final-independent-review.log`. Earlier failed reviews are preserved.
- Coordinator added `tests/test_onboarding.py` to repair scope for the necessary callback-ID expectation. Worker's original six-file/uncommitted report above remains historical. Parent will commit/integrate precisely these repaired files, then exercise assembled controller/router paths; no application/release sign-off yet.

## Changed files and limits

Only the authorized files were changed: `src/budget_bot/controller.py`, `src/budget_bot/services/onboarding.py`, `tests/test_controller_setup_replay.py`, `tests/test_controller_ingress.py`, `tests/test_controller_lifecycle.py`, and this evidence file.

No full suite, live Telegram calls, credential reads, provisioning, commits, pushes, runtime/report/conversation edits, sibling polling, or global Hermes changes were performed. Backend onboarding tests outside the requested controller set were not run or edited.
