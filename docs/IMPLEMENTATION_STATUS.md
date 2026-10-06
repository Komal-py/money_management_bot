# Implementation progress

## Current coordinator verification

Implementation remains authorized. Main includes settings/private controller boundaries, financial planning (W1), PostgreSQL persistence/migrations (W2), command parsing/durable Telegram transport (W4), deterministic reports/calendar components (W7), and W8's frozen-baseline acceptance/review/CI/runbook artifacts.

`python .private/verify_wave.py` returned exit 0 after exercising **all 17 current main test files exactly once**, sequentially in their authorized schemas. Parsed JUnit: **252 collected, 251 passed, 0 failures, 0 errors, 1 strict expected failure**. The expected failure is missing `/calendar` controller routing, not passing acceptance. Source/test/migration/configuration hashes were unchanged throughout verification. Evidence: ignored `.coordination/wave-verification/summary.json` and per-schema XML/logs.

| Schema | Verified result |
|---|---|
| test_w2: financial core, settings, controller, storage | 115 passed |
| test_w4: command/transport and real-store transport integration | 67 passed |
| test_w7: reports/calendar and real-store report integration | 58 passed |
| test_w8: independent baseline acceptance | 9 passed, 1 strict xfailed |
| test_integration: real planner/store tracer bullets | 2 passed |

Full `ruff check src tests` and `git diff --check` passed. Fresh independent review of the exact coordinator production diff passed with no security concerns or logic errors (`docs/COORDINATOR_REVIEW.json`). Its nonblocking suggestions are covered by a real-store 25-part/two-cycle delivery regression and the documented due_at/retry ordering limit. Reviewer did not rerun tests; coordinator did. This verifies the current integrated source, not a finished runnable bot or live Telegram/AI service; no release security sign-off or successful distribution build is claimed.

## Concurrent wave: returned contributions

| Worker | Process evidence | Coordinator disposition |
|---|---|---|
| W1 financial hardening | Exit 0 | Source e510153 integrated as caa9db1; 72 targeted tests independently passed, then included in the frozen-tree regression. |
| W2 persistence hardening | Exit 0 | Source 77c68d3 integrated as fb6e2bd; 30 targeted tests independently passed. Target IDs/month-local revisions and monotonic durable Telegram offsets verified. |
| W4 transport hardening | Exit 0 | Source ba2a276 integrated as 8b8a960; 64 targeted tests independently passed before coordinator fixes. Final transport regression includes two new bot-isolation/ordered-claim regressions. |
| W7 report integration | Exit 0 | Source da2fa12 integrated as bc254cb; 58 targeted tests independently passed. Main integration exposed stale expectations about cross-month revision numbering; reconciled against the actual planner/store contract. |
| W8 review/CI/runbook | Exit 0 | Source 60b50ba integrated as 9d768f6; 9 acceptance tests pass with one known calendar xfail. Review applies to frozen fdcf05e, not unseen sibling work. Remote CI has not run. |
| W3 onboarding/access | Exit 0 observed in process readback | Saved source 8817faf remains in worker worktree, not independently accepted or integrated yet. |
| W5 AI and W6 LangGraph | Last process readback: running | Completion/artifact acceptance is not inferred. |

Main integration initially failed one gap-offset expectation and one old target uniqueness constraint. Applied Alembic head only to approved idle test schemas test_w4/test_w7/test_w8/test_integration; exact readback verified revision 0002_target_month_revision, new constraint and unchanged target-audit row counts (including 22 existing rows in test_w7). Production schema/database was not migrated.

Coordinator then reproduced two genuine transport defects with failing tests: another bot's inbox/completion/outbox was unscoped, and reply chunks were ordered by random UUID. Bot-scoped ingress/completion/claim filters and persisted position ordering now pass real-PostgreSQL regression. This does not guarantee global delivery order across simultaneous transports or retries; initial production topology remains one bot poller per project schema. Transport tests use the real Telegram SDK with controlled request adapters, not live Telegram.

## Remaining and runtime state

Integrate/verify onboarding, AI and durable LangGraph contributions; wire startup and controller routes; resolve W8's remaining concrete findings; update CI for all new schema-specific suites; exercise complete application acceptance and packaging; perform bounded live smoke checks and publish/read back the approved repository. Calendar controller dispatch remains a known failure. W8's prior no-isolation build failed because hatchling was unavailable; build success has not been established. **The bot application is not running; no GitHub push has occurred.**

The earlier 147-test baseline and failed first-wave worker exits are historical evidence, not current totals. Useful W1/W2 saved code from that wave was recovered and verified. The later all-eight concurrency request superseded dependency waves; workers were launched together in isolated worktrees with low/medium effort and 1800-second limits. Process success is not component acceptance. No duplicate workers were launched for the repeated request.

## Approvals and resource boundaries

Project-only AI credential/config copy was explicitly approved and completed; credentials remain ignored and source Hermes configuration unchanged. Dedicated restricted app/test resources use the approved existing PostgreSQL server; only project-related files/resources are accessed. The three-hour temporary wake-lock expired normally; no renewal or permanent power-plan change. Model tariffs are unverified; no cheaper-route or dollar-cost claim.
