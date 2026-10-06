# Implementation progress

## Historical eight-component integration baseline

All eight current-wave workers exited 0 and their source contributions are integrated on main. W3/W5/W6 were independently exercised by the coordinator before cherry-pick: onboarding/access **49 passed**, AI **111 passed**, durable workflow **18 passed**. Exit 0 alone was not treated as acceptance. Integrated commits: W3 `cde4707`, W5 `0b3656e`, W6 `492405b`.

Coordinator added real PostgreSQL/LangGraph/Responses-SDK/presentation seam tests. Undo/correct drafts now reach explicit owner-scoped transaction selection before review, with no model-selected record IDs. A returned review's concrete funding-clarification loop was reproduced red for both pool and income answers, then repaired using structured persisted owner context. Unknown answers keep the question open, workflow re-instantiation resumes it, and financial balances remain unchanged until explicit Confirm. Correction money is compared using the planner's canonical two-decimal representation.

Complete final frozen-source regression: `python .private/verify_wave.py` exit 0; **25 test files exactly once, 436 collected, 435 passed, 0 failures/errors, 1 strict expected failure**. Calendar controller routing remains the expected failure, not passing acceptance. Source/test/migration/config hashes were stable across the final run, including the packaging allowlist. Evidence: ignored `.coordination/wave-verification/summary.json` and per-schema XML/logs.

| Approved schema | Result from complete regression |
|---|---|
| test_w2 financial core/settings/controller/storage | 115 passed |
| test_w3 onboarding/access | 49 passed |
| test_w5 controlled Responses SDK/AI/rendering | 111 passed |
| test_w6 real durable workflow and cross-worker seams | 24 passed |
| test_w4 Telegram command/transport/store seams | 67 passed |
| test_w7 reports/calendar components | 58 passed |
| test_w8 independent baseline acceptance | 9 passed, 1 strict xfailed |
| test_integration planner/store | 2 passed |

Full source/test Ruff and Git whitespace checks passed. Local CI YAML was updated to enumerate all 25 current test files exactly once in their required schemas; parsed enumeration has no omissions, duplicates or extras. No remote CI execution is claimed.

## Review evidence

- `docs/COORDINATOR_REVIEW.json`: previous transport bot-scope/ordered-claim diff passed independent review; claim-boundary regression and retry-order limitations retained.
- `docs/WORKER_SEAMS_REVIEW.json`: onboarding/AI/workflow review `deleg_88fc132c` **failed on provider HTTP 429**, so it is not a completed sign-off. Its partial concrete funding finding was independently reproduced and fixed.
- A separate bounded 180-second, low-effort review through the already-approved `azure-kiro-sol61` route passed the exact coordinator AI/workflow diff and current files. No security concerns, logic errors or suggestions; read-only code review, not live execution or full release sign-off. Evidence: ignored `.coordination/worker-seams-review.log`.
- W8's baseline review remains limited to its frozen baseline, not unseen sibling source.

## Packaging evidence and limitation

`uv build --out-dir .coordination/dist` produced a wheel and source distribution. Archive inspection initially caught nested worker example files in the source distribution. A root-anchored source allowlist/exclusions removed them; final archive inspection found **no .env/private/coordination/worktree paths**, and every wheel Python file matches current source bytes. Build success does **not** make this a released product. The entrypoint limitation in this historical build was repaired in the R1 coordinator recovery below; a new final build and complete assembly verification remain required.

## Remaining-worker failure and startup recovery

All five new R1–R5 agents failed with exit 1 on `gpt-6.1-sol` token-rate limits; all original logs/status files and partial worktree files are preserved. None completed its assignment. The coordinator inspected R1's saved tests, reproduced failures and implemented startup/configuration. Main commit `c70de3a` contains the recovered code and `docs/R1_EVIDENCE.md`. Independent main readback: **22 targeted startup/settings tests passed** using approved test_w1 PostgreSQL and controlled real Telegram SDK; full source/test Ruff and whitespace checks passed. This selected result is not additive to the historical complete regression and does not prove assembled application acceptance.

R2 retry returned `be8419f2392f9f7cbdb0dfb8019b194de372d85b` and initially passed 41 selected tests. Its first independent review caught unbound setup Cancel replay; a real PostgreSQL repro failed. Repair `deleg_c55a257c` added draft UUID fencing under the onboarding advisory lock. A follow-up review caught a non-onboarded calendar callback fallback that restarted setup; eight repros failed and coordinator made that fallback non-mutating. Final independent review **passed**, preserving both earlier failed reviews. Repaired source is integrated on main as `4de968b`/`c799df0`. Coordinator verified **84 main controller tests**, **49 backend onboarding/access tests**, **10 baseline acceptance tests with no xfail**, and **21 actual controller/router/graph + affected clarification seams**, all passing. Startup/settings regression **22 passed**. Two old report doubles were aligned to the frozen full query/snapshot contract; actual owner/read-only assertions remain. The previous calendar strict xfail was removed after a real XPASS. See `docs/R2_COORDINATOR_REVIEW.json`; these are selected overlapping checks, not complete release acceptance.

R3 retry `proc_467b1abac6a9` returned `9c54c44fc562a1a17dc8c29212a7e9de0919a4d1` and initially passed 75 selected tests. Its first independent review failed on broad exception masking, with four reproduced failures preserved. Focused repair `deleg_20347444` returned commit `5f5a4af87e985a10041edffd7078206d602dd001`; coordinator independently reran **93 conversation/dependency/page tests** and **58 report/calendar tests**, all passed, plus Ruff/whitespace/static checks. A separate fresh bounded review **passed** the repaired component with no concerns/errors. The earlier failed review remains historical. The repair distinguishes invalid input from dependency failures and consumes original-receipt metadata for workflow queries. R3 component is integrated on main as `6582cad` and `7d058ff`; coordinator reran **151 main conversation/error/report/calendar tests**, all passing. The actual durable graph/router clarification seam below is now verified. R2 private-controller wiring is now integrated and assembled paths are verified above; independent complete application acceptance remains pending.

The workflow clarification repair returned main commit `24f9965dec83884e41987befa957a1f6868f455a`. Coordinator independently reran **33 affected tests**, then a separate bounded independent follow-up **passed** the exact repair. After R3 integration, **2 actual router + PostgreSQL checkpoint restart + Responses SDK controlled HTTP cases passed** across midnight/month-end with original receipt rendering. A report -> mutation -> report origin-switch negative was added; final affected workflow/clarification suites returned **36 passed**, and startup/settings regression **22 passed**, Ruff/whitespace passed. Initial bucket-only report clarification remains intentionally ineligible without trusted intent; Cancel and submit a complete report. Earlier failed review preserved in `docs/QUERY_COORDINATOR_REVIEW.json`. R4 recovery is running independently. R5 retry `proc_e12719cab1fd` returned commit `969bd9ca61708d81fae82b4cd36ddd5d0c69a7fe`, but is **not accepted**: coordinator reran **8 synthetic verifier tests**, current-source **33-file accounting**, and an actual offline build. The build passed; the actual archive gate failed on generic credential-pattern matches in known synthetic/public fixtures. Independent review also failed on direct-child-only timeout cleanup, incomplete deliverable fingerprint/output boundaries, unnecessary owner-environment secret reads, untracked XPASS and deletion of failed-run traces/artifacts. Coordinator separately reproduced nested-doc/example-config fingerprint omissions. Merge is held; focused repair `deleg_733ab546` runs with durable RED/GREEN verifier tests and real artifact checks. Evidence `docs/R5_COORDINATOR_REVIEW.json`. These verifier/selftest checks are not application acceptance or release success. Original R1–R5 logs retain first-attempt quota failures.

## Remaining release work

Source startup/private-controller/NL/report/calendar assembly is now integrated and selected real graph/store/controlled SDK paths pass. Remaining: independent R4 complete application acceptance/security review; R5 exhaustive schema-aware runner/CI/handoff recovery; coordinator frozen whole-main regression, dependency gates, fresh build/archive/entrypoint checks and bounded live synthetic smoke; then approved repository publication/readback and remote CI. The bot application is **not running** and **no GitHub push has occurred**.

## Authorization/resource boundaries

Implementation remains authorized in `C:/Users/FL_LPT-657/Projects/telegram_bot`. No live owner finances were recorded. Tests use only approved existing PostgreSQL resources and isolated schemas; no new server/container was created. Live Telegram/AI calls were not made by these integrated seam tests; the Responses SDK HTTP layer is controlled. The review retry used an existing approved agent route with low effort and a short runtime cap; tariffs remain unverified. Source Hermes configuration/credentials are unchanged. Project credentials/private files remain ignored. The three-hour wake-lock has expired; no renewal or permanent sleep-setting change.

Earlier failed worker attempts, the 147-test baseline and the 251-pass integrated transport stage are historical, not additive current totals. Reused worker work was preserved, and no duplicate eight-worker wave was launched.
