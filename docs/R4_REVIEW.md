# R4 independent application / security review

## Verdict and scope

NOT RELEASE READY. Reviewed production source is fd8f50d89ce36cff4b5fcabeafaca31dfb4e0f28. R4 changed only tests/test_acceptance_release.py and its owned review/evidence documents. Final complete owned-suite run: **3 failed, 59 passed**, no skips/xfails. The three strict failures are actionable recovery/privacy gates, not downgraded to passing characterizations. Production fixes belong to the coordinator; no financial semantics or dependencies were changed.

This is independent controlled application evidence: actual BudgetController, ConversationRouter, OnboardingService, AccessService, ReportService, BudgetWorkflow/StateGraph, PostgreSQL BudgetStore, durable PostgresSaver reconstruction, real OpenAI Responses SDK over httpx.MockTransport, and real python-telegram-bot over a controlled BaseRequest. No sibling service/router/workflow implementation is replaced with a passing double. Narrow fault injection deliberately raises at real persistence/report seams; real code executes before the injected transaction rollback. Some retained service tests use actual InMemorySaver; persistence claims below refer only to explicit PostgreSQL checkpointer cases.

No deployment, production migration, owner records, live Telegram/provider, dependency audit, build, fresh whole-main aggregate, concurrent production pollers, or live-language quality is verified. Startup CLI/lifecycle is source-reviewed, not invoked here: this worker does not provision/migrate a runtime schema. Parent owns those release gates. Historical 435 passes is not a fresh aggregate.

## Concrete unresolved findings

### R4-01 — High: inbox retry consumes one setup answer as two different answers

Locations: src/budget_bot/telegram/transport.py:93-115; src/budget_bot/controller.py:191-192; src/budget_bot/services/onboarding.py:152-162,215-227,245.

Strict reproduction: tests/test_acceptance_release.py:941, `test_defect_setup_answer_replay_must_not_answer_next_question`.

1. Unique synthetic registered owner starts setup.
2. Real PTB delivers opening answer `100`; actual onboarding persists opening=10000, step=name.
3. Fail `store.complete_update` after controller returns, representing a crash/DB failure before durable reply/inbox completion.
4. Retry the exact durable inbox item with no new received update.
5. Expected: same draft and next-name prompt. Observed: step=allocation, name=`100`. Financial snapshot is unchanged and there is no pending financial review, but the interaction is corrupted.

The inbox's unique key prevents duplicate receipt rows, not repeat application of an already persisted conversational answer. No incoming update identity reaches onboarding.handle. A setup advisory lock serializes concurrent calls, but does not make an answer idempotent. Fix needs durable input-to-transition/reply identity (or equivalent transactional design), not another finance lock or hiding the retry error. Similar nonfinancial side effects/clarification-answer boundaries deserve coordinator regressions; this test does not claim those additional cases reproduced.

### R4-02 — Medium: proposal survives crash but original review reply does not

Locations: src/budget_bot/controller.py:187-188; src/budget_bot/workflows/workflow.py:261-265; src/budget_bot/storage/store.py:227-250; src/budget_bot/telegram/transport.py:110-115.

Strict reproduction: tests/test_acceptance_release.py:961, `test_defect_proposal_retry_must_return_original_review_buttons`.

1. Process `/expense 10 Travel Metro` with real controller/graph/store.
2. Persist proposal, then fail inbox completion before its first outbox review exists.
3. Retry exact durable update.
4. The proposal identity, full contents, expiry instant and financial snapshot remain intact, but retry completes the inbox with a pending-interaction error and zero review buttons. The test requires the original request/revision Confirm/Edit/Cancel controls and fails.

Controller submit does not provide a stable request_id derived from bot/update identity; workflow allocates a fresh UUID and detects an existing interaction instead of replaying the original response. This is not a duplicate financial commit. It is lost recoverable presentation: the owner can cancel/re-enter but did not receive the review needed to proceed. Durable operation/reply correlation should cover proposals, answers and access/setup operations, not only Confirm. In contrast, crash AFTER financial confirmation is verified recoverable with exactly one expense at tests/test_acceptance_release.py:822.

### R4-03 — Medium, conditional: real Telegram SDK parse failure logs raw update content

Application entry: src/budget_bot/telegram/transport.py:81-91 and src/budget_bot/main.py:69-74. Actual traceback identifies installed PTB telegram/_bot.py:4784-4789. No SDK source file was opened or modified outside the worktree; this location is from execution traceback.

Strict reproduction: tests/test_acceptance_release.py:984, `test_defect_sdk_parse_failure_must_not_log_private_update`.

A controlled getUpdates reply contains synthetic callback content but omits required `chat_instance`. Real PTB raises TypeError and logs CRITICAL `Error while parsing updates! Received data was ...`, including the full synthetic callback content, identity and message envelope. The app never persisted the item (offset remains zero) and financial state remains unchanged. Application's later generic/redacted error handling cannot retract the SDK's earlier log. Source search found no application logging filter covering this path.

Scope: malformed/incompatible upstream data is required; this is not proof an ordinary owner can force Telegram to emit invalid callback structures, nor an observed live leak. Still, a concrete privacy/error boundary violates a blanket redacted-logging claim. Coordinator should establish sanitized SDK logging and retain retryability without dropping unseen updates; merely catching TypeError after get_updates does not remove already emitted logs.

## Verified application behavior

All locations below are in tests/test_acceptance_release.py and are passing in the final owned-suite run, unless explicitly called a limitation.

- :158, :173, :1143 — invitation redemption, guided opening/bucket/allocation review, Confirm/Edit/Cancel, and complete SDK inbox/outbox setup journey. Generated setup finish controls carry canonical draft UUIDs. Full SDK setup reconstructs durable graph before Confirm, processes duplicate update once, then displays calendar. Financial state stays empty until confirmation.
- :603, :635 — all generated Back/Cancel/Review/Add-more callbacks reject foreign/cancelled/stale drafts; cancelled calendar/day fallback preserves exact tombstone and money. All three review decisions reject foreign owners and cannot replace/cancel a newer setup review.
- :208, :400, :433, :1030 — admin access metadata has no money; ordinary user admin denial; invite single-use/expiry; trusted private sender/chat/bot checks; revoked owners cannot Confirm/Edit/Cancel outstanding reviews or re-register with a fresh invite. Revocation assertions read only this test's synthetic owner account rows and request status because public finance access correctly denies revoked users.
- :322, :482, :660, :706, :1224 — real SDK NL mutation, funding clarification and owner selection; malformed/unknown choice does not mutate money; missing-bucket clarification uses the real strict envelope. Durable funding answer and calendar/spending clarification survive reconstruction. Month-end answer uses original Asia/Kolkata October receipt, not November answer date. Captured provider context has only message/names/date/timezone (and explicit funding_source where appropriate), store=False, no actor or owner identity.
- :706, :1054 — explicit owner-scoped NL correction/undo selectors, foreign selector rejection across durable reconstruction, old review revision fencing, deterministic reports; audit history retains original and corrected states plus undo chain. Every mutation still requires exact confirmation.
- :1083, :1190 — combined income/allocation/expense commits as one batch; negative expense and target warnings do not silently fund other accounts; injected failure after actual transaction writes rolls back entire batch and retry applies it once.
- :273, :1112, :1252 — exact shortage rejection, unfunded reversal/correction rejection, transfer source limits, future date rejection, unchanged snapshot and pending state. Calendar pagination exposes eight then one records, consistent totals, no writes.
- :222, :236, :347, :462, :1132, :1280 — timezone, command Cancel, report/calendar owner scope, invalid date/page input, expired review no-write, deterministic reports and malformed command validation.
- :555, :862 — real SDK timeout/server/schema errors are safe; commands/Confirm/reports/calendar continue without another provider request; unexpected report failure leaves inbox pending, no false-success outbox, and later retry succeeds.
- :822, :907 — real SDK duplicate updates and Confirm replay, monotonic gap cursor, bot-scoped pending/outbox operations, crash-after-confirm reconstruction, outbox claim limit/backoff/token fencing. These do NOT establish generic exactly-once interactions or strict delivery order.

## Remaining limits / risks (not green guarantees)

1. Worker-instance/inbox claim: store.py:500-508 is a plain pending SELECT with no lease/claim. main.py:94-108 says one poller but has no cross-process singleton fence. Two independent pending reads return the same work in :1002. No simultaneous production processes were launched; concurrency guarantees are not established. Workflow owner advisory locks and finance request uniqueness do not solve stale/repeated conversational input.
2. Ingress ordering: transport.py:113-116 continues after failure. In :1002 update 10 fails but later update 12 completes and produces an outbox reply; offset is 13 because receipt is durable. This is a passing limitation characterization, NOT chronological application acceptance. Retrying earlier conversational input after newer updates is hazardous.
3. Delivery ordering: store.py:534-550 claims due items with SKIP LOCKED; transport.py:138-141 continues after send failure. In :907 the first reply is delayed while the following replies deliver, then the first retries. Equal-due claim ordering and the 20-item limit work, but retry scheduling and parallel senders can overtake. Ambiguous remote send timeout remains at-least-once, not exactly-once delivery.
4. Input limits: commands.py:75 bounds command text; controller.py:147-156 validates text/timestamps but does not impose an equivalent NL length/rate limit. workflow.py:444-452 materializes all active transaction candidates; transport chunks replies, not DB/graph/provider input costs. No stress/rate/large-history test was run. Missing deterministic command fields yield safe syntax replies, not a demonstrated guided command-entry flow. Upstream Telegram bounds are not a substitute for application resource-policy review.
5. Error boundaries: controller catches BudgetError while unexpected dependencies remain retryable, tested at report/transaction/outbox completion seams. SDK parsing happens before durable inbox receipt and can leak logs (R4-03). Unusual payload shapes and every DB/transport exception class are not exhaustively covered. The suite uses synthetic sentinel strings only.
6. AI/receipt limits: scripted Responses SDK HTTP establishes envelope/backend behavior, not provider language accuracy or live privacy policy. Initial report clarification with bucket-only metadata remains the documented restricted path; full query-origin switching and every timezone/DST edge are parent regression work. No claim that regex redaction recognizes every unlabeled identity.
7. Release gates: exhaustive requirement permutations, concurrency races, arbitrary crash points, restore, final source security/dependency/build/package identity, startup/service operation and live synthetic acceptance remain parent-owned. No bot deployment/publication approval is implied.

## Original six failures

All were investigated fixture/contract drift, not production defects: obsolete unbound setup callback (three cases); expiry textual offset versus same exact instant; safe actionable shortage wording; explicit revoked response. The saved original suite is preserved verbatim in commit 9fe6a8626dcbed920661fc9c02fe3316fad4c0d6 and original coordinator logs remain untouched. Recovery first made these 30 tests pass, then independent coverage exposed the three unresolved findings above. See R4_EVIDENCE.md for actual commands/results, including new-test fixture mistakes and their corrections.
