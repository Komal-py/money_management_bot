# Report clarification coordinator repair — not release sign-off

R3 returned a concrete integration gap: real interpreter report clarification could not finish with a query through the durable graph. This is coordinator-owned workflow source; R3 original 75 passes did not cover it.

## Observed verification

- `tests/test_workflow_query_clarification.py` real PostgreSQL/checkpointer and real Responses SDK over controlled HTTP first failed with missing `query` after clarification.
- Added a guarded query termination for compatible report clarification only, no financial review/actions/edit/resolved funding. The returned backend-only `query_received_at` is the initial receipt, not the later answer timestamp.
- A second negative tracer failed: a mutation clarification could acquire report eligibility after the provider changed its next missing field. Persisted `report_clarification` now prevents that false-to-true transition.
- Prior affected command: `python .private/run_tests.py main --schema test_w6 tests/test_workflow_query_clarification.py tests/test_workflow.py tests/test_workflow_integrated.py -q --tb=short --junitxml=C:/Users/FL_LPT-657/Projects/telegram_bot/.coordination/query-origin-affected-green.xml`
- That run observed **31 passed**, Ruff and `git diff --check` passed. These are selected suites, not a new aggregate complete-main count; the independent review below subsequently rejected the initial bucket-only eligibility.
- Positive behavior survives PostgreSQL graph reopen, an additional clarification, and midnight crossing with original receipt. All finance snapshots stay unchanged. Negative cases cover funding, expense description, review Edit, changed clarification origin, invalid date range and unknown bucket.

## Independent-review intent repair — strict RED/GREEN

- `.coordination/query-clarification-independent-review.log` is **FAILED** and remains unchanged. It found that initial `missing_fields=['bucket_name']` with empty actions is shared by expenses and reports, so the prior guard could abandon a known expense when the next provider result was a query. Passing tests did not supersede that finding.
- Reproduced RED before any source edit: `python .private/run_tests.py main --schema test_w6 tests/test_workflow_query_clarification.py -k bucket_only -q --tb=short --junitxml=C:/Users/FL_LPT-657/Projects/telegram_bot/.coordination/query-bucket-origin-red.xml` returned **1 failed, 7 deselected**, specifically `assert 'query' not in output`.
- Added the focused report-origin period -> bucket clarification -> query case before changing source. The adapter normalizes bucket clarification text, so the test checks the bucket question and persisted `missing_fields` rather than the supplied wording. The final pre-fix two-case run returned **1 failed, 1 passed, 7 deselected** in `.coordination/query-bucket-origin-pre-fix.xml`: the bucket-only expense remained RED and the report-origin continuation already worked.
- Minimal source fix: initial report eligibility requires at least one report-only field (`period`, `start`, or `end`), no actions, and only report-compatible missing fields. Shared `bucket_name` alone cannot establish eligibility. The existing sticky-false continuation logic is unchanged, so a mutation-origin clarification cannot later acquire report permission. Previously established report origin may still ask for `bucket_name` and finish.
- GREEN command: `python .private/run_tests.py main --schema test_w6 tests/test_workflow_query_clarification.py tests/test_workflow.py tests/test_workflow_integrated.py -q --tb=short --junitxml=C:/Users/FL_LPT-657/Projects/telegram_bot/.coordination/query-bucket-origin-green.xml` returned **33 passed**. `uv run --no-sync ruff check src/budget_bot/workflows/workflow.py tests/test_workflow_query_clarification.py` and `git diff --check` passed.
- The report-origin period -> bucket case closes and reopens the PostgreSQL checkpointer before query completion, verifies original ISO `query_received_at`, then rejects a further answer with `no_question`. The prior period -> repeated period -> query restart case is preserved. Financial snapshots and pending reviews stay unchanged. Funding, mutation-description, changed-origin, bucket-only expense, pending Edit, invalid dates and unknown bucket remain guarded.

## Pending and limits

- An **initial bucket-only report clarification is intentionally ineligible** for query completion. The schema has no independent trusted clarification-intent discriminator. Supporting it safely needs trusted workflow context, not heuristics from request text, question wording, empty/provider actions, or the next provider query. Cancel and start a complete report request instead.
- This source change affects newly established eligibility; it does not migrate previously persisted checkpoints that the rejected patch may already have incorrectly marked report-compatible.
- The prior independent review remains failed; the parent must independently review this repair and complete controller/router/workflow integration and full frozen regression. The 33 passes are selected affected suites, not release sign-off or a complete-main aggregate.
- Only approved existing test-only PostgreSQL `test_w6` / `test_w6_workflow` and controlled Responses SDK HTTP transport were used. No new database/schema, containers, servers, live provider/Telegram calls, production migrations, owner-finance changes, push or publication. Unrelated controller/service/onboarding/calendar work is outside this repair.
