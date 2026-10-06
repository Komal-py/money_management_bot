# W6 workflow evidence

## Scope and baseline

Owned changes only: `src/budget_bot/workflows/**`, `tests/test_workflow.py`, this evidence file.
Worktree baseline: `fdcf05e`, branch `worker/w6`. Initial `git status --short` was empty; no saved untracked workflow source/tests existed. REQUIREMENTS.md and CONTRACTS.md were read before implementation. No sibling source, controller, manifests, lockfiles, conftest, credentials files, or shared documentation was changed.

## Implemented

- `BudgetWorkflow(store, interpreter=None, checkpointer=None)` provides async submit (optional stable request_id), natural_language, decide and answer.
- Real LangGraph StateGraph has prepare/wait/apply nodes. `interrupt` suspends wait; `Command(resume=...)` advances it. Separate checkpointed owner session selects the current owner/request thread. Exact canonical UUID strings are validated, not repaired.
- Financial planning, proposal, edit, cancel and confirm go through BudgetStore. Workflow recovery reads owner-scoped Request/Batch/BatchResult records; it never creates ledger facts itself. No balance changes before explicit Confirm.
- Owner session advisory lock uses an independent AUTOCOMMIT connection. Acquisition is bounded, and financial store operations use their own short transactions. AI does not run inside a financial or advisory-lock transaction. Store defaults bound SQL statements/locks; checkpoint connections also bound statements/locks.
- Review callbacks retain controller-compatible `rev:<request_uuid>:<revision>:<decision>` data. Owner comes from the authenticated caller and is rechecked against the store. Stale edit/cancel callbacks return the current review without changing it; confirm uses store stale re-review authority.
- Checkpointed missing expense bucket, missing amount/description/name, interpreter clarification, edit, and owner-selected correction/undo flows. Receipt timestamp survives clarification and restart; an explicit edit starts a new receipt timestamp. Explicit creation plus expense is one review, never an automatic unnamed bucket.
- NL undo/correct model IDs, batch IDs and last selectors are discarded. The owner must select an ID from displayed owner-scoped active candidates. Model-provided internal selection markers are rejected.
- Common English `add/put/top up ... to/into <existing bucket>` ambiguity gets a deterministic funding question even if the controlled interpreter incorrectly supplies allocation. Other ambiguity relies on the strict interpreter clarification contract.
- Durable ingress replay preserves request/review and financial IDs. Committed-result replay recovers the store result after a graph-side post-commit crash, including after PostgreSQL checkpoint reopen. Terminal or expired prior requests cannot trap the next ingress.
- Controlled interpreter or no interpreter; no live provider calls. Provider-unavailable returns safe command guidance, and commands/confirm remain usable.
- Local deterministic renderers calculate no new financial facts: exact reviewed actions, snapshot projected balances, timezone, expiry, summaries and warnings are displayed. Parent may replace `flow.render_review` and `flow.render_result` with W5 renderers.
- Query results carry a validated `query` dictionary for the parent controller. Unknown query keys/model totals, unknown buckets and malformed/range dates are rejected; workflow fabricates no report result.
- `async with postgres_checkpointer(database_url, schema='workflow') as saver` manages the supported PostgresSaver library tables. Async methods offload supported synchronous saver calls to threads, allowing actual Windows Proactor-loop execution without global event-loop setup changes. Tests use only `BUDGET_TEST_DATABASE_URL`, application schema `test_w6`, checkpoint schema `test_w6_workflow`.

## Actual execution

Interpreter used for all runs:
`C:/Users/FL_LPT-657/Projects/telegram_bot/.venv/Scripts/python.exe`

Environment set without printing credentials:
`export PYTHONPATH="$PWD/src" BUDGET_TEST_SCHEMA=test_w6`

Initial saved regression run:
`python -m pytest tests/test_workflow.py -q`
failed collection for exact missing `budget_bot.workflows` module.

Incremental regression runs exposed and fixed replay rendering/timezone differences, Windows async psycopg incompatibility, invalid-answer retry trapping, post-commit session trapping, provider outage session trapping, funding ambiguity, expiry ingress trapping, invalid edit trapping, and missing amount/description clarification. Two test assumptions were corrected against real domain behavior: bucket creation is metadata rather than a financial transaction; expenses require completed opening setup. A state-revision test now changes revision while retaining the timezone, so it tests stale confirmation rather than the separate timezone/future-date safeguard.

Final verification command:
`python -m pytest tests/test_workflow.py -q && python -m ruff check src/budget_bot/workflows tests/test_workflow.py && git diff --check`

Actual final output: `18 passed in 30.71s`; `All checks passed!`; diff whitespace check exited zero. No full suite was run. No dependencies were installed. PostgreSQL tests were not skipped; real store and real planner were used, with only the interpreter controlled and deliberate crash injection around actual store.confirm.

## Integration and explicit limitations

- Parent must supply the factory saver for durable production checkpoints; default InMemorySaver is intentionally nondurable. Parent must route NL validated query output to report services, and invoke answer for current clarification/edit interaction. Parent may wire W5 renderers through the two exposed renderer attributes.
- BudgetStore edit/cancel lack review-revision parameters. Workflow serializes its own calls and checks revisions before them, but direct concurrent store edit/cancel bypassing workflow cannot be atomically fenced by W6 alone. Parent should route decisions through this workflow.
- If changing timezone makes an already-reviewed canonical expense date future relative to the original receipt, the existing planner rejects confirmation rather than producing a stale plan. No financial safeguard was weakened; user must edit/cancel that review. This cross-owner-module behavior was observed during the initial tests and not changed outside W6 ownership.
- `answer(owner_id,text,now)` has no step token argument. It always targets the current checkpointed question; it cannot distinguish two identical late free-text replies across different steps. Durable Telegram update replay/fencing remains parent ingress responsibility.
- Deterministic ambiguity defense covers common English funding phrasing, not arbitrary language semantics; remaining ambiguity must be returned as clarification by W5. No live model availability or finished-app claims are made.
- Checkpoint tables/history remain in the isolated authorized schema for reopen/recovery evidence; tests reset only application tables in test_w6. No shared schemas, new database, role or server were provisioned.
