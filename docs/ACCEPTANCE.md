# Acceptance matrix

**Status: design coverage only. Every implementation gate below is NOT RUN.** Product scope and HLD D01–D09 technical/policy defaults are approved for implementation; setup inputs and D10 operational decisions remain open. This matrix states how completion will be observed; it is not application code or evidence of passing tests.

| Requirement | Observable behavior | Required verification / boundary | Status |
|---|---|---|---|
| R01 — isolation | Users cannot read, alter, select, or resume another user's money/history/workflows; groups rejected | Two-user real DB/repository tests; cross-owner FK failures; forged callbacks; later live second-user walkthrough | NOT RUN |
| R02 — access | Single-use invite grants one registration; expired/revoked/duplicate redemption fails; admin has access-management only | Concurrent real DB redemption; trusted admin checks; unauthorized/admin financial route negatives | NOT RUN |
| R03 — setup | One-question-at-a-time setup, Back/Cancel, labeled final review; no money before Confirm; calendar offered afterward | Guided-flow transport/graph tests; real DB cancel/restart/duplicate Confirm and one-opening-per-user constraint; zero-opening setup; later owner walkthrough | NOT RUN |
| R04 — money | Income enters pool; allocation moves existing money; exact paise; tracked total matches ledger | Pure money edge/property tests; real DB posting/balance invariant after opening, income and allocation | NOT RUN |
| R05 — buckets | Create owner-unique buckets; no rename/archive/delete routes or silent defaults | Duplicate/case/length validation; owner scope; unsupported NL/command negatives | NOT RUN |
| R06 — commands/NL | Every supported budgeting action has command/button and English NL route through same domain contracts | Real graph with controlled provider; command-to-proposal parity; later synthetic provider and live Telegram checks | NOT RUN |
| R07 — review | Command and NL changes cannot commit before exact Confirm; Edit rebuilds; Cancel leaves financial state unchanged | Real DB stale/forged/expired/replayed confirmation; Confirm-versus-Cancel/expiry/revocation races; real graph review transitions | NOT RUN |
| R08 — expense | Selected bucket is debited once; reply reports committed remaining balance | End-to-end controlled transport through real service/DB and final renderer; live fictional expense later | NOT RUN |
| R09 — overspending | Confirmed expense may make bucket/total negative; warning; no implicit other-bucket deduction | Real DB negative expense and conservation tests; review/result formatting | NOT RUN |
| R10 — bucket resolution | Omitted bucket gets suggestion; ambiguity/no fit prompts choice/create; no unauthorized auto-creation | Adversarial/ambiguous schemas, staged bucket creation, combined rollback; graph clarification paths | NOT RUN |
| R11 — funding ambiguity | 'Add to Travel' asks existing pool versus new income; new income+allocation is atomic | Real graph ambiguity contract; source-fund/total invariants; combined commit rollback | NOT RUN |
| R12 — source funds | Short allocation/transfer is blocked with exact shortage and no partial state change | Real DB concurrent source spend, transfer invariant, same-source/destination negatives | NOT RUN |
| R13 — corrections | Current effective revision corrected/undone with preserved original history; unfunded reversal blocked | Real DB net reversal, income decrease/increase, transfer reversal, expense rebucket/date correction, replay and audit immutability | NOT RUN |
| R14 — batches | Single reviewed ordered plan; all actions commit once or every change rolls back | Inject failure mid-batch using real DB; new bucket/target rollback; concurrency/replayed callbacks; last-batch undo policy if approved | NOT RUN |
| R15 — carry-forward | Month transition changes neither pool nor bucket money | Frozen clocks across month/year/leap boundary; ledger balances unchanged; report ranges differ correctly | NOT RUN |
| R16 — targets | Optional monthly target warns at/above threshold, not a money source or spending block | Real aggregates with backdated/corrected/undone expenses; target set/remove timeline selection, crossing/repeated warning policy if approved | NOT RUN |
| R17 — dates | Relative date anchored to message/timezone; future expenses rejected; India default changeable | Fixed receipt-time/delayed-worker tests, ambiguous/invalid dates, timezone change, leap day; historical pre-opening expense review warning | NOT RUN |
| R18 — reports | Day/week/month/range sums by bucket; transfers/income excluded; active corrected expenses used | Real DB aggregates + actual serializers/renderer; total equals sum; inclusive range/empty/filter semantics | NOT RUN |
| R19 — calendar | Month navigation and day tap reveal correct owner-scoped active expenses and bucket totals; no mutation | Generated grid leap/year boundaries; stale/forged navigation tokens; pagination/empty-day; post-setup/live UI walkthrough | NOT RUN |
| R20 — AI outage | Commands/buttons, reviews, balances, reports and calendar still work; NL fails clearly | Provider timeout/refusal/invalid output; deterministic actions and results through real service while provider disabled | NOT RUN |
| R21 — AI boundary | Minimal context, no identity/unrelated history/secrets; model cannot authorize/write/calculate authoritative facts | Captured real-SDK controlled wire; redaction preserves money/date digits; prompt-injection/cross-user tests; approved phrase/fact selection, mandatory-fact coverage and invalid-selection fallback | NOT RUN |
| R22 — provider reuse | Exact configured endpoint/model and referenced credential used without exposing/changing Hermes | Local key presence verified only; later restricted config constructor, source-config fingerprint, secret exclusion and bounded authorized synthetic live schemas | DISCOVERY ONLY; INTEGRATION NOT RUN |
| Cross-cutting — replay/recovery | Single financial application survives duplicate updates/resumes/crash; old answer does not answer new prompt | Real PostgreSQL/checkpoint fault injection: commit before checkpoint, answer before resume, outbox revision race, inbox offset sequence and lease fencing | NOT RUN |
| Cross-cutting — operational | Runnable local instructions, locked dependencies, artifact identity, source-test evidence, no embedded keys | Frozen-tree full suite/JUnit, lint, build, artifact scan; later backup restore/live/deployment gates | NOT RUN |

## Evidence levels

- **Document checks:** links, requirement IDs, example arithmetic, citation mappings and design consistency. These do not test application behavior.
- **Controlled adapters:** tests simulate Telegram/provider responses and exercise real domain/graph wiring. Not evidence of real service acceptance or language quality.
- **Real PostgreSQL:** isolated approved app-owned schema tests establish the exercised constraints/atomicity/locking. SQLite or in-memory state cannot substitute for these claims.
- **Synthetic live provider:** real configured endpoint with fictional messages, after approval. Distinct interpretation and rendering gates.
- **Live Telegram:** actual invite, setup, commands, natural language, review, calendar and restart walkthrough after owner-controlled credentials/setup.
- **Operational readiness:** approved hosting/backup/retention/cost decisions and demonstrated restore. Separate from code completion and bot-token validity.

## Current outcome

Requirements have been restated and HLD/LLD drafted. No bot application, database tables, dependencies, live provider calls, or live Telegram tests have been created/run in this stage. See [DESIGN_CHECKS.md](DESIGN_CHECKS.md) for actual document validation only.
