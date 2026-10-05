# Low-level design — Telegram budget bot

**Status: design contracts and defaults approved for implementation; no implementation evidence yet.** Read [REQUIREMENTS.md](REQUIREMENTS.md) and [HLD.md](HLD.md) first. The owner explicitly approved D01–D09; D10 operational decisions remain unresolved. Descriptions of proposed schemas/code below mean not yet implemented, not silently unapproved policy. No DDL, package install, database, or live API call is executed by this document.

## 1. Proposed package boundaries

```text
telegram_bot/
  pyproject.toml / uv.lock          # created during implementation
  config.example.toml             # non-secret configuration
  .env.example                    # secret names only, no usable values
  src/budget_bot/
    bootstrap.py                  # settings, dependency wiring, lifecycle
    config.py
    telegram/
      ingress.py                  # explicit durable getUpdates ingestion
      commands.py                 # deterministic typed command parsing
      callbacks.py                # token resolution + owner/revision checks
      menus.py / calendar_ui.py
      delivery.py                 # outbox delivery and acknowledgements
    workflows/
      graph.py / state.py / nodes.py
      setup.py / reviews.py       # proposal and review persistence
      runner.py                   # serialized start/resume and recovery
    domain/
      money.py / actions.py       # exact money + discriminated actions
      ledger.py / preview.py      # shared deterministic effect planning
      corrections.py / targets.py
      errors.py
    services/
      access.py / finances.py / reports.py
    storage/
      models.py / repositories.py / unit_of_work.py
      inbox.py / outbox.py / workflows.py
    ai/
      schemas.py / provider.py / privacy.py / rendering.py
    observability.py
  alembic/                        # application table migrations
  tests/
    unit/ integration/ graph/ transport/ live_synthetic/
  docs/
```

Dependency direction: Telegram/AI/workflow adapters → services → domain/repository interfaces. Domain code never imports Telegram, LangGraph, or an LLM SDK. The bootstrap owns concrete wiring. No unrestricted AI tools, shell access, ORM session access, or dynamic SQL are exposed to the model.

## 2. Money, dates, and input validation

### Money

- All stored money and posting deltas use PostgreSQL `BIGINT` integer paise, with checked arithmetic and per-operation range validation.
- External/model amounts are **decimal strings**, never JSON floats. Parse using Decimal, reject non-finite values, exponent notation, unexpected signs, zero/negative action amounts, and more than two fractional digits. Convert exactly to paise without rounding.
- Starting money may be zero. Normal income/allocation/transfer/expense amounts must be positive. An absent target means no target; a present target must be positive.
- Proposed per-field supported money range: up to `999999999.99` INR; aggregate storage still rejects BIGINT overflow. This is an implementation limit for review, not a discovered provider limit.
- Commands use strict documented amount syntax. Natural-language normalization may support Indian separators such as `1,00,000` only with validated grouping, and suffixes such as '2k' only with an explicit normalized amount in the review.
- Format amounts deterministically as INR with two fractional digits; model wording cannot change monetary figures.

### Dates and timezones — proposed D05 policy

- Store `bookkeeping_date DATE` on each logical transaction and `received_at`/`committed_at TIMESTAMPTZ` in UTC for audit.
- User preference is an IANA timezone, initially `Asia/Kolkata`; bundle tzdata in the project for Windows portability.
- Resolve 'today/yesterday/last Monday' relative to trusted Telegram message receipt time and the user's timezone captured for that request—not when a delayed worker runs.
- When the user gives only a date, do not invent a time-of-day. Review shows the date, timezone, and 'defaulted to message-day' when omitted. UTC audit timestamps are not presented as user-stated purchase times.
- Today and past expense dates are supported, including dates before opening; reject future expense dates. A historical date controls reporting, but the newly recorded expense still deducts from the current tracked bucket balance on confirmation. For a date before opening, explicitly warn that amounts already accounted for in starting money must not be entered again. Do not silently assume an expense was already included or reject a past date that the approved scope permits. Income/allocation/transfer bookkeeping-date behavior is a proposed D05 detail: use receipt-day unless an explicit supported past date is requested.
- Ambiguous dates such as `03/04` need clarification or an explicit year/month/day calendar selection. Impossible dates and future expenses fail validation before confirmation.
- Changing timezone affects future relative-date interpretation and the current report month, not already chosen bookkeeping dates. A pending date-defaulted review is invalidated if timezone changes.
- Spending reports filter active expenses by bookkeeping date. Report ranges are inclusive externally; SQL uses `date >= start AND date < end + one day`. Weeks start Monday; monthly ranges use the real calendar, including leap years.

### Names and text

- Bucket display name: 1–60 trimmed characters; reject control characters and markup tricks. Store an owner-scoped normalized key for case-insensitive duplicate prevention.
- Description: 1–300 characters; bounded and escaped for Telegram formatting. It is data, never an instruction.
- Exact normalized bucket matches can resolve directly. Fuzzy/suggested matches require review; multiple similar candidates require explicit selection.
- Existing/staged bucket IDs are generated and resolved by backend code. The LLM outputs names or proposal-local references, never database identifiers.
- Reject extra/unknown schema fields, unsupported actions, malformed metadata, and missing required values. A syntactically valid model output is still only a proposal.

## 3. Database model

All UUIDs/IDs below are backend-generated. Telegram IDs are stored as BIGINT. For every owner-owned record, use `(owner_id, id)` uniqueness and **composite owner-aware foreign keys** so a child cannot reference another user's object. Repository filters always require trusted owner context; database constraints back that up.

| Table | Important fields | Constraints / responsibility |
|---|---|---|
| `bot_users` | id, telegram_user_id, access_status, timezone, onboarding_status, opening_date, state_revision, created_at | Unique Telegram user ID; status changes invalidate pending writes; user row is the financial lock target. No administrator finance-read methods. |
| `invites` | id, code_digest, created_by_admin_id, expires_at, revoked_at, redeemed_by, redeemed_at | Unique digest; atomic single redemption; admin identity comes from configured trusted Telegram IDs, never model output. |
| `accounts` | id, owner_id, kind POOL/BUCKET, display_name, normalized_name, balance_paise | One pool/user; unique bucket name/user. Pool balance ≥ 0; bucket balances may be negative. No rename/archive/delete routes. |
| `financial_batches` | id, owner_id, request_id, review_revision, committed_at, immutable_result_json | Unique `(owner_id, request_id)`; one financial commit per request. Result snapshot supports replay without recalculation. |
| `logical_transactions` | id, owner_id, originating_batch_id, action_type, current_revision_id, status ACTIVE/UNDONE, committed_order | Stable identity for correction/undo. Opening/income/allocation/transfer/expense only; metadata not disguised as expense. |
| `transaction_revisions` | id, owner_id, transaction_id, revision_no, amount_paise, source_account_id, destination_account_id, bookkeeping_date, timezone_at_entry, description, supersedes_revision_id, change_reason, applied_by_batch_id | Unique transaction/revision; immutable old versions. Current revision changes only inside financial commit. |
| `ledger_postings` | id, owner_id, batch_id, logical_transaction_id, account_id, delta_paise, reverses_posting_id, created_at | Append-only signed account effects; no deletion/update during correction. Each posting belongs to same owner/batch/account. |
| `target_versions` | id, owner_id, bucket_id, effective_month, target_paise nullable, revision_no, created_at | Append-only optional target timeline; NULL is explicit removal. Unique bucket/month/revision; current-month changes do not rewrite historical choices. For a report month choose the newest revision at the greatest effective_month not later than that month. |
| `target_crossings` | owner_id, bucket_id, month_start, target_revision_id, triggering_batch_id | Idempotent warning record for a below-to-at/above crossing; later overspending warning can be derived per new expense result. |
| `requests` | id, owner_id, trusted_update_key, kind, status, graph_thread_id, proposal_revision, last_activity_at, expires_at | One active mutation request/user via partial unique index; supports setup, query, or mutation. |
| `proposals` | id, owner_id, request_id, revision, actions_json, captured_timezone, reference_received_at, state_revision_at_preview, result_preview_json, created_at | Immutable proposal revisions, typed/schema-versioned JSON; never source of authorization by itself. |
| `review_tokens` | token_digest, owner_id, request_id, proposal_revision, expected_step, allowed_action, expires_at, consumed_at | Opaque short callback tokens; do not encode amounts, secrets, or raw UUID authorization in button data. |
| `resume_requests` | id, owner_id, request_id, trusted_update_key, expected_step, proposal_revision, validated_answer_json, status, original_result_json | Unique callback/reply identity. Old replay returns its original outcome; cannot answer a newer interrupt. |
| `telegram_inbox` | bot_identity, update_id, received_at, owner_id nullable, sanitized_payload, status, attempts, due_at, lease_version | Unique bot/update; durable work. Unregistered updates may only route to access/help, not money operations. |
| `polling_cursor` | bot_identity, next_offset | Persist after full-page ingestion in the same transaction; never advance based on handler completion alone. |
| `telegram_outbox` | id, owner_id, chat_id, request_id, payload_revision, payload_json, dedupe_key, status, due_at, lease_version, delivered_message_id | Durable payload revisions; acknowledge exactly the payload/version sent. Unique local delivery intent does not imply exactly-once Telegram acceptance. |
| `ui_sessions` | id, owner_id, kind CALENDAR/SELECTION, context_json, revision, expires_at | Owner-bound read-only navigation and correction candidate selection. |
| Library checkpoint tables | thread/checkpoint identifiers, state, pending writes | Separate `workflow` schema; use supported PostgreSQL checkpointer setup, not ad hoc migrations.[6] |

**Schema ownership:** application-owned tables in `budget`; library-managed checkpoints in `workflow`. Alembic metadata includes all application tables, including inbox/outbox/reviews; exclude library tables from autogeneration. Prefer separate migration and restricted runtime roles. Append-only permissions/constraints and service invariants need real DB tests; writing a table sketch is not evidence that those guarantees exist.

**Read performance:** indexes on `(owner_id, bookkeeping_date)` for effective expense queries, `(owner_id, bucket_id, bookkeeping_date)` via a query view/join plan, and status/due fields for inbox/outbox recovery. Do not store derived monthly balances as a second authority.

## 4. Typed contracts

The following are illustrative schemas/contracts, **not runnable modules**.

### Trusted context (never from the model)

```text
ActorContext {
  owner_id, telegram_user_id, chat_id, bot_identity,
  received_at_utc, timezone, access_status
}
```

Derive this from authenticated ingress and database registration. Validate private chat sender and check access again immediately before financial commit.

### AI interpretation

```json
{
  "schema_version": 1,
  "kind": "mutation",
  "actions": [
    {
      "type": "expense",
      "amount_inr": "400.00",
      "bucket_name": "Travel",
      "description": "Metro",
      "date_expression": null
    }
  ],
  "missing_fields": [],
  "clarification_question": null
}
```

Discriminated alternatives are `query`, `clarification`, and `unsupported`. Query fields are a supported report type, period expression/explicit dates, and optional owner-resolved bucket names. No free-form SQL or generated report totals. Action alternatives:

| Action | Required proposed fields | Backend-resolved effects |
|---|---|---|
| `create_bucket` | name | Staged owner bucket, zero balance |
| `income` | amount string, description, optional supported date | Credit pool |
| `allocate` | amount string, bucket name | Pool → bucket |
| `transfer` | amount string, source/destination bucket names | Bucket → different bucket |
| `expense` | amount string, description, bucket name or missing marker, date expression | Debit bucket, spending record |
| `undo` | transaction/batch reference text or selected candidate | Resolve owner history and compensate |
| `correct` | transaction reference + requested replacement fields | Resolve existing action; reverse/reapply net approved effects |
| `set_target` | bucket name, amount string or explicit remove flag | Current-month onward target version |

Guided opening creates a backend-only `opening` action. AI cannot repeat opening setup or reset starting money. Add a partial unique constraint permitting at most one opening logical transaction per owner, and commit opening plus onboarding completion atomically. Zero opening is a valid recorded setup event with no nonzero posting. Resuming `/start` after completion shows the menu, never creates another opening.

Raw 'add money to bucket' remains ambiguous unless the request explicitly identifies existing pool money or new income. A model confidence number cannot override that rule.

### Shared deterministic service

```text
preview(actor, proposal) -> ReviewSnapshot | Clarification | DomainRejection
apply_confirmed(actor, request_id, proposal_revision, expected_state_revision)
    -> CommittedResult | AlreadyCommittedResult | StaleReview | DomainRejection
get_spending(actor, ReportQuery) -> SpendingReport
get_calendar_day(actor, local_date) -> CalendarDayReport
```

`ReviewSnapshot`: ordered labeled actions, resolved accounts, exact amount/date/description, old/projected balances, tracked total, target/negative warnings, revision, expiry. No unreviewed balance writes.

`CommittedResult`: immutable batch ID, logical transaction IDs, exact account changes/resulting balances, warnings, effective report dates, and optional narration facts. This is persisted once and replayed as-is; 'already recorded' is not a second write.

`SpendingReport`: inclusive range, timezone/bookkeeping semantics, per-bucket paise totals, grand total, expense count, optional requested bucket filter. `CalendarDayReport` adds active expense rows with deterministic pagination.

**Errors:** `MissingField`, `InvalidMoney`, `InvalidDate`, `UnknownBucket`, `AmbiguousBucket`, `InsufficientSourceFunds`, `AmbiguousTransaction`, `StaleReview`, `ExpiredRequest`, `AccessDenied`, `AlreadyApplied`, `ProviderUnavailable`, `InvalidModelOutput`, `WorkflowBusy`. Domain rejection rolls back; provider failures preserve deterministic paths. Infrastructure failures remain retryable failures, not successful replies.

## 5. Financial commit algorithm

The same pure effect planner is used for previews and execution so rules do not drift.

```text
1. Acquire workflow serialization outside the financial transaction.
2. Begin database transaction.
3. Lock owner's user row; validate active access and completed/allowed setup state.
4. If a committed batch exists for this owner/request, return its saved result.
5. Lock the request/review and validate exact proposal revision, expected step,
   expiry, and confirmed decision. Verify state revision matches the review.
6. Resolve owner accounts/transactions from trusted DB reads, never model IDs.
7. Re-plan the ordered batch against current balances. Validate every action;
   stage created buckets and targets in the same transaction.
8. Append batch, logical transaction revisions and signed ledger postings.
   Update affected account balances, transaction heads, targets, and state revision.
9. Assert balance/ledger invariants and no unsupported source overdrafts.
10. Save immutable committed result, request financial status, and deterministic
    fallback outbox intent in this same transaction.
11. Commit. Only afterward render optional model narration and deliver Telegram.
```

If any step fails, roll back **the entire batch**, including newly created buckets/target changes in that batch. Serialization does not replace unique constraints/idempotency. Concurrent writes by the same user serialize on the user row; unrelated users remain independent.

`state_revision` changes on any data affecting a review: financial changes, target changes, bucket creation, timezone/access changes. Stale confirmation triggers a new preview and explicit fresh approval, not silent application to new balances.

### Core invariants

1. Account balances equal the sum of all signed postings for those accounts; initially zero.
2. Pool is never negative. Bucket balances can be negative through recorded expenses only, or remain negative while money is added.
3. Opening/income batch posting sum equals positive net cash; expense sum equals negative net cash; allocation/transfer sum is zero. Corrections follow net compensated totals.
4. Total tracked money equals pool plus all buckets, including negative buckets; it also equals opening plus active net income minus active net expenses.
5. No transfer has identical source/destination; no positive amount comes from a source with less than the required non-expense reduction.
6. One request creates at most one committed financial batch, irrespective of duplicated callbacks, graph replay, or restarted workers.
7. Every revision/posting/reference belongs to the authenticated owner.
8. Reports use effective active expense revisions, not raw posting counts; reversals are not new income/expense categories.

**Ordered batches:** income can fund a later allocation in the same reviewed batch. An expense can make a bucket negative; a later transfer *out* of that negative bucket is blocked. Order is visible and fixed in the review. Limit to the proposed D09 action count; reject/split larger input rather than partially processing it.

## Corrections and undo

The original transaction details and all prior revisions/postings stay immutable; only the logical transaction's current-revision pointer and effective status change. The applied correction creates a new revision and compensating postings, with all effects committed atomically. Correct amount/date/description/bucket for expenses; amount/description/date for income; amount and source/destination for allocations/transfers. Changing an action's type requires explicit undo plus a new action, not a disguised revision.

**Proposed D06 source-check policy:**

- For one corrected logical action, calculate the **net account change** from removing its current version and applying its replacement. This avoids rejecting an income increase merely because much of the original income has been allocated.
- For income/opening undo or downward correction, require the pool to cover the net reduction. Never debit a bucket implicitly.
- For allocation/transfer undo/correction, require every account with a net reduction to cover that reduction. A previously negative destination may receive money, but cannot be further drained by a reversal.
- Expense undo credits the original bucket. Expense correction credits back its old effect and applies the replacement expense; the new expense may overdraw its selected bucket, with warning, as normal expenses may.
- Same-bucket expense increase is allowed to make it more negative; source-overdraft rules for transfers do not incorrectly block a legitimate expense correction.
- Recheck the entire change at confirmation. If funds are missing, explain the precise account and required restoration; do not undo unrelated transactions automatically.

For 'undo last', propose the last committed **financial batch**, reversing active financial actions in reverse order and checking each staged effect. This is especially important for combined income/allocation/expense requests. Metadata created with that batch is retained: no bucket deletion feature is introduced by undo. Metadata-only batches are not candidates for 'undo last'. If the latest money change was itself an undo/correction, or a batch's actions have since been corrected/undone, show current transaction/revision candidates and an explicit reversal preview rather than blindly reversing originating postings. Any undo of a correction must restore the selected prior effective version with fresh source-fund validation; repeated undo of an already undone action cannot post again.

Natural-language references such as 'fix yesterday's metro expense to 350' search only this owner's bounded candidate set. The LLM does not provide a trusted transaction ID. Show date, amount, bucket, description, and short display reference; ambiguous candidates get selection buttons.

Reports exclude undone expenses and use the corrected amount, bucket, and bookkeeping date. Ledger/audit queries retain every version. A correction can change target status for both old and new months/buckets; recompute affected aggregates rather than adding a raw reversal to monthly spending.

## 6. Workflow state and durable resume

```text
BudgetWorkflowState {
  schema_version, request_id, trusted_context_ref,
  sanitized_user_text, input_mode,
  captured_timezone, reference_received_at,
  interpreted_plan, resolved_proposal_revision,
  missing_fields, expected_step,
  review_revision, confirmation_decision,
  committed_result_ref, report_snapshot,
  narration_result, terminal_status
}
```

Each thread ID is namespaced by backend-owned owner/request IDs, such as `budget:<owner UUID>:<request UUID>`. It is not selected by the model or supplied freely by Telegram users. Load actor context afresh from the trusted request record.

### Interrupt rules

LangGraph requires checkpointed state for pausing/resuming, and the interrupted node restarts when resumed. `Command(resume=...)` carries the validated answer back to the exact workflow.[1][6]

- Clarification/review nodes may persist idempotent prompts but may not perform financial writes before `interrupt`.
- Do not catch LangGraph's interrupt exception as an ordinary failed model call.
- Bind incoming answer to owner, request, expected step, and revision before accepting it. Validate an amount/date/selection before persisting an accepted answer.
- Store each accepted resume request and its original outcome. On duplicate ingress, return/resend that outcome; never consume the same old reply against a later interrupt.
- Persist due work using the controller's clock explicitly. Workers select due records and acquire a fenced lease; expired workers cannot acknowledge newer work.
- One pending mutation per user is the proposed D04 default; read-only queries get separate threads and do not overwrite waiting action state.

### Locking

Use a dedicated PostgreSQL connection/session advisory lock around a workflow start/resume, with a stable hashed namespaced key. Release/close it after execution. Financial row locks are acquired only within the short apply transaction. When busy, queue the resume instead of losing it or starting competing graph executions. Any operation competing with confirmation—including Cancel, Edit, expiry, target/timezone changes, and access revocation—uses the same lock order: owner user row, request row, then account/transaction rows in stable ID order. Cancel/expiry may terminate only an uncommitted request; if commit won the race, report the saved result rather than claim no money changed.

### Crash recovery table

| Crash boundary | Required recovery |
|---|---|
| Telegram received, before inbox commit | Telegram may redeliver; no offset advanced |
| Inbox committed, before processing | Worker finds pending durable work |
| Prompt saved, before Telegram send | Outbox sends exact pending payload |
| Answer persisted, before graph resume | Durable resume worker retries exact answer/step |
| Money committed, before checkpoint | Replayed apply returns immutable saved batch result |
| Money committed, narration fails | Deterministic committed reply stays deliverable; no re-posting |
| Telegram accepted reply, before local acknowledgement | At-least-once duplicate reply possible; money remains single-application |
| New prompt saved while old prompt delivery is acknowledged | Acknowledge only old payload revision; new prompt remains pending |

Do not mark a resume completed if financial execution failed. Preserve the accepted decision for retry when failure is infrastructural; business rejections return a deliberate terminal/new-review outcome.

## 7. Telegram interaction contract

### Commands and buttons — proposed interface

| Command | Result / guided flow |
|---|---|
| `/start [invite]` | Register/redeem invite or resume setup; registered user gets menu |
| `/help` | Supported examples and safe command syntax |
| `/bucket` | Guided bucket creation |
| `/income` | Amount/description/date collection, then review |
| `/allocate` | Pool-to-bucket selection/amount, then review |
| `/transfer` | Source/destination/amount, then review |
| `/expense` | Amount/description/bucket/date collection, then review |
| `/balance` | Basic pool/bucket/total overview |
| `/target` | Choose bucket and set/remove monthly target; review |
| `/undo` | Select most recent candidate/batch, then review |
| `/correct` | Select transaction and fields to change, then review |
| `/spending` | Period/date-range selection and bucket breakdown |
| `/calendar` | Current month grid and day selection |
| `/settings` | Timezone and preferences |
| `/cancel` | Cancel current uncommitted workflow; never reverse a committed transaction |
| Admin `/invite`, `/revokeinvite`, `/revokeaccess` | Access management only; not an AI tool or finance browsing route |

Detailed shorthand command argument grammar is finalized during implementation and documented from the real parser; commands without arguments always offer guided steps, not just print syntax. All user budgeting actions have deterministic command/button paths during an AI outage. Admin access commands need no model.

### Reviews

Review fields are labeled: action, amount, bucket/source/destination, description, date and timezone, projected balances, negative/target warnings, and expiry. Omit irrelevant/null fields. Financial reviews are deterministic; LLM output cannot hide/edit these fields. `Edit` opens field selection and rebuilds a revision; `Cancel` discards uncommitted work; `Confirm` revalidates.

### Callbacks

Telegram button callback data has a byte-size limit, and clients expect `answerCallbackQuery` after a press.[3] Use compact opaque tokens, such as a prefix plus a random token, bounded under the documented limit. Persist token digest and owner/request/revision/allowed-action scope. Check Telegram sender, chat, token expiry, request state, and current access. Promptly acknowledge the callback even if work is queued.

Disable old buttons after completion, but correctness must come from server-side checks; a user/client can submit stale/forged data. Never trust visible message text or button captions as authorization. No money/secret payloads in callback data.

### Calendar

- Build a Monday-first month grid using a calendar library; month header and Previous/Next controls.
- Display expense-marked dates without leaking transaction text into tokens. Tap queries a current owner-scoped day snapshot.
- Blank grid cells are inert. Leap days/year boundaries are generated programmatically.
- Day view: date, total spending, per-bucket breakdown, and paginated active expenses. Future days can be viewed as empty, but cannot record future expenses.
- Proposed page size: 10 expenses; Telegram text split/escaped safely under message limits. Pagination/navigation is read-only, expiring and owner-bound.
- Present Calendar in the post-setup menu; do not send a daily reminder automatically.

### Transport durability

Use python-telegram-bot's Bot/keyboard types but an explicit ingestion loop whose getUpdates offset is advanced only after durable batch persistence. Handle `message` and `callback_query` explicitly; unsupported/edited messages do not silently alter records. Before activation, inspect webhook state; do not delete an existing webhook or drain pending updates without explaining/approving that bot takeover. One active poller per bot identity; same bot cannot safely be polled by Hermes gateway and this app at once.

## 8. AI provider and output grounding

Use the discovered model/base URL/key reference in HLD. No provider credential is in this document. Protocol/strict-schema support is an unverified integration gate; provider capability docs do not validate a deployment.[8]

### Interpretation adapter

```text
interpret(sanitized_message, allowed_actions, candidate_bucket_names,
          local_reference_date, timezone) -> Interpretation
```

The system instruction treats user text/bucket labels as data. Supply minimal relevant context only; do not send Telegram identity, invite/admin data, unrelated history, full ledger, or raw secrets. Preserve monetary/date digits when redacting contact/account IDs. Bound input length, actions, field lengths, call deadline, and output tokens. Explicitly disable SDK retries for live compatibility probes. Runtime retry policy is bounded and separate from money idempotency.

Refusal, invalid schema, partial/incomplete output, timeout, and unsupported action are distinct safe outcomes. A clarification question is constrained to missing supported fields; never let it request API keys, passwords, or another user's records.

### Conversational rendering adapter

```text
render(trusted_result_facts, language='en') -> NarrationSelection
```

Proposed schema: approved fact IDs and selected IDs from a backend-owned catalog of conversational phrases. Do not accept free-form introductions: a sentence can invent a financial claim even without digits. The backend validates that fact IDs are relevant and that every mandatory balance/warning fact is included, then formats all amount/date/bucket/result lines itself. Invalid selections fall back to deterministic text. Do not rely solely on a 'never hallucinate' prompt or another LLM checking the reply.

The LangGraph render node combines model-selected conversational framing with immutable result/report facts. This still provides natural-language output while keeping balances, 'recorded' status, and analytics authoritative. User review and fallback replies never need the model.

Persist a deterministic outbox result at commit. If narration completes before delivery, replace via compare-and-swap on the unsent payload revision; otherwise keep the already queued/sent result rather than sending contradictory duplicate narration. On delivery failure after remote acceptance, duplicate text remains possible and is disclosed.

### Credential configuration

During an explicitly authorized later setup, store only bot API key, Telegram bot token, and database secret in an ignored restricted project secret file/deployment secret. Non-secret provider/model/base URL/limits go in TOML. Prefer a narrowly copied AI key over runtime reads of Hermes's entire secret file; do not make the bot dependent on Hermes's internal modules. Never use Hermes's Telegram token without separate explicit intent. Verify acceptance without printing token/key contents; no arbitrary redirect follows authenticated requests.

## 9. Monthly targets and analytics

Proposed D07 behavior:

- A configured target persists into later months until changed/removed. Effective target versions begin on the current local calendar month; balances never reset.
- Compute monthly spending from active expense revisions for that bucket/month.
- Warn when a confirmed new action crosses from below target to at/above target, and include a concise over-target warning with subsequent new spending above it.
- Setting/lowering a target below existing month spending warns in its review/result. Corrections/backdated expenses recompute affected bucket/month totals; undo below the threshold permits a future new crossing to warn again.
- Month boundaries alone do not send messages, reset balances, or allocate money.

Spending reports include chosen range, bucket totals, overall total and explicit empty results. A bucket filter reports only the selected scope and labels it; it must not imply an account-wide total. No income/transfer entries count as spending. Natural-language unsupported trend/forecast/advice queries receive a supported-report suggestion, not invented analytics.

## 10. Verification plan

Detailed requirement coverage is in [ACCEPTANCE.md](ACCEPTANCE.md). Minimum boundaries:

1. **Pure domain:** exact conversion, range/zero/NaN rejection, effect invariants, normal overdraft expense, source-fund blocks, corrections and batch order.
2. **Real PostgreSQL:** migrations/constraints, owner-aware foreign keys, atomic invite redemption, rollback of mixed batches, concurrent apply, unique request application, reversal history, inbox/outbox leases.
3. **Real graph + persistent checkpointer:** clarification, edit/cancel, interrupted-node replay, crash after financial commit before checkpoint, old answer replay after new interrupt, simultaneous resumes, AI outage.
4. **Controlled Telegram/SDK transports:** callback authorization and size, offset commit sequence, known schema/refusal/incomplete/error bodies, safe output fallback, calendar/day pagination, adversarial messages/bucket names.
5. **Synthetic live provider (after approval):** actual interpretation schema and render schema on fictional records; report what endpoint/protocol accepted without claiming general quality.
6. **Live Telegram (after setup authorization):** owner and second-user isolation walkthrough, invite/setup, command/NL expense, combined review, cancellation/correction, target warning, reports/calendar, restart persistence.
7. **Frozen-tree release:** single integrated suite with JUnit evidence, lint, package build, artifact identity and secret exclusion checks. A security review/scan remains distinct from passing tests.

No real user money is entered by the agent without explicit values and authorization. No isolated PostgreSQL tests run against production financial tables. No additional database instance or container is provisioned under general permission to test.

## 11. Decisions and limits still visible

- HLD D01–D09 and the stack/defaults are approved for implementation.
- Creation of `telegram_budget`, `telegram_budget_test`, project roles and isolated schemas on the existing server is approved; obtain/verify host, port, login and locally entered credentials before provisioning. Leave unrelated resources untouched.
- Separately routed workers and bounded synthetic AI probes using configured providers are authorized. Verify exact endpoints, credentials and protocol support without exposing values; no verified pricing or cost cap is available yet.
- Production retention/deletion, backup destination/restore schedule, always-on hosting, expected user scale, and cost cap remain deployment gates (D10).
- A local laptop backend stops responding while asleep/offline; a bot created in Telegram does not itself host Python code.
- Exactly-once financial effects are a design goal to prove through idempotency tests. Exactly-once Telegram delivery and provider-wide no-retention are not promised.

## Sources

[1] https://docs.langchain.com/oss/python/langgraph/interrupts
[3] https://core.telegram.org/bots/api
[6] https://docs.langchain.com/oss/python/langgraph/checkpointers
[8] https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/responses?view=foundry-classic
