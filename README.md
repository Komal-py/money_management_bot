# Telegram budget bot

**Current stage: design approved; setup inputs pending before implementation. No application code exists yet.**

An invite-only, multi-user INR budgeting bot with virtual buckets, commands and English natural language, mandatory financial reviews, audited corrections, spending targets, reports, and a day-by-day Telegram calendar.

## Review in this order

1. [Approved requirements](docs/REQUIREMENTS.md) — what the bot should and should not do.
2. [HLD](docs/HLD.md) — overall components, money model, provider discovery, resource inventory, and proposed decisions.
3. [LLD](docs/LLD.md) — proposed schemas, workflow contracts, ledger rules, callbacks, corrections, and tests.
4. [Acceptance matrix](docs/ACCEPTANCE.md) — the evidence needed before features can count as complete.
5. [Design checks](docs/DESIGN_CHECKS.md) — actual document checks, not application test results.
6. [Setup inputs and eight-worker plan](docs/SETUP_AND_WORKERS.md) — owner-only inputs, project access boundaries, worker ownership, scheduling, and model/effort controls.

## Approval boundary

Product scope, stack and HLD/LLD defaults are approved for implementation. The owner authorized eight separately routed Hermes workers, bounded synthetic bot API checks using configured providers, dedicated app/test databases and project roles/schemas on the existing PostgreSQL server, and a push to `main` at `https://github.com/Komal-py/money_management_bot.git`. PostgreSQL connection details and local credentials, Telegram setup, and GitHub write authentication still need verification. None of those implementation/setup actions have been performed yet. Always-on hosting, backups/retention and a production cost cap remain separate decisions.

The existing Hermes provider/key reference was inspected locally without exposing or copying the key. The requested PDF was not attached/available. No credential belongs in these documents or in Git.

There are deliberately no run commands yet: a proposed design is not a runnable bot. Once implementation is authorized and exercised, this README will include actual installation/start/stop and verification instructions.
