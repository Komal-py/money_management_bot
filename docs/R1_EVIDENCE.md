# R1 failure and coordinator recovery

The R1 agent run `proc_bce4399db48f` exited 1 after the approved `gpt-6.1-sol` route exhausted its token-rate quota. The three API retries did not complete implementation. The original log/status remains in ignored coordination storage. This is not a successful worker verdict.

## Preserved contribution

R1 added settings regression tests before failing; no startup implementation or worker commit existed. Coordinator inspected the exact saved diff and ran it: 15 failures, 2 passes. A discovered `dotenv_values(None)` call searched the parent credential file despite the test's explicit no-file intent. The ignored XML was sanitized locally, and the test launcher now removes inherited live credentials. No credential values are included here.

## Coordinator implementation and observed verification

- Explicit settings loader: None means no dotenv file; strict AI_ENABLED, optional disabled-AI credentials, safe database/schema/timezone/positive-integer validation, separate migration-only configuration.
- Actual executable `budget_bot.main` and `python -m budget_bot`; offline config checking, explicit Alembic migration operation, no application create_all at startup.
- Composes real store/onboarding/access/reports/workflow/controller/transport; persistent PostgreSQL checkpointer; deterministic W5 renderers; AI interpreter only when enabled.
- Lifecycle owns bot/interpreter/store/checkpointer cleanup. Existing webhook is refused without deletion, polling or financial setup. Ctrl+C/SIGTERM request transport shutdown.
- Runtime regression first failed 5 tests because main was absent. Recovery command `python .private/run_tests.py r1 --schema test_w1 tests/test_runtime.py tests/test_settings.py -q --tb=short` passed **22 tests** in 8.96 seconds using approved existing PostgreSQL test_w1 and controlled real Telegram SDK requests. No live Telegram/AI call or owner financial mutation.
- Targeted Ruff and Git whitespace checks passed.

## Limitations

This is coordinator-tested startup infrastructure, not completed application acceptance. Controller onboarding/NL/calendar wiring is still unfinished. Packaging must be rebuilt after final assembly, and installed-wheel migration-file support remains a separate release check; migration commands currently require the source project directory. No live poller, GitHub push or remote CI was performed by this recovery.
