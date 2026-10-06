# R3 conversation recovery evidence

Original recovery scope: worker/r3, recovery r3-retry1. That recovery changed only conversation.py, test_conversation.py and this evidence file. The independently reviewed error-boundary repair and coordinator-requested query receipt seam are recorded separately below. Historical baseline totals are not results from the repair run.

## Interface delivered

- `ConversationRouter(store, workflow, reports)` in `budget_bot.services.conversation`.
- `async dispatch(self, owner_id, text, received_at, now, *, query=None, callback_data=None)` returns an existing JSON-safe output dictionary or None for an unsupported callback.
- `menu(self) -> dict` returns command-based presentation with an empty keyboard. No invented menu callbacks or financial-entry flow. Calendar outputs retain the existing `{text,data}` buttons.
- Explicit query dispatch validates exactly the five frozen query fields using the existing strict Query schema; deterministic ReportService renders balances/spending. Calendar defaults to the receipt-local month. Unknown buckets and malformed dates/pages produce safe replies. Spending accepts bucket filters and inclusive ranges; balances/calendar reject filters/ranges they cannot render rather than silently discard them. Calendar today/week/month requests all open the receipt-local month.
- `cal:YYYY-MM` and `day:YYYY-MM-DD[:page]` delegate to existing calendar parsing/rendering. Unsupported callback namespaces return None and never enter the workflow.
- NL attempts real `workflow.answer` first; only `BudgetError.code == 'no_question'` permits new NL. Pending store reviews additionally block a new NL interaction even when checkpoints are absent or AI is disabled. Current question/edit handling is left to the real graph. Explicit read-only queries and calendar navigation do not consume the pending interaction.
- Workflow query outputs are rendered rather than returned as “Report request validated.” Workflow safe errors (including pending_review, unknown_bucket and invalid_model_output) propagate to the caller. Existing workflow/provider outage text remains intact; unexpected database failures remain retryable exceptions.
- Caller still owns sender/chat authorization, command parsing, setup, review decisions and safe BudgetError presentation. No confirmation occurs in this router.

## Execution environment and boundaries

Commands ran from `C:/Users/FL_LPT-657/Projects/telegram_bot/.worktrees/r3` with:

    export PYTHONPATH='C:/Users/FL_LPT-657/Projects/telegram_bot/.worktrees/r3/src'
    export BUDGET_TEST_SCHEMA=test_w7

The already-inherited BUDGET_TEST_DATABASE_URL was used, never printed. `PY` below means the exact executable:

    C:/Users/FL_LPT-657/Projects/telegram_bot/.venv/Scripts/python.exe

The tests require test_w7 and inherited test DB configuration, initialize existing app tables idempotently, use unique synthetic owner IDs, and never truncate/drop or clean up another test's rows. Real PostgreSQL BudgetStore, ReportService, planner and LangGraph execute. Graph checkpoints in this component suite use BudgetWorkflow's default InMemorySaver; durable PostgreSQL checkpoint restart is not claimed. Missing-checkpoint/store-review recovery is exercised with a fresh graph. Network cases inject httpx.MockTransport into the real AIInterpreter/OpenAI SDK using synthetic credentials and example.invalid, with no live traffic. No Telegram calls, provider credential reads, owner money, dependency edits, DB/server/container provisioning, full suite, build, push or remote CI occurred.

## Actual red/green commands and outcomes

1. Preserved partial tests, before production edits:

       "$PY" -m pytest tests/test_conversation.py -q

   RED: 6 failed, 3 passed. NL reports returned only “Report request validated”; explicit offline query arguments were ignored. Added deterministic report dispatch. Same command GREEN: 9 passed.

2. Added invalid-query, canonical-bucket, pending read-only and naive-receipt regressions before validation implementation:

       "$PY" -m pytest tests/test_conversation.py -q -k 'invalid_queries or range_query or naive_receipt' --tb=short

   RED: 24 failed, 9 deselected. Failures included unhandled KeyError/TypeError/ValueError, silently ignored query fields and uncanonicalized returned bucket names. Added validation/error rendering. First whole-file rerun: 32 passed, 1 failed because the test compared UTC proposal expiry formatting against the equivalent +05:30 database representation. Corrected the test to compare stored pending review before/after (not production date semantics). Same whole-file command GREEN: 33 passed.

3. Added calendar/month navigation, malformed callbacks, unsupported callback isolation, current-active expense pagination/owner isolation and menu tests:

       "$PY" -m pytest tests/test_conversation.py -q -k 'calendar_month_navigation or invalid_calendar or unsupported_callbacks or calendar_day_pages or menu_offers' --tb=short

   RED: 25 failed, 33 deselected. Callback text wrongly entered graph answers/NL; menu method absent. Implemented delegation to existing calendar and command-only menu. Whole-file GREEN: 58 passed.

4. Added missing-checkpoint pending-review and empty-message regressions:

       "$PY" -m pytest tests/test_conversation.py -q -k 'store_pending or empty_message' --tb=short

   RED: 5 failed, 58 deselected. Fresh graphs/AI-disabled paths bypassed stored pending reviews; empty text reached interpreter. Added store pending guard after no_question and menu fallback for no current question/empty input. Whole-file GREEN: 63 passed.

5. Supplemental tests exercised already-existing workflow/provider behavior (no production patch or fabricated RED claimed):

       "$PY" -m pytest tests/test_conversation.py -q -k 'real_sdk_outage or invalid_answer or edit_answer or unknown_nl or unexpected_database' --tb=short

   GREEN on first execution: 9 passed, 63 deselected. Real SDK timeout, HTTP 503 and malformed output were controlled; subsequent commands, reports, calendar and explicit graph Confirm worked without extra provider requests. Invalid bucket answers stayed in current graph, edits reused the request, safe unknown-query errors propagated, injected unexpected database errors remained retryable.

6. Added real store week/month receipt-anchor and real SDK validated-query-to-calendar supplemental coverage. Final targeted command:

       "$PY" -m pytest tests/test_conversation.py -q

   Final observed result: 75 passed in 46.83s, no failures, skips or xfails.

7. Static/whitespace checks:

       "$PY" -m ruff check src/budget_bot/services/conversation.py tests/test_conversation.py
       git diff --check

   Ruff: All checks passed. Git whitespace check: exit 0. Staged owned-file whitespace check also required before commit.

## Verified scenarios

- Real graph interruption and current-question priority, invalid answer retention, same-review Edit; original receipt retained for initial expense clarification.
- New NL only proposes; snapshot unchanged before Confirm. Store pending reviews block unrelated NL with/without interpreter/checkpoint state.
- Validated NL balances/spending/calendar and real Responses SDK calendar response reach deterministic outputs. Current day/month uses owner timezone, not later processing time; Los Angeles and Asia/Kolkata cross-month receipt cases covered.
- Read-only queries remain available during review. Strict extra-field/type/date/range/unknown-bucket rejection; canonical owner bucket output, no input mutation.
- Calendar leap month, year navigation, bounded first/last years, invalid callback/page data and unsupported callback isolation.
- Day pagination uses active corrected expenses, excludes undone expenses and other owner's data; empty day renders zero spending. Navigation leaves finances and current question unchanged.
- Command menu parses with the existing parser and invents no unsupported buttons.
- SDK outage/invalid-output boundary, provider-free deterministic commands and confirmation, retryable unexpected DB failure.

## Coordinator boundaries / remaining gaps

- This worktree's controller is the pre-R2 controller and does not call ConversationRouter. R2/coordinator must integrate the frozen optional conversation seam, send authorized NL/query/calendar requests here, display menu after setup, and catch safe BudgetError while leaving unexpected failures retryable. No assembled controller/application acceptance or release claim is made.
- Read-only source finding, not repaired or marked as passing acceptance: `workflows/workflow.py:543-550` accepts only mutation after an interpreter clarification. The AI adapter can ask for report period/start/end or unknown-bucket clarification, but the graph rejects a subsequent query with invalid_answer. R3 correctly gives the current question priority and does not bypass it. Supporting report clarification-to-query completion requires a coordinator-owned workflow change, including preserving original receipt when rendering that completed query. Current tests cover directly validated NL queries, not that unsupported workflow transition.
- Month callbacks contain no financial data and rely on caller authorization; day/report reads use only the explicit caller owner. Telegram actor checks and revocation ingress are R2 responsibilities.
- No unowned files were edited. No new dependencies or financial semantics were introduced. Full merged regression, durable checkpoint coverage, release packaging, live smoke and CI remain coordinator work.

## Independent review repair: dependency error masking

Repair base: `9c54c44fc562a1a17dc8c29212a7e9de0919a4d1` in preserved `.worktrees/r3`, branch `worker/r3`. The independent coordinator repro was present as an untracked test; it was rerun before any production edits. All commands in this section ran from `C:/Users/FL_LPT-657/Projects/telegram_bot` via the approved runner, without reading or printing credentials/URLs. Existing real PostgreSQL schema `test_w7` only; no DB/container/server provisioning, live Telegram/provider, full suite, merge or push.

Root cause: `_calendar` caught ordinary `ValueError` across `day_view`'s owner-scoped store read and rendering. `_report` caught ordinary `ValueError` and `BudgetError('invalid_date')` across snapshot reads and report services. Dependency faults became successful invalid-input replies, including dependency Pydantic `ValidationError` (a `ValueError` subclass).

Minimal repair:

- `_ReportInputError` identifies only router input rejection. Catch Pydantic `ValidationError` only around `Query.model_validate`; catch `invalid_date` only around receipt-local input validation. Snapshot/report dependency calls are outside those catches. Strict envelope, date/range, unsupported filters and owner bucket validation keep their existing safe replies.
- Catch ordinary `ValueError` only around pure callback parsing. `CalendarInputError(ValueError)` identifies invalid/out-of-range pages; `day_view` still uses one owner-scoped `store.spending` read before deciding whether the requested page exists. Store and rendering failures propagate unchanged. Existing direct calendar callers retain `ValueError` compatibility.
- Expanded repro verifies exception object identity for ordinary `ValueError`, Pydantic `ValidationError` and dependency `BudgetError('invalid_date')` at snapshot/spending/balances/day boundaries. Two out-of-range page tests assert safe replies, exactly one spending read, caller owner, unchanged snapshot and pending state. Existing malformed inputs, unknown bucket, date/range and owner isolation tests remain unchanged.

Actual RED:

```text
python .private/run_tests.py r3 --schema test_w7 tests/test_conversation_dependency_errors.py -q --tb=short --junitxml=C:/Users/FL_LPT-657/Projects/telegram_bot/.coordination/r3-dependency-errors-red.xml
```

Result: **4 failed in 7.58s**, each `DID NOT RAISE ValueError` as independently reported.

Expanded RED, still before production edits:

```text
python .private/run_tests.py r3 --schema test_w7 tests/test_conversation_dependency_errors.py -q --tb=short --junitxml=C:/Users/FL_LPT-657/Projects/telegram_bot/.coordination/r3-dependency-errors-expanded-red.xml
```

Result: **11 failed, 3 passed in 15.52s**. All four ordinary ValueError and four dependency ValidationError boundaries failed; three report-side dependency invalid_date boundaries failed. Day invalid_date already propagated, and both existing out-of-range safe replies passed. No invented RED for those three passing controls.

First GREEN, before the separate receipt seam addition: the two conversation files together returned **89 passed in 73.12s** using the final targeted command below. The final XML replaces that first GREEN artifact.

## Separate coordinator-requested seam: persisted query receipt

The coordinator explicitly extended ownership to `tests/test_conversation.py` and requested support for trusted backend workflow output `query_received_at`. Router now parses `datetime.fromisoformat(output.get('query_received_at', received_at.isoformat()))` only when rendering a workflow query. Explicit `query=` dispatch still uses its supplied receipt. Missing metadata remains backward compatible, and backend metadata is not included in the rendered output. No workflow.py/main edits were made.

Four controlled workflow-output cases use the real PostgreSQL store and deterministic ReportService/calendar: spending/calendar with and without persisted receipt. The persisted receipt is before Asia/Kolkata midnight/month end; the reply is after it. Two different dated expenses distinguish the spending totals. All cases also assert explicit-query receipt behavior and read-only snapshot/pending state. This verifies the router seam, not the coordinator's actual graph clarification implementation.

```text
python .private/run_tests.py r3 --schema test_w7 tests/test_conversation.py -q -k workflow_query_uses_persisted_receipt --tb=short --junitxml=C:/Users/FL_LPT-657/Projects/telegram_bot/.coordination/r3-query-receipt-red.xml
```

RED before the receipt production change: **2 failed, 2 passed, 75 deselected in 11.49s**. Persisted spending/calendar incorrectly rendered the later March receipt; missing-metadata controls already passed.

```text
python .private/run_tests.py r3 --schema test_w7 tests/test_conversation.py -q -k workflow_query_uses_persisted_receipt --tb=short --junitxml=C:/Users/FL_LPT-657/Projects/telegram_bot/.coordination/r3-query-receipt-green.xml
```

GREEN: **4 passed, 75 deselected in 10.75s**.

## Final repair verification

```text
python .private/run_tests.py r3 --schema test_w7 tests/test_conversation.py tests/test_conversation_dependency_errors.py -q --tb=short --junitxml=C:/Users/FL_LPT-657/Projects/telegram_bot/.coordination/r3-dependency-errors-green.xml
```

Final targeted real PostgreSQL result: **93 passed in 80.88s** (79 conversation cases, 14 dependency/page cases), no failures, skips or xfails.

Additional compatibility check for the minimally changed calendar exception type:

```text
python .private/run_tests.py r3 --schema test_w7 tests/test_reports_calendar.py -q --tb=short --junitxml=C:/Users/FL_LPT-657/Projects/telegram_bot/.coordination/r3-calendar-exception-compatibility.xml
```

Result: **23 passed in 0.63s**. These existing calendar unit cases use StoreDouble, not PostgreSQL, and verify direct ValueError callers still work.

```text
uv run --no-sync ruff check .worktrees/r3/src/budget_bot/services/conversation.py .worktrees/r3/tests/test_conversation_dependency_errors.py .worktrees/r3/src/budget_bot/telegram/calendar.py .worktrees/r3/tests/test_conversation.py
git -C C:/Users/FL_LPT-657/Projects/telegram_bot/.worktrees/r3 diff --check
```

Ruff: **All checks passed**. Whitespace check: **exit 0**; staged whitespace check also required before commit. Repair-owned files: conversation.py, calendar.py, test_conversation_dependency_errors.py, test_conversation.py and this evidence file. No unresolved issue in these targeted repairs. Parent retains workflow integration, assembled acceptance and final review; the historical workflow gap above was reported repaired in main by the coordinator but is not present or exercised in this preserved worktree.
