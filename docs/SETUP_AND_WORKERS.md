# Setup inputs and eight-worker delivery plan

## Current authorization

The owner approved full implementation/testing, the proposed stack and HLD/LLD defaults, eight separate Hermes worker processes with session-only model/effort selection, bounded synthetic bot API checks using configured providers, and a push to `main` at `https://github.com/Komal-py/money_management_bot.git`. The owner explicitly approved creating `telegram_budget` and `telegram_budget_test`, project roles, and isolated schemas on the existing PostgreSQL server, with no unrelated changes or additional server/container. Connection details supplied: host `localhost`, port `5432`, maintenance database `postgres`, setup login `postgres`. The maintenance database is for setup connection only, not application storage. The owner now requires all setup inputs/permissions collected and verified first, then an explicit **start coding** request before any implementation worker/code starts. Local password/token entry, Telegram admin pairing and GitHub write authentication are still setup gates. Cached GitHub access can read the public target but the repository API reports no push permission; do not confuse public read access with write authority. Hosting/operational decisions remain separate. Do not treat credentials as permission to inspect unrelated data.

No implementation workers are running yet. This file is a delivery plan, not a claim of completed setup.

## Where the owner is needed

| Boundary | Owner provides/approves | Coordinator handles |
|---|---|---|
| GitHub target | Approved `https://github.com/Komal-py/money_management_bot.git`, branch `main`; write authentication still unverified | Inspect only target repository, preserve existing work, commit/push without force, verify remote commit SHA |
| GitHub authentication | If existing Git Credential Manager cannot authenticate, complete browser/device login or enter a repository-scoped token locally | Check target read/write permission, configure project-scoped access as needed; no password/token in URLs or chat |
| PostgreSQL connection | Host, port, login role, database names if already created, and password entered locally | Verify exact connection, discover only project-target privileges/objects, do not browse unrelated database contents |
| PostgreSQL provisioning | Explicit permission to create `telegram_budget`, `telegram_budget_test`, project roles and `budget`/`workflow` schemas on the supplied existing server, OR supply isolated existing project databases | Create only approved resources, apply migrations, give each test worker isolated owned schema; no new server/container, no unrelated drops/changes |
| Telegram bot | Create/select a dedicated bot through BotFather, paste its token locally; confirm it is not used by another integration | Verify getMe/webhook status, configure menus, run backend and exercise bot paths. No destructive webhook takeover without approval |
| Human administrator | Numeric Telegram user ID if known; otherwise send one exact pairing challenge in the dedicated bot | Validate private sender challenge, configure admin role; no first-sender or bot-ID ownership inference |
| AI provider | Already authorized Hermes AI-key reuse; confirm bounded quota/billing use for bot probes and coding workers, and a cost cap if available | Use only configured approved provider routes/key reference, no key output, preserve Hermes working settings; verify actual schemas |
| Design policies | Approve/revise the HLD/LLD proposed defaults, especially request expiry, dates, correction/batch undo, targets, invite revocation and usage caps | Freeze approved rules and contracts before dependent implementation; preserve selected scope/exclusions |
| Live user walkthrough | Send a few fictional messages/click confirmations and calendar; ideally a second Telegram account/user joins for live isolation acceptance | Run automated unit/integration/transport/failure tests and verify recorded results; distinguish constructed second-user tests from a real second user |
| Hosting | No input needed for initial local build. An always-on deployment later needs explicit host/cost/backup/retention decisions | Provide verified local start/stop instructions; laptop sleep/offline means no bot response; no silent cloud deployment |

The owner need not write code, design database tables, install Python libraries manually, coordinate workers, run test commands, or make Git commits. Browser login/BotFather interactions and intentional live financial confirmations remain owner-controlled.

Secrets are entered only into a restricted local ignored project file or supported credential manager. `.env` is plaintext, not an encrypted vault. Never print its values or copy it into worker worktrees, logs, test artifacts, GitHub or documents. Prepare exact local entry fields only after scope is known; preserve any existing settings.

## Eight worker assignments — proposed, not dispatched

The parent is the **coordinator**, additional to eight distinct worker roles. Before dispatch, the coordinator owns the shared typed contracts, project dependency lockfile, settings/bootstrap interfaces, file ownership map, test commands, and approved policy baseline.

| Worker | Owned slice | Targeted evidence | Initial reasoning / escalation |
|---|---|---|---|
| W1 | Pure money/action effect planner, source-fund rules, correction/undo effects | Test-first exact amounts, conservation, overspending, reversal and ordered-batch cases | Medium; high only for concrete unresolved financial failure |
| W2 | Application DB models, migrations, repositories, atomic financial persistence | Real approved PostgreSQL constraints, ownership, rollback, concurrency and idempotency | Medium; bounded high for locking/transaction defects |
| W3 | Invite/access service and guided initial setup | Redemption, admin isolation, Back/Edit/Cancel, single opening and setup completion | Low/medium; escalate only failing state/ownership cases |
| W4 | Telegram ingress/outbox transport, commands, callbacks and menus | Durable polling offset, sender/token authorization, duplicate delivery, deterministic commands | Low/medium; bounded high for crash/lease races |
| W5 | AI SDK adapter, structured action interpretation, privacy, phrase/fact rendering | Controlled real-SDK wire/schema/refusal/incomplete/timeout tests; coordinator owns authorized live probes | Low/medium; escalate compatibility defects only |
| W6 | LangGraph start/resume, proposal reviews, workflow recovery and locks | Actual persistent checkpoints, stale reviews, replay, interrupt routing and post-commit crashes | Medium; high for concrete recovery/race defects |
| W7 | Spending reports, target versions/warnings and calendar UI | Real aggregates, active revisions, dates/leap months, read-only callbacks and pagination | Low/medium; escalate only boundary failures |
| W8 | Independent acceptance/failure tests, security review, packaging/CI and handoff documentation | Tests against frozen real components, artifact/secret checks, findings with evidence, CI configuration | Low for docs/CI; medium review; high only a named consequential path |

Each worker also writes/runs its component tests; W8 is not a substitute for test-first development. W8 can propose fixes but must not edit another worker's owned files concurrently. Coordinator integrates/wires returned work, resolves shared-file changes, runs final integrated suite/build, manages secrets/live services, and is the only GitHub publisher.

## Scheduling to prevent dependency stalls

1. **Setup gate:** validate exact target repo/database/provider/Telegram permissions without unrelated access. Resolve proposed financial policies. Establish local Git and dependency environment.
2. **Contract gate:** coordinator writes shared schemas/service contracts and freeze file ownership. Inputs, outputs, identifiers, errors, transaction ownership, sync/async behavior, and idempotency are explicit.
3. **Foundation wave:** W1/W2/W5 and W8's initial harness/acceptance design work against frozen contracts. Launch at most two or three simultaneously initially; eight roles do not mean eight concurrent paid calls.
4. **Integration wave:** after verifying relevant foundation outputs, W3/W4/W6/W7 receive the actual contracts and component revisions. Do not launch a task that must wait on an unpublished sibling interface.
5. **Assembly gate:** parent connects the real modules and corrects contract drift; component workers run targeted suites only. Shared settings/lockfiles/migrations are edited only by the named owner.
6. **Frozen verification gate:** stop source edits, run complete real-db/graph/transport suite with machine-readable evidence, lint and build. W8 reviews concrete frozen paths; changes require a new final run.
7. **Live gate:** authorized synthetic provider calls and dedicated Telegram walkthrough, with explicit limits and fictional records. No owner money entered from inferred past values.
8. **Publish gate:** secrets/artifact review, commit and push only approved branch; verify exact remote SHA and CI results before success report.

Use isolated Git worktrees under project-owned ignored coordination storage or an approved sibling worktree directory. Each test worker uses an independently named approved schema and bounded database lock/statement deadlines. Workers never push, provision shared resources, wait on siblings, alter Hermes global config, or start long-lived processes they expect to survive child teardown. Parent owns live poller/server and final suite.

This removes circular task dependencies and reduces edit/resource conflicts. No responsible coordinator can promise zero hangs, rate limits, or database deadlocks; detect timeout/lock/rate-limit failures, pause affected work, diagnose, and retry with explicit bounded budgets.

## Model and effort controls

The active `delegate_task` tool has no per-task model/reasoning parameters. Do not invent them, change Hermes globally, or pretend a prompt changed the worker's actual model.

For per-worker routing, use **separate Hermes worker processes** with verified CLI session-only `--model`, `--provider`, `--reasoning`, `--max-turns`, and `--run-budget` flags and isolated worktrees. These are independently launched agents, not the native delegate tool's child objects; parent owns their process lifecycle/results. Alternatively use native subagents at an explicitly approved common route, but that cannot provide different per-task settings through the current tool schema.

Configured candidates observed locally:

- `azure-kiro-kimi` / `Kimi-K2.5`
- `azure-kiro-glm` / `FW-GLM-5.3`
- `azure-kiro-astra` / `gpt-6-astra`
- `azure-kiro-sol61` / `gpt-6.1-sol`

Configuration presence does not prove credential acceptance, supported effort levels, price, or quota availability. GLM previously rate-limited the attempted design review. Verify configured routes and owner/provider tariff before labeling one cheaper; do not infer prices from model names. Start routine tasks on a verified lower-cost candidate at low/medium effort, retain medium on consequential financial work, and upgrade only a failed/reasoning-heavy bounded subtask after diagnosis. Max effort is not the default. Record requested/effective route where observable, retries, caps, and limitations; never claim dollar cost when no reliable billing data is available.

The bot's user-facing model remains the approved Hermes AI route unless the owner changes it; coding-worker model choices are separate from the bot's runtime provider.

## Completion means

- All approved features connected through actual bot/service/graph paths, not stubs.
- Real isolated PostgreSQL tests for required atomicity/constraints/locking.
- Full suite/lint/build on the frozen source, machine-readable results, and secret exclusion.
- Actual authorized model acceptance and Telegram interactions, labeled separately from controlled tests.
- Verified GitHub target/branch/commit and CI status.
- Honest outstanding operational or owner-operated gates; no claim of bug-free software or guaranteed availability.
