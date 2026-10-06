# Telegram budget bot

**Current stage: original eight worker contributions integrated; executable startup has now been implemented and coordinator-tested after the remaining-work agent quota failures. Controller/onboarding/NL/calendar assembly and release acceptance remain unfinished. The bot is not running and this project is not ready for release.**

Invite-only, multi-user virtual INR budgeting through Telegram, with isolated owners, central pool and buckets, reviewed financial actions, audited corrections, targets, spending reports, and a recorded-expense calendar.

## Verified locally

- Final frozen-source regression: **435 passed, 1 known strict expected failure**, across all **25** current test files exactly once. The expected failure is `/calendar` controller dispatch.
- Real PostgreSQL planning/storage, guided setup/access and durable LangGraph workflows tested. Cross-worker seam tests use the real OpenAI Responses SDK with controlled HTTP, not live AI or owner financial data.
- Financial changes remain proposals until explicit Confirm; undo/correction requires explicit owner-scoped record selection. Funding clarification accepts an explicit pool/income choice instead of looping.
- Source/test Ruff and Git whitespace checks passed. Local CI configuration enumerates all current test files in the required isolated schemas; remote CI has not run.
- Historical wheel/source distribution builds excluded private/worktree paths. **`budget_bot.main` now exists; startup/configuration passed 22 coordinator tests against real test PostgreSQL and controlled Telegram SDK.** Final assembly still needs a fresh exhaustive test/build/package run.

See [Implementation status](docs/IMPLEMENTATION_STATUS.md) for evidence, review limits and remaining work.

## Local development checks

Python 3.11 and uv are used. Credentials are owner-entered in ignored `.env`; examples contain no secrets. Tests require an approved dedicated test database/schema on PostgreSQL, never the owner's production records.

```bash
uv sync --locked --group dev --python 3.11
uv run --no-sync ruff check src tests
uv build
```

Schema-specific test commands are in [.github/workflows/ci.yml](.github/workflows/ci.yml). Do not run the whole suite under one shared schema: individual fixtures enforce different isolated schemas. The local coordinator's ignored `.private/verify_wave.py` verifies coverage, JUnit counts and frozen-source fingerprints.

Offline configuration validation is available with `python -m budget_bot --check-config`. Explicit migration and live startup must wait for completed ingress acceptance; the current component checks do not authorize or establish a running bot.

## Requirements and design

1. [Approved requirements](docs/REQUIREMENTS.md)
2. [HLD](docs/HLD.md)
3. [LLD](docs/LLD.md)
4. [Acceptance matrix](docs/ACCEPTANCE.md)
5. [Shared worker contracts](docs/CONTRACTS.md)
6. [Setup and eight-worker assignments](docs/SETUP_AND_WORKERS.md)
7. [Runbook](docs/RUNBOOK.md) — stage-specific operational limits, not a claim of deployed service.

## Authorization and remaining release gates

Implementation, eight concurrently isolated workers, existing PostgreSQL project/test resources, bounded synthetic provider checks and eventual publication to `https://github.com/Komal-py/money_management_bot.git` on `main` are approved. Credential/preflight/pairing setup completed; credentials stay ignored, and source Hermes configuration is unchanged.

Remaining: complete controller/NL/calendar assembly, complete application acceptance, release/security checks, final build/entrypoint verification, real bounded synthetic startup smoke, then repository publication/readback and remote CI. **No GitHub push or live application deployment has occurred.** Laptop long polling needs the laptop awake and online; always-on hosting is a separate decision. The temporary three-hour wake-lock expired without changing the sleep plan. Provider tariffs remain unverified.
