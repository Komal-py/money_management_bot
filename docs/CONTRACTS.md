# Frozen implementation contracts

Approved scope is docs/REQUIREMENTS.md plus D01–D09 in HLD/LLD. Setup verified and start coding authorized. Coordinator owns integration, settings, dependency locks, live credentials and GitHub publishing. Worker instruction-file write was blocked by the approval system; these rules are supplied explicitly in worker prompts, not through a replacement instruction file.

## Shared data (plain dictionaries, JSON-safe)

Amounts are signed integer paise. Input amount strings converted exactly by domain.money; bool/float/exponents/nonfinite/more than two decimals rejected. IDs are UUID strings except Telegram IDs. Dates are ISO strings; receipt timestamps aware UTC ISO. Exceptions: `BudgetError(code, message)` with `.code`, safe `.message`, subclass ValueError in domain.errors.

Snapshot:
```
{"owner_id":uuid, "revision":int, "onboarded":bool, "timezone":"Asia/Kolkata", "pool":int,
 "opening_date":ISO_date_or_null,
 "buckets":{"Travel":{"id":uuid,"balance":int,"target":int_or_null}},
 "transactions":[{"id":uuid,"batch_id":uuid,"type":str,"amount":int,"bucket":name_or_null,
  "source":name_or_null,"destination":name_or_null,"description":str,"date":ISO_date,
  "active":bool,"revision":int}],
 "targets":[]}
```

Actions dictionaries use `type`: opening/income/allocate/transfer/expense/create_bucket/undo/correct/set_target.
`amount_inr` exact string; bucket fields `bucket_name`, `source_bucket`, `destination_bucket`; `description`; `date_expression` null/today/yesterday/ISO date. create_bucket uses `name`. undo uses `transaction_id` (backend-resolved) or `batch_id` or `last=true`. correct uses `transaction_id` and `changes` with same action fields. set_target uses `bucket_name`, `amount_inr` or `remove=true`. opening is backend-only and cannot repeat. max 8 ordered actions. Bucket names case-insensitive owner uniqueness, trimmed 1–60 chars. Descriptions 1–240. No implicit source funding. Future expenses invalid; historical expenses deduct current money and warn.

Interpretation: `{"schema_version":1,"kind":"mutation|query|clarification|unsupported","actions":[],"missing_fields":[],"clarification_question":null,"query":null}`. query `{report:"balances|spending|calendar",period:"today|week|month|range",start:null,end:null,bucket_name:null}`. No owner IDs, arbitrary SQL or model-generated totals. W5 validates strict schemas and redacts secrets/contact identifiers without corrupting money/date strings. NL undo/correct references must be selected from owner-scoped candidates, not trusted model IDs.

## W1 domain APIs (owns src/budget_bot/domain/** and tests/test_domain*.py)
- `money.parse_money(value:str, allow_zero=False)->int`; `format_money(paise:int)->str` (₹, two decimals).
- `dates.resolve_date(expression, received_at:datetime, timezone:str)->date`; `local_today(received_at, timezone)`.
- `planner.plan(snapshot:dict, actions:list[dict], received_at:datetime)->dict` pure; never mutates arguments. Uses snapshot timezone.
- Plan `{snapshot: updated_snapshot, postings:[{account:"pool" or canonical_bucket_name,amount:int,transaction_id:uuid}], events:[{transaction:dict,previous:dict_or_null}], metadata:[dict], warnings:[str], actions:[canonical_action], summary:[str]}`. New transactions IDs assigned once when proposal is first stored; persist exact plan, but confirmation re-plans and compares revision, not fresh financial IDs. Correct/undo events retain logical transaction ID, increment revision and previous state. Undo excludes undone/report reversals. opening sets onboarded and opening_date; zero allowed. Bucket creation/target changes represented metadata; no destructive lifecycle.
- Transfers/allocations source funds required; expense only may go negative. Undo last reverses most recent active financial batch in reverse order; later corrections cannot blindly reverse original versions. All failures BudgetError and no partial plan.

## W2 storage APIs (owns src/budget_bot/storage/**, alembic/**, alembic.ini, tests/test_storage*.py)
SQLAlchemy 2 + PostgreSQL + Alembic, actual tests using BUDGET_TEST_DATABASE_URL and BUDGET_TEST_SCHEMA. Apply app-owned models/migrations; library-managed LangGraph checkpoints belong W6.
`BudgetStore(database_url:str, schema="budget")`; methods synchronous, datetime explicit:
- `initialize()` creates only own schema/tables for test convenience (production coordinator uses migration). `close()`.
- `ensure_admin(telegram_id:int)->owner_uuid`; `get_user(telegram_id)->dict|None`; `get_snapshot(owner_id)->snapshot` checks active; groups auth handled ingress.
- `create_invite(admin_owner, now, ttl_hours=24)->code`; `redeem_invite(code, telegram_id, now)->owner_uuid` atomic single use; `revoke_user(admin_owner,target_telegram_id,now)` access only, admin cannot fetch another owner's finances through controller.
- `set_timezone(owner_id,zone)` validates zone, increments state revision; `list_access(admin_owner)->list` excludes financial fields.
- `propose(owner_id, actions, received_at, request_id=None)->review`; one pending mutation, 30m expiry. review `{request_id,revision,owner_id,status,actions,plan,expires_at,state_revision}`. Calls pure planner; stores proposal not ledger. `get_pending(owner_id)->review|None`.
- `confirm(owner_id,request_id,review_revision,now)->result`; same lock order owner/request/accounts; review expiry/access/state revision checked. stale returns renewed review with `status="stale"` and increments review revision, no writes. result `{status:"committed",batch_id,request_id,snapshot,warnings,summary}` persisted immutable; duplicate returns prior result. All postings/events/metadata + onboarding + request completion + response atomically. `cancel(owner_id,request_id,now)->dict`; commit-won returns committed result. `edit(owner_id,request_id,actions,received_at)->review` revision bumps, no financial writes.
- `spending(owner_id,start:date,end:date,bucket_name=None)->dict` sums current active expense revisions only, inclusive dates; `{start,end,by_bucket,total,count,expenses}`. `calendar_day` equivalent or use spending.
- `save_update(bot_id,update_id,payload,now)` unique durable inbox; `pending_updates(limit=100)->list`; `complete_update(update_id,replies,now)` atomically durable outbox + completed status; `polling_offset(bot_id)->int` contiguous durable-received offset. `pending_outbox(now,limit=20)->list`; `ack_outbox(id,lease_token,now)` fenced; `fail_outbox(id,lease_token,now)` retry. Outbox record `{id,chat_id,text,keyboard,lease_token}` keyboard JSON-safe, claim via row locking. Worker transport must not drop pending updates. If scope too big document exact unfinished methods, no misleading stubs.

## W5 AI APIs (owns src/budget_bot/ai/**, tests/test_ai*.py)
`AIInterpreter(base_url,api_key,model,timeout=30)` async `.interpret(text:str,bucket_names:list[str],received_at:datetime,timezone:str)->dict`; `.close()` async. Responses API strict schema, low effort, store=False, no redirects, zero SDK retries. No source config/.env reads. Ambiguous add funding must return clarification; unsupported features explicit. Invalid/refusal/incomplete/timeout raise BudgetError("provider_unavailable" or "invalid_model_output",safe message). Wire tests httpx.MockTransport through real SDK, no live calls from workers. Renderer deterministic `render_review(review)`, `render_result(result)`, `render_balances(snapshot)` returns English text with mandatory exact facts/warnings; no ungrounded AI financial prose.

## W3 setup/access UI APIs (owns src/budget_bot/services/onboarding.py, access.py, tests/test_onboarding*.py)
`OnboardingService(store)` synchronous `handle(owner_id,text,now)->dict`, `back(owner_id,now)`, `cancel(owner_id,now)`. State persisted in a dedicated owner setup state table/repository in these owned modules; use store.engine and schema but do NOT modify W2 models. Return `{text,keyboard:[[{text,data}]],review:null_or_review,done:bool}`. one question at time: opening money, bucket name(s), initial allocation, add more or review; allow zero opening; no writes until store.propose + confirm. resuming /start if onboarded menu not re-opening. Back/edit/cancel/restart/invalid values tested. `AccessService(store).handle_admin(owner_id,command,args,now)->dict` invite/list/revoke only. Keyboard data for setup `setup:back|cancel|finish|more`; review buttons from W6, not own confirm implementation.

## W6 workflow API (owns src/budget_bot/workflows/**, tests/test_workflow*.py)
`BudgetWorkflow(store,interpreter=None,checkpointer=None)` async `submit(owner_id,actions,received_at)->dict`; `natural_language(owner_id,text,received_at)->dict`; `decide(owner_id,request_id,revision,decision,now,edited_actions=None)->dict`. Returns `{text,keyboard,review,result}` using W5 renderers (fallback import may be locally adapted after coordinator integration). `decision=confirm|edit|cancel`. Store pending review authority, actual LangGraph StateGraph and interrupt/Command resume persistent PostgreSQL checkpoint. thread owner/request namespace, session advisory locking not financial transaction across AI. durable resume identity/replay, same request revision binds callbacks. Clarifications owner-scoped and persisted; `answer(owner_id,text,now)` resolves missing fields/current edit, never an old answer to new step. Test actual graph and real store; controlled interpreter. If `checkpointer=None`, tests can use InMemorySaver but production exposes postgres factory. W6 owns workflow schema setup only and supported PostgresSaver factory; no other DB provision.

## W7 report APIs (owns src/budget_bot/services/reports.py, src/budget_bot/telegram/calendar.py, tests/test_reports*.py)
`ReportService(store).balances(owner_id)->str`, `.spending(owner_id,period,received_at,start=None,end=None,bucket_name=None)->str`, `.day(owner_id,date_string)->str`. Monday weeks, month inclusive, totals deterministic, owner scope.
`calendar.month_view(year,month)->{text,keyboard}`; callback `cal:YYYY-MM` / `day:YYYY-MM-DD[:page]`, valid bounded date; `.day_view(store,owner_id,date,page=0)->dict`. private/current owner bound by controller; no financial writes, pagination 8 entries. Calendar month nav leap/year boundaries. Monthly target warning planner/store based current active expenses & target, report reflects target history; parent handles integration gaps.

## W4 transport APIs (owns src/budget_bot/telegram/commands.py, transport.py, tests/test_telegram*.py)
`commands.parse_command(text,received_at,timezone)->dict` `{kind:"mutation|query|admin|setup|cancel|timezone|help",actions:[],query:null,command,args}`; use shlex; /income 1000 Salary; /bucket Travel; /allocate 500 Travel; /expense 400 Travel "Metro" [YYYY-MM-DD]; /transfer 100 Travel Food; /undo [id|last]; /correct id amount=350 bucket=Food date=... description=...; /target Travel 2000|off; /balance; /spending today|week|month|start end [bucket]; /calendar; /start [invite]; /invite; /users; /revoke telegramid; /timezone zone; /cancel; /help. Every financial command creates actions, never commits.
`TelegramTransport(bot,store,controller)` async `.poll_once(now)->int`, `.deliver_once(now)->int`, `.run(stop_event)`; controller async `.handle(update_payload,now)->list[reply]`; replies `{chat_id,text,keyboard}` inline keyboard normalized data. Maintained PTB SDK, no webhook deletion, no drop pending, durable inbox before offset acknowledgement; transient handler failure leaves pending. Validate private chat/from_user not bot/sender==chat.id in controller parent; no full bot startup from worker. Callback answer safe and no direct finance. Lease-based outbox retry, redacted logging. Parent owns main.py/controller.py and runtime menu commands.

## W8 independent quality/CI (owns tests/test_acceptance*.py, .github/**, docs/RUNBOOK.md, docs/WORKER_REVIEW.md)
Initial wave create practical acceptance tests/CI/runbook against stable APIs; final independent read-only review actual returned components then tests, never overwrite sibling source. CI PostgreSQL service test database & schema env, uv lock sync, pytest, ruff, build. No secrets runtime model in CI. Report skips/blockers honestly; no synthetic invented external output.

## Coordinator owns
src/budget_bot/__init__.py, settings.py, controller.py, main.py; shared test conftest, pyproject/uv.lock/config.example.toml; docs/CONTRACTS/verification; migrations deployment; secret provisioning; worker environments/prompts/logs; final frozen suite/build/push. Each worker commit only owned files. Use shared project venv python executable supplied but set PYTHONPATH=<yourworktree>/src. Stable test schema per worker; no full suite concurrently.
