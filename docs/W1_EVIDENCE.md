# W1 domain hardening evidence

## Scope and starting point

Owned changes only: `src/budget_bot/domain/**`, `tests/test_domain*.py`, and this file. Read REQUIREMENTS.md and CONTRACTS.md; consulted LLD money, target-history, and reversal rules. Initial `git status --short` was empty, branch `worker/w1`; no saved untracked files were present to discard or claim as new work. Existing source/tests were read and exercised before editing.

No dependency installation, database access, full-suite execution, provider/Telegram calls, shared configuration edits, or sibling source edits occurred.

## Reproduced defects and minimal fixes

1. Historical target leakage: the first October target incorrectly warned on September expenses because lookup fell back to the current bucket target when no historical version qualified. Lookup now distinguishes no history from history that starts after the expense month. Existing bucket-only fallback is retained when that bucket has no versions.
2. Account arithmetic accepted values outside signed BIGINT storage bounds. The shared posting application path now checks the resulting account balance for financial actions and revisions alike. This implements existing LLD checked-arithmetic rules, not a new per-action cap. Expenses still may overdraw; aggregate balances may exceed the per-action input limit within BIGINT bounds.
3. Long leading-zero amounts passed format/range checks but leaked Python's integer digit-limit ValueError. Strip already-accepted padding before integer conversion; preserve exact existing amount grammar and range.
4. Calendar-limit timezone conversion and `yesterday` underflow leaked OverflowError. They now return BudgetError with `invalid_date`.
5. List/dict action types leaked TypeError from dictionary membership. Reject non-string action types with the existing `unsupported_action` domain error.

TDD execution (all commands used the project venv executable below):

- Baseline: `-m pytest tests/test_domain_planner.py tests/test_domain_money.py tests/test_domain_dates.py -q` -> 25 passed in 1.49s.
- First red: `-m pytest tests/test_domain_planner.py -q -k 'first_target or account_balance_overflow'` -> 4 failed, 6 deselected. Historical warning assertion failed; all three overflow cases failed to raise BudgetError.
- Minimal fixes, then same domain suite -> 29 passed in 1.42s; scoped Ruff and diff check passed.
- Second red: `-m pytest tests/test_domain_planner.py tests/test_domain_money.py tests/test_domain_dates.py -q -k 'leading_zero or supported_calendar or outside_calendar or unhashable'` -> 7 failed, 29 deselected. Exact failures were integer digit-limit ValueError (2), calendar OverflowError (3), unhashable action TypeError (2).
- Minimal fixes, then domain suite -> 36 passed in 0.99s; scoped Ruff and diff check passed.
- Added invariant coverage without changing passing production behavior; final verification below.

## Previously uncovered behavior verified, not rewritten

- Ordered repeated corrections to the same logical transaction, interleaved corrections to another transaction, and reverse-order batch undo. Every intermediate event retains exact prior/current revision content; current IDs and originating batch IDs remain stable.
- Eight ordered revisions in one allowed batch, with postings reconciled against projected balances.
- Undo last skips inactive/metadata-only batches and uses current effective versions even after later corrections to an older originating batch. Repeated undo or correction after undo rejects the whole plan.
- Opening/income/allocation/transfer reversal source shortages identify the precise account and required restoration; undoing dependencies explicitly allows reversal. No implicit funding.
- Allocation downward correction validates the net destination reduction, including negative destination balances. Transfer direction correction validates its full net debit and preserves failed-batch purity, including staged metadata.
- Expense correction may overdraw the new bucket, while undo credits the current effective expense bucket. Bounds apply to undo/correction as well as initial actions.
- Exact ID linkage among generated metadata, snapshots, events, and postings. Uppercased, padded, compacted, and foreign selectors do not resolve. Foreign correction IDs also fail. Input and audit payloads are not aliased by output mutation.
- Same-month target revisions, removal and re-enable across months, unsorted history, bucket-scoped fallback, and current active expense aggregation after correction/undo.
- Snapshot-local expense dates and target months across UTC/local month boundaries; future local expenses and future corrections rejected; historical expenses debit current funds and warn.
- Exact per-action money boundary/grouping and valid aggregate BIGINT endpoints.

## Final executed verification

Working directory: `C:/Users/FL_LPT-657/Projects/telegram_bot/.worktrees/w1`.

Environment set before baseline and retained for subsequent runs:

```sh
export PYTHONPATH='C:/Users/FL_LPT-657/Projects/telegram_bot/.worktrees/w1/src'
```

Final commands:

```sh
'C:/Users/FL_LPT-657/Projects/telegram_bot/.venv/Scripts/python.exe' -m pytest tests/test_domain_planner.py tests/test_domain_money.py tests/test_domain_dates.py -q --hypothesis-show-statistics
'C:/Users/FL_LPT-657/Projects/telegram_bot/.venv/Scripts/python.exe' -m ruff check src/budget_bot/domain tests/test_domain_planner.py tests/test_domain_money.py tests/test_domain_dates.py
git diff --check
```

Actual results: 72 passed in 1.15s; Ruff `All checks passed!`; diff check exited 0 without output. No skipped tests.

Hypothesis statistics: ordered revision/undo conservation 80 passing cases; allocation net-source property 80 passing cases; exact amount round-trip 100 passing cases. All had zero failing and zero invalid cases. Properties use deterministic generation and no Hypothesis example database. These cases are exercised within the 72 pytest tests, not additional pytest test counts.

## Remaining integration gaps / limits

- Domain-only execution does not verify storage confirmation, ownership authentication, persistence/replay, or reports. Snapshot ownership and current-transaction ordering are trusted inputs to this API; domain tests verify lookups stay inside the supplied snapshot, not database isolation.
- Read-only inspection of this worktree's `storage/store.py:424-443` shows target persistence currently regenerates target IDs and numbers revisions across all months, whereas the planner emits exact target metadata IDs with bucket/month revision numbering. Coordinator/W2 should reconcile that persistence path with reviewed metadata and test reload identity/history. No storage code was changed or DB test run here.
- Plan calls intentionally assign fresh UUIDs; purity here means no input mutation or I/O, not identical UUIDs on independent calls. Storage must persist the reviewed plan IDs rather than replace them on confirmation.
- No claim about full-suite integration, deployment, live service operation, or pricing.
