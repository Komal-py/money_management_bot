# W5 evidence

Scope: src/budget_bot/ai/**, tests/test_ai*.py, this evidence file only.
Base: fdcf05e, worker/w5. Saved untracked schemas.py and three tests were read and run before editing; they were retained and completed rather than treated as finished work.

## Deliverables

- `budget_bot.ai.AIInterpreter` (also `budget_bot.ai.provider.AIInterpreter`): async interpret/close through installed OpenAI Responses SDK, httpx client, HTTPS endpoint validation, redirects disabled, environment proxies disabled, zero SDK retries, positive finite timeout, low reasoning, store=False, strict JSON schema.
- Refusal, incomplete responses, invalid output/envelopes/JSON, timeouts, server errors and redirects become safe BudgetError messages without provider detail. No credentials/config file discovery in the adapter; credentials/model must be supplied explicitly.
- Prompt context includes only redacted message, redacted bucket names, local date and timezone. No identity metadata, IDs, balances, targets, history or prior response IDs. Known credential, explicit identity/contact/secret patterns are redacted without converting money/date strings. Redacted bucket collisions clarify rather than choosing arbitrarily; unique matches restore the original local bucket name after the provider response.
- Ambiguous add/put/top-up clauses clarify funding before a provider call; model instructions also require clarification. Missing/unknown buckets offer existing names or named creation. Unspecified model-created buckets are rejected into clarification. Ordered income plus allocation stays ordered.
- Opening is absent from the model action schema. Undo/correct accept human reference syntax, never authority IDs; interpreter returns transaction_reference clarification with no executable actions. W6 must select owner-scoped records and construct backend actions. No financial write path exists in this package.
- `render_review`, `render_result`, `render_balances` exported from `budget_bot.ai` and defined in `budget_bot.ai.rendering`. Deterministic labels, exact INR values, dates, local timezone, source/destination direction, projected/current balances, warnings, metadata, confirmation language and localized review expiry. No nullable field/internal dictionary dump. Review tests use the actual domain planner, including correction/undo before/after facts and warnings.

## Actual execution

Interpreter used throughout:
`C:/Users/FL_LPT-657/Projects/telegram_bot/.venv/Scripts/python.exe`

Session environment:
`PYTHONPATH=C:/Users/FL_LPT-657/Projects/telegram_bot/.worktrees/w5/src`
`BUDGET_TEST_SCHEMA=test_w5`

Initial saved targeted run:
`python -m pytest tests/test_ai_contract.py tests/test_ai_provider.py tests/test_ai_schema.py -q`
Result: collection error, missing budget_bot.ai.provider.

Saved independently runnable baseline:
`python -m pytest --noconftest tests/test_ai_schema.py tests/test_ai_contract.py -q`
Result: 28 passed. Subsequent runs use --noconftest to avoid the shared conftest's configuration-file fallback; these tests need no database or deployment configuration.

Incremental red/green results:
- Provider tests initially exposed a broken saved MockTransport factory (SDK isinstance requires a class); changed test factory to an AsyncClient subclass, preserving the real SDK. 39 passed after implementation/factory repair.
- Renderer import regression failed before implementation, then 8 passed against actual planner output.
- Hardening regressions: 5 failed / 32 passed (decimal funding clause, top-up syntax, non-JSON HTTP body, nullable query contract, explicit name redaction), then combined 84 passed.
- Schema/redacted-bucket/committed-fact regressions: 8 failed / 77 passed, then combined 97 passed.
- Unspecified bucket, financial-string redaction and committed correction regressions: 7 failed / 51 passed, then combined 104 passed.
- Explicit configuration regression: 5 failed / 50 passed, then final combined 111 passed.

Final commands (with the full interpreter path above in place of `python`):

`python -m pytest --noconftest tests/test_ai_schema.py tests/test_ai_hardening.py tests/test_ai_rendering.py tests/test_ai_provider.py tests/test_ai_contract.py -q --tb=short`
Result: 111 passed in 3.92s.

`python -m ruff check src/budget_bot/ai tests/test_ai_contract.py tests/test_ai_provider.py tests/test_ai_schema.py tests/test_ai_rendering.py tests/test_ai_hardening.py`
Result: All checks passed.

`git diff --check`
Result: exit 0. Staged diff whitespace validation also runs before commit because the starting files were untracked.

## Boundaries / remaining integration

- No hosted API, Telegram, database or network calls were made by these tests. Provider success/error responses are explicit MockTransport fixtures, not evidence of hosted-provider availability or model quality. No dependency installation, full suite, shared-file edit, credential printing, or database setup.
- W6 integration is not exercised here. Interpreter deliberately returns a safe transaction-selection clarification rather than executing NL undo/correct; owner-scoped selection, conversational persistence, proposals and confirmation remain W6/backend responsibilities.
- Natural-language intent recognition is not a proof over arbitrary English. Deterministic funding guards cover tested add/put/top-up clause forms, with additional model instructions; backend validation and explicit confirmation remain mandatory for every mutation.
- Pattern redaction cannot reliably distinguish an unlabelled personal name or digit-only contact number from a description or monetary amount. Explicit identifiers and known credential strings are covered; the interpreter API cannot accept Telegram identity metadata or snapshots.
- Frozen committed results omit plan/events. Rendering uses matching batch transactions and, for correction/undo, exact backend summary reference prefixes to recover current labeled facts from the result snapshot. This is tested against the current planner summary format. Do not pass model prose as backend result summaries.
- No finished-app, live-service, provider pricing or cross-worker integration claims.
