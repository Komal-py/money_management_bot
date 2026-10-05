# Telegram budget bot — approved requirements

## Status and authority

**Product scope, HLD/LLD defaults and implementation approved; setup inputs pending.** The owner also approved project-only app/test database provisioning on the existing server, eight separately routed Hermes workers and bounded synthetic API use. GitHub target is `https://github.com/Komal-py/money_management_bot.git`, branch `main`. Hosting and operational decisions remain separate.

This is a consolidated restatement of the conversation, not a verbatim transcript. Explicit user decisions take precedence over assistant recommendations. Approved technical defaults and still-unresolved operational decisions appear in [HLD.md](HLD.md#review-decisions); later explicit approval of D01–D09 supersedes their earlier proposal status.

The bot tracks manually entered virtual money. It does not transfer actual money, connect to banks, or claim to know a bank-verified balance.

## Requirements

| ID | Approved requirement |
|---|---|
| R01 | Multiple registered users, each with completely separate money, buckets, transactions, and pending interactions. |
| R02 | Invite-only registration using an administrator-generated, single-use invite code. Administrator features manage access, not other users' financial records. The database operator can technically access stored records; bot isolation is not protection from the host operator. |
| R03 | Guided initial setup: starting available money, user-created buckets, initial allocations, and explicit review before financial writes. |
| R04 | INR only. Maintain an unallocated available-money pool and virtual bucket balances. New income enters the pool; allocation distributes existing pool money. |
| R05 | Create user-named buckets. Bucket renaming, archiving, restoring, and deletion are excluded from version one. |
| R06 | Support English natural language and commands/buttons for all supported user budgeting actions. LangGraph orchestrates interpretation, clarification, confirmation, and conversational output. |
| R07 | Show Confirm / Edit / Cancel before every balance-changing action, including explicit commands. |
| R08 | Record expenses with amount, description, bucket, and date; deduct from the selected bucket and report its remaining balance. |
| R09 | When an expense exceeds its bucket's funds, record it after confirmation, allow a negative balance, and warn the user. Do not silently fund it from another bucket. |
| R10 | If a bucket is omitted, suggest a suitable existing bucket for confirmation. If no bucket fits, offer creation or selection; never create an unspecified default bucket automatically. |
| R11 | For ambiguous requests such as 'add money to Travel', ask whether this means allocation from existing pool money or new incoming money. New money allocated immediately to a bucket must be represented as income plus allocation, not invented as an internal transfer. |
| R12 | Support pool-to-bucket allocations and bucket-to-bucket transfers. Block an allocation or transfer exceeding its source's available funds and explain the shortage. |
| R13 | Support undo and correction of recorded transactions with preserved audit history. Block reversals/corrections that need funds no longer available and explain what must be restored. |
| R14 | A single message can contain multiple actions. Show one combined review and apply all actions or none. |
| R15 | Balances carry forward across months; monthly boundaries only affect reporting and targets, not funds. |
| R16 | Optional monthly spending target per bucket. Warn when monthly spending reaches or exceeds the target; the target does not block expenses or create/reset money. |
| R17 | Support expenses dated today or in the past, defaulting to today when omitted; reject future expense dates. Default timezone Asia/Kolkata, changeable per user. |
| R18 | Text-only spending summaries, broken down by bucket, for a chosen day, week, month, or date range. Corrected expenses use their current amount/date/bucket; undone expenses are excluded, while audit records remain. |
| R19 | After guided setup, expose a monthly calendar. Tapping a day shows recorded expenses and spending by bucket. The calendar is a view, not a spending schedule or reminder feature. |
| R20 | Commands/buttons, confirmations, and basic reports continue to work if the AI service is unavailable. Explain that natural-language interpretation is unavailable. |
| R21 | Hosted AI may receive the user's message and the minimum relevant bucket context. Exclude Telegram identity and unrelated history. Backend logic, not the model, validates mutations and calculates financial/report facts. |
| R22 | Reuse the AI provider, model, and API credential used by the active Hermes configuration. Do not expose the credential or change Hermes's working settings. The stated PDF attachment was not available during preparation. |

## Version-one exclusions

- Bank/payment connections or actual money movement.
- Loans, refunds, recurring transactions, and reminders.
- Voice/photo input, charts, and CSV exports.
- Shared family/group budgets; multi-currency support.
- Bucket renaming, archiving, restoring, and deletion.
- Separate web/Android UI or Telegram Mini App.
- Arbitrary financial advice or unrestricted question answering outside supported budgeting/report actions.

Balance feedback and a basic balance overview support normal budgeting. A standalone transaction-history search product, month-over-month trend reports, and exports were not selected; transaction selection for corrections and day-level calendar details remain included.

## Walkthroughs

### Expense

User: 'I've spent 400 on metro, travel.'

1. Interpret an expense proposal; resolve Travel only among this user's buckets.
2. Show a labeled review: expense, INR amount, description, bucket, local date/timezone, projected remaining balance, and applicable warnings.
3. Confirm records it once. Edit rebuilds the proposal and review. Cancel records no financial action.
4. Reply with the committed remaining balance in conversational English.

### Ambiguous funding

User: 'Add 2,000 to Travel.'

Ask whether this is existing pool money or new income. If new income, review income into the pool followed by allocation into Travel as a single atomic batch.

### Combined actions

User requests income, allocation, and an expense in one message. Clarify any missing fields first, then show the ordered actions and their combined effects. No partial application is allowed.

### Calendar

Open Calendar, move between months, and tap a date. Show that day's active expense records and per-bucket spending, including a clear zero-spending result for an empty day. Calendar navigation changes no money.

## Next checkpoint

Review [HLD.md](HLD.md), [LLD.md](LLD.md), and the [acceptance matrix](ACCEPTANCE.md). Subsequent explicit owner approval authorizes the listed project-only database resources, configured-provider workers and bounded synthetic bot API checks. Connection/local secret entry must still be completed and verified; unrelated access, secret publication and cloud deployment remain excluded.
