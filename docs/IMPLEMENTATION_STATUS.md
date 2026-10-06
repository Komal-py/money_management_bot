# Implementation progress

## Current verified baseline

Implementation remains authorized. Main includes secret-safe settings, the private-chat/controller boundary, W1 pure financial planning, W2 PostgreSQL persistence, W4 command parsing/durable Telegram transport, and W7 deterministic reports/calendar components.

Latest coordinator regression: `python .private/run_tests.py main tests -q --junitxml=.../.coordination/batch-regression.xml` returned **147 passed in 48.82s**. Parsed JUnit: 147 tests, 0 failures, 0 errors, 0 skipped. These are the currently implemented tests, not full application acceptance. Real planner/store integration is exercised; report/calendar tests remain component tests with injected store facts, and controlled transport tests are not live Telegram evidence.

Scoped Ruff passed for the changed storage test and W7 source/tests. `git diff --check` passed. No full-project lint/build/security sign-off is claimed.

## Consolidated five-worker results

| Worker | Process result | Coordinator disposition |
|---|---|---|
| W1 financial core | Exit 1; provider token-rate limit | Saved financial code already integrated; regression passes. Worker exit is not represented as success. |
| W2 storage | Exit 1; provider token-rate limit | Saved persistence code already integrated. Fixed the integrated test harness to use the real shared BudgetError; all 19 storage tests pass in the latest regression. |
| W4 Telegram | Exit 0 | Saved code independently tested and already integrated. |
| W5 AI | Exit 1; provider token-rate limit | AI adapter remains unfinished; no passing adapter or live-app claim. |
| W7 reports/calendar | Exit 0 | Independently reran 36 component tests, then integrated source commit ecd6b5b as ea106de; included in the latest regression. |

The initial combined test attempt used test_coordinator while storage tests require test_w2, producing 19 setup errors before those tests ran. The private runner now selects the approved test_w2 schema for main, without relaxing the schema allowlist. A subsequent run exposed two exception-class mismatches in the test-local planner. Worker-only module fallbacks were removed and the real shared exception class retained; final regression above supersedes both failed attempts.

## Remaining and runtime state

Guided onboarding/access service, AI adapter, durable LangGraph workflows, final startup wiring, real report/controller/calendar integration, independent W8 acceptance/security review, complete acceptance gates, and build/live smoke verification remain unfinished. The bot application is not running. No GitHub push has occurred.

The later W3/W5 alternate-route process records show exit -15 after interruption; they are not completion evidence. No worker process was running at the latest process-list check. Do not infer the cause of those exits from the token-rate-limit failures above.

## Approvals and resource boundaries

The earlier project-only AI credential-copy approval was explicitly granted and completed. Credentials remain ignored; source Hermes configuration was unchanged and no safety settings were disabled. Dedicated restricted app/test resources use the approved existing PostgreSQL server. The three-hour temporary wake-lock expired normally; no renewal or permanent power-plan change was made. Model tariffs are unverified; no cheaper-route or dollar-cost claim is made.
