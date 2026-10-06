# W8 independent frozen-main review

## Scope and evidence boundary

Reviewed HEAD fdcf05e3f8e5d563569617ac5d7538c3094a53bc on worker/w8, independently of W3/W5/W6. git status was clean at start: no saved untracked work existed to preserve or credit. Read REQUIREMENTS/CONTRACTS, domain money/dates/planner, storage models/store, migration/env, controller, command/transport/calendar/report paths and relevant fixtures. Setup is authorized despite stale setup prose.

Only owned acceptance, CI and these operational/review documents changed. No sibling source fix, dependency installation, full suite, live provider/Telegram call, external write, new DB/role/server or production migration occurred. This is not complete security signoff. The coordinator's previously reported 147-test baseline is supplied context, not a W8 full-suite result.

## Concrete findings

| Severity | Location | Reproduction / consequence | Status |
|---|---|---|---|
| High release blocker | pyproject.toml:20-21; absent src/budget_bot/main.py | Declared budget-bot resolves budget_bot.main:main, but source inventory contains no main module. Installing/building alone cannot establish a runnable entrypoint. | Missing assembly; no startup attempted. |
| High release blocker | controller.py:7-12,49,62; absent services/onboarding.py, services/access.py, workflows/**, ai/** | Constructor needs workflow/onboarding/access; financial command dispatch calls workflow.submit and confirmation calls workflow.decide. Passing None causes failure on these routes. Actual persisted LangGraph, setup UI and AI/rendering boundary cannot be acceptance-tested via missing imports. | Explicit missing boundary; not imported by new tests. |
| High functionality | controller.py:34-36,53-71; commands.py:159-161 | /start invite from unregistered user returns invite-only rejection before parsing/redemption. For registered users /start, /invite, /users, /revoke, /timezone and /cancel parse but fall through to generic help. Assigned onboarding/access objects are unused. | Read-confirmed missing ingress wiring, not fixed. |
| Medium functionality | controller.py:38-50,63-71; commands.py:138-158; telegram/calendar.py:16-74 | Registered /calendar returns generic help, not October 2026. cal:/day: callbacks are rejected as unavailable. Real calendar views are present and read-only but unreachable through this ingress. | Executed exact regression failed before strict xfail annotation; retained as strict expected failure. |
| Medium reliability | controller.py:67-70; services/reports.py:27-35,67-87; transport.py:73-79 | /spending today Unknown parses, then ReportService raises ValueError for unknown bucket. No controller exception translation; transport retains/retries the inbox indefinitely without a user-facing error. Similar BudgetError on invalid financial proposal escapes via workflow unless translated there. | Read-confirmed; unavailable workflow handling not assumed. |
| Medium outage readiness | settings.py:28-45 | AI_ENABLED=false still requires AI_API_KEY/AI_BASE_URL/AI_MODEL before settings construction. No credential-free AI-disabled startup path is implemented. Controller also has no NL fallback explanation/clarification route. | Source review, no secret/config load performed. |
| Medium transport boundary | storage/store.py:496-509,524-536; telegram/transport.py:65-75,91-101 | pending_updates/outbox are unfiltered by bot. Save same update_id under two bot IDs: complete_update(update_id,...) raises ambiguous_update. A transport can claim another bot's replies. This baseline is safe only with an explicit single-bot-per-schema operating constraint; multi-bot sharing is not verified. | Read-confirmed API limitation, not asserted as cross-owner exploit or new scope. |
| Low reporting consistency | domain/money.py:90-94; services/reports.py:12-16 | format_money(-1000) places minus after currency (₹-10.00); reports use -₹10.00. Same integer fact, inconsistent user rendering. | No financial safeguard changed. |
| Build environment blocker | pyproject.toml:26-28 | Supplied Python's python -m build --no-isolation cannot import hatchling.build. No-isolation packaging did not succeed; missing runtime main remains a separate issue even if backend later supplied. | Actual command failed; no installation authorized. |

Paths in the table are under src/budget_bot unless explicitly pyproject.toml. Missing components are findings about this frozen baseline, not assertions about siblings' current work. CI uses sensible major action refs (checkout@v4/setup-uv@v6), contents:read and disposable PostgreSQL credentials; remote action execution and release provenance were not verified. No GitHub/network write was made.

## Acceptance verification

New tests use real available planner/store/controller/reports/calendar/transport components with approved BUDGET_TEST_DATABASE_URL and only test_w8. Unique synthetic owners avoid shared truncation and preserve append-only evidence. OfflineBot/Handler are explicitly test doubles, not provider success or an implementation of absent workflows. DB connection initialization errors are replaced with a credential-safe failure; URL is never printed. Store enforces SQL lock/statement bounds.

Verified unchanged safeguards:

- Proposal/edit/cancel do not write money; duplicate confirmation and post-commit cancellation return immutable committed result.
- Failed combined income/allocation is atomic with no pending review; insufficient source funds do not create money.
- Confirmed expense alone may go negative, retains pool and emits negative/no-implicit-funding warnings.
- Old edited revision and timezone state revision renew review without committing; expired confirmation is blocked.
- Foreign owner cannot confirm review or undo transaction; revoked owner cannot obtain snapshot; report totals remain owner-scoped.
- Correction moves current report date/amount; undo excludes expense from calendar/report while three transaction revisions and three postings remain.
- Consumed allocation cannot be undone; future expenses rejected without mutation.
- Actual controller balances use trusted private actor; groups rejected.
- Durable inbox retained on synthetic transient failure; retry creates real outbox, offline delivery acknowledged with no remaining due reply.

Calendar missing-wiring regression initially: 8 passed, 1 failed in 16.29s, exact assertion expected October 2026 but received generic help. Because controller is outside ownership, no minimal source fix was permitted; strict xfail names the boundary and will fail on XPASS, forcing review when parent wires calendar. This is not counted as passing acceptance.

## Actual commands and results

Environment: PYTHONPATH=this worktree/src, BUDGET_TEST_SCHEMA=test_w8; approved DB URL inherited without output. Python executable for each command:
C:/Users/FL_LPT-657/Projects/telegram_bot/.venv/Scripts/python.exe

- Before editing, -m pytest tests/test_controller.py tests/test_controller_commands.py tests/test_controller_reports.py tests/test_reports_calendar.py -q: 34 passed in 2.19s.
- Final -m pytest tests/test_acceptance_baseline.py -q --tb=short: 9 passed, 1 xfailed in 8.17s.
- -m ruff check tests/test_acceptance_baseline.py: All checks passed.
- git diff --check: exit 0 (also repeated before commit).
- -m build --no-isolation: exit 1, BackendUnavailable / missing hatchling. Do not substitute build success; build isolation installation deliberately not attempted.
- Initial combined prerequisite command using python -c was policy-blocked, executed no verification; replaced by shell test -n and real targeted pytest.

CI separates source fixtures' test_w2 and test_integration from acceptance test_w8, locked uv dependencies, scoped/full CI lint and tests, and normal isolated PEP 517 packaging. CI is authored, not remotely executed. Parent owns full-suite integration, installing build prerequisites, startup assembly, final wiring review and publication. Local commit identifier is returned in the worker's final report; this document does not embed a self-referential commit hash.
