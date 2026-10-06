# Frozen-baseline local operations runbook

Applies to main fdcf05e3f8e5d563569617ac5d7538c3094a53bc, not sibling work in progress. Setup and coding are authorized; old setup-pending prose is not a blocker. This is a manually entered virtual INR ledger, not bank-verified money or payment movement.

## Current usable components versus startup

Implemented libraries: exact money/date planner, owner-scoped PostgreSQL ledger and append-only audit guards, invite storage, pending proposal/edit/cancel/confirm, deterministic reports/calendar views, command parsing, private ingress checks and durable Telegram inbox/outbox adapter.

There is no src/budget_bot/main.py despite the budget-bot entrypoint in pyproject.toml:20-21. No runnable app/start command is verified. Onboarding/access UI, AI interpreter/renderers and LangGraph workflow modules are absent from this baseline. Controller recognizes only mutation submission, review callbacks, help, balances and spending; actual workflow submission/confirmation still needs an injected implementation. Calendar views exist but command/buttons are not wired. See WORKER_REVIEW.md before any deployment decision.

## Test-only validation

Use the existing project Python environment. Never point tests at production. The coordinator supplies BUDGET_TEST_DATABASE_URL externally; do not paste its value into commands, documents or logs. conftest only avoids its .env fallback when that variable is already supplied. Validate that it is nonempty before invoking pytest.

Windows bash (native executable paths, no MSYS /c path for Python):

    export PYTHONPATH="$PWD/src" BUDGET_TEST_SCHEMA=test_w8
    test -n "$BUDGET_TEST_DATABASE_URL"
    C:/Users/FL_LPT-657/Projects/telegram_bot/.venv/Scripts/python.exe -m pytest tests/test_acceptance_baseline.py -q --tb=short
    C:/Users/FL_LPT-657/Projects/telegram_bot/.venv/Scripts/python.exe -m ruff check tests/test_acceptance_baseline.py
    git diff --check

Acceptance uses test_w8 only, unique users, no shared truncation, real PostgreSQL/planner/report components and an offline bot double. It preserves synthetic rows/audit history for repeat runs. Store connections enforce 5-second lock, 15-second statement and 20-second idle-transaction bounds. Do not run a full suite during the concurrent worker wave. Existing test_storage.py requires test_w2 and uses a planner double; test_integration_store.py explicitly initializes/truncates test_integration. Those fixtures must not be weakened to fit another schema.

CI has a disposable PostgreSQL 16 test service and separates test_w2, test_integration and test_w8 commands. Remaining tests use test_ci. Dependencies use uv sync --locked; tests run without automatic third-party pytest plugins except explicitly loaded pytest-asyncio. CI has contents:read only, no persisted checkout credentials, runtime AI credentials or Telegram token. CI workflow has not been executed remotely in this review. Its normal PEP 517 build may install the declared build backend into build isolation; the worker does not install packages.

## Future startup prerequisites (coordinator-owned, not performed)

Supply project-local runtime settings securely: DATABASE_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_USER_ID, AI_API_KEY, AI_BASE_URL and AI_MODEL. Current load_settings requires AI fields even when AI_ENABLED=false; an actual credential-free outage/startup path remains a gap. Never obtain credentials from unrelated profiles or print settings/connection URLs.

Migration env.py expects BUDGET_DATABASE_URL and BUDGET_SCHEMA, whereas load_settings reads DATABASE_URL. Coordinator must explicitly map the intended application URL and schema before running alembic upgrade head. Test initialize() is convenience DDL, not evidence of production migration rollout. No new database, role or server is needed or authorized here. Workflow checkpoints require separate coordinator-reviewed wiring/migrations once their implementation exists.

Startup must own Bot initialize/shutdown, store lifecycle, admin initialization, implemented workflow/setup/access/report injection and transport.run(stop_event). No such assembly or process supervisor is verified. Do not advertise /start, /invite, /users, /revoke, /timezone, /cancel or calendar navigation as functioning end-to-end merely because their parser supports them.

## Laptop operation and failure handling

No cloud deployment was performed or verified. A future laptop-hosted process only runs while the laptop is powered, awake, connected and the process/database are available. Closing its terminal, sleep, reboot or network loss stops service; automatic restart is not implemented. Do not promise continuous uptime or reminders.

Durable receive records precede polling offsets; transient handler failures retain pending work. Outbox leases retry failed delivery. Remote-send success followed by local failure can duplicate replies (at-least-once delivery); financial request replay is separately guarded by storage. Stop gracefully with the future stop event and close resources. Do not delete pending inbox/outbox to recover a transient outage or manually rewrite balances/history.

Coordinator integration now scopes inbox handling, completion and outbox claims to the active bot. Within equal due_at values, claims order by bot/update/reply position before UUID; sequential delivery preserves position across the 20-item claim boundary. Retry/backoff changes due_at and can reorder parts, and simultaneous transports can reorder sends. This is not a global exactly-once/strict-order delivery guarantee. Run one active poller for the initial single-bot project schema.

Protect PostgreSQL access and backups: bot owner isolation is not protection from the database operator. Backup/restore, retention, logging destination, supervisor and cloud hosting remain operational decisions, not executed checks. Do not run destructive migration downgrade or synthetic test cleanup against application schemas.

## Local verification limits

Worker acceptance and scoped lint results are recorded in WORKER_REVIEW.md. Local python -m build --no-isolation is blocked because hatchling is absent in the supplied environment; dependencies were not installed. Packaging failure and missing runtime entrypoint are distinct. No live Telegram/provider call, GitHub write, cloud deployment or complete security signoff is represented by these checks.
