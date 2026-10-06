from copy import deepcopy
from datetime import datetime, timezone
from uuid import UUID

import pytest
from hypothesis import given, settings, strategies as st

from budget_bot.domain.errors import BudgetError
from budget_bot.domain.money import MAX_AMOUNT, format_money
from budget_bot.domain.planner import plan

NOW = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)


def snapshot(onboarded=True, pool=0, travel=0, food=0):
    return {
        "owner_id": "f46d3b1a-3d35-48c5-890c-943b95143149", "revision": 0,
        "onboarded": onboarded, "timezone": "Asia/Kolkata", "pool": pool,
        "opening_date": "2026-10-01" if onboarded else None,
        "buckets": {"Travel": {"id": "568de6ca-c573-4145-8750-e229611d5371", "balance": travel, "target": None},
                    "Food": {"id": "cf8a59c7-b0ac-4648-a067-1496f9570c05", "balance": food, "target": None}},
        "transactions": [], "targets": [],
    }


def action(kind, amount=None, **fields):
    return {"type": kind, **({"amount_inr": amount} if amount is not None else {}), **fields}


def test_ordered_setup_and_cash_effects_are_exact_pure_and_json_safe():
    state = snapshot(onboarded=False)
    actions = [action("opening", "10.01"), action("create_bucket", name=" Fun "),
               action("income", "2.00", description="Salary"),
               action("allocate", "10", bucket_name=" travel "),
               action("transfer", "1", source_bucket="TRAVEL", destination_bucket="fun"),
               action("expense", "0.01", bucket_name="Fun", description="Ticket")]
    original = deepcopy((state, actions))
    result = plan(state, actions, NOW)
    assert (state, actions) == original
    assert set(result) == {"snapshot", "postings", "events", "metadata", "warnings", "actions", "summary"}
    updated = result["snapshot"]
    assert updated["pool"] == 201
    assert updated["buckets"]["Travel"]["balance"] == 900
    assert updated["buckets"]["Fun"]["balance"] == 99
    assert updated["onboarded"] and updated["opening_date"] == "2026-10-06"
    assert updated["revision"] == 1
    assert sum(p["amount"] for p in result["postings"]) == 1200
    assert len(result["events"]) == 5
    assert len({e["transaction"]["batch_id"] for e in result["events"]}) == 1
    for event in result["events"]:
        UUID(event["transaction"]["id"])
        assert event["previous"] is None
        assert event["transaction"]["active"]
    assert result["actions"][3]["bucket_name"] == "Travel"
    assert result["metadata"][0]["type"] == "create_bucket"
    import json
    json.dumps(result)


def test_batches_validate_boundaries_and_trusted_fields_without_partial_effects():
    state = snapshot(pool=100, travel=100)
    invalid = [[], [action("income", "1", description="Pay")] * 9,
               [action("create_bucket", name="pool")],
               [action("income", "1", description="Pay", owner_id="model")],
               [{**action("income", "1", description="Pay"), "amount": 500}],
               [action("expense", "1", bucket_name="Travel", description="x", date_expression="2026-10-07")],
               [action("create_bucket", name="New"), action("allocate", "2", bucket_name="New")]]
    original = deepcopy(state)
    for actions in invalid:
        with pytest.raises(BudgetError):
            plan(state, actions, NOW)
        assert state == original
    with pytest.raises(BudgetError):
        plan(snapshot(onboarded=False), [action("income", "1", description="Pay")], NOW)


def test_expenses_overdraw_without_funding_and_warn_for_history_and_before_opening():
    state = snapshot(pool=10000, travel=50)
    result = plan(state, [action("expense", "2", bucket_name="Travel", description="Metro",
                                 date_expression="2026-09-30")], NOW)
    assert result["snapshot"]["pool"] == 10000
    assert result["snapshot"]["buckets"]["Travel"]["balance"] == -150
    text = " ".join(result["warnings"]).lower()
    assert "negative" in text and "historical" in text and "opening" in text
    assert "₹-1.50" in " ".join(result["summary"])
    with pytest.raises(BudgetError) as exc:
        plan(result["snapshot"], [action("transfer", "0.01", source_bucket="Travel",
                                         destination_bucket="Food")], NOW)
    assert exc.value.code == "insufficient_source_funds"
    assert "Travel" in exc.value.message and "₹1.51" in exc.value.message


def test_monthly_targets_version_without_moving_money_and_warn_on_active_spending():
    state = snapshot(travel=1000)
    result = plan(state, [action("set_target", "2", bucket_name="travel"),
                          action("expense", "2", bucket_name="Travel", description="Metro")], NOW)
    updated = result["snapshot"]
    assert updated["buckets"]["Travel"]["balance"] == 800
    assert updated["buckets"]["Travel"]["target"] == 200
    assert updated["targets"][0]["effective_month"] == "2026-10-01"
    assert updated["targets"][0]["amount"] == 200
    assert "target" in " ".join(result["warnings"]).lower()
    assert "₹2.00" in " ".join(result["warnings"])
    removed = plan(updated, [action("set_target", bucket_name="Travel", remove=True)], NOW)
    assert removed["snapshot"]["buckets"]["Travel"]["target"] is None
    assert len(removed["snapshot"]["targets"]) == 2
    assert not removed["postings"] and not removed["events"]
    assert removed["snapshot"]["targets"][1]["revision"] == 2
    for fields in ({"amount_inr": "0"}, {"remove": False}, {"remove": True, "amount_inr": "1"}):
        with pytest.raises(BudgetError):
            plan(state, [action("set_target", bucket_name="Travel", **fields)], NOW)


def test_correction_uses_net_effect_preserves_identity_and_reports_current_expense():
    funded = plan(snapshot(), [action("income", "10", description="Salary"),
                               action("allocate", "9", bucket_name="Travel"),
                               action("expense", "2", bucket_name="Travel", description="Metro")], NOW)["snapshot"]
    income, _, expense = funded["transactions"]
    original = deepcopy(funded)
    increased = plan(funded, [action("correct", transaction_id=income["id"],
                                     changes={"amount_inr": "12"})], NOW)
    assert increased["snapshot"]["pool"] == 300
    assert increased["postings"] == [{"account": "pool", "amount": 200, "transaction_id": income["id"]}]
    event = increased["events"][0]
    assert event["previous"] == income
    assert event["transaction"]["id"] == income["id"]
    assert event["transaction"]["revision"] == 2
    assert funded == original
    changed = plan(increased["snapshot"], [action("correct", transaction_id=expense["id"],
                                                changes={"amount_inr": "3", "bucket_name": "food",
                                                         "date_expression": "2026-09-30", "description": "Lunch"})], NOW)
    assert changed["snapshot"]["buckets"]["Travel"]["balance"] == 900
    assert changed["snapshot"]["buckets"]["Food"]["balance"] == -300
    effective = changed["snapshot"]["transactions"][2]
    assert effective["bucket"] == "Food" and effective["date"] == "2026-09-30"
    assert sum(p["amount"] for p in changed["postings"]) == -100
    with pytest.raises(BudgetError):
        plan(funded, [action("correct", transaction_id=income["id"], changes={"amount_inr": "8"})], NOW)
    with pytest.raises(BudgetError):
        plan(funded, [action("correct", transaction_id=expense["id"], changes={"type": "income"})], NOW)


def test_undo_last_reverses_current_batch_in_reverse_order_and_retains_metadata():
    created = plan(snapshot(), [action("create_bucket", name="Fun"),
                                action("income", "10", description="Pay"),
                                action("allocate", "10", bucket_name="Fun"),
                                action("expense", "3", bucket_name="Fun", description="Ticket")], NOW)["snapshot"]
    expense_id = created["transactions"][-1]["id"]
    corrected = plan(created, [action("correct", transaction_id=expense_id, changes={"amount_inr": "4"})], NOW)["snapshot"]
    metadata_only = plan(corrected, [action("set_target", "5", bucket_name="Fun")], NOW)["snapshot"]
    result = plan(metadata_only, [action("undo", last=True)], NOW)
    assert result["snapshot"]["pool"] == 0
    assert result["snapshot"]["buckets"]["Fun"]["balance"] == 0
    assert result["snapshot"]["buckets"]["Fun"]["target"] == 500
    assert [e["transaction"]["id"] for e in result["events"]] == [t["id"] for t in reversed(created["transactions"])]
    assert result["events"][0]["previous"]["amount"] == 400
    assert result["events"][0]["transaction"]["revision"] == 3
    assert all(not t["active"] for t in result["snapshot"]["transactions"])
    assert sum(p["amount"] for p in result["postings"]) == -600
    with pytest.raises(BudgetError):
        plan(result["snapshot"], [action("undo", transaction_id=expense_id)], NOW)
    with pytest.raises(BudgetError):
        plan(result["snapshot"], [action("undo", last=True)], NOW)


def test_first_target_does_not_apply_to_earlier_months():
    targeted = plan(snapshot(), [action("set_target", "1", bucket_name="Travel")], NOW)["snapshot"]
    result = plan(targeted, [action("expense", "2", bucket_name="Travel", description="Old ticket",
                                    date_expression="2026-09-30")], NOW)
    assert not any("monthly target" in warning for warning in result["warnings"])
    assert any("Historical" in warning for warning in result["warnings"])


@pytest.mark.parametrize("state,actions", [
    (snapshot(pool=2**63 - 1), [action("income", "0.01", description="Overflow")]),
    (snapshot(pool=1, travel=2**63 - 1), [action("allocate", "0.01", bucket_name="Travel")]),
    (snapshot(travel=-(2**63)), [action("expense", "0.01", bucket_name="Travel", description="Underflow")]),
])
def test_account_balance_overflow_is_rejected_without_mutating_inputs(state, actions):
    original = deepcopy((state, actions))
    with pytest.raises(BudgetError) as exc:
        plan(state, actions, NOW)
    assert exc.value.code == "invalid_amount"
    assert (state, actions) == original


@pytest.mark.parametrize("kind", [[], {}])
def test_unhashable_action_type_is_rejected_as_a_domain_error(kind):
    with pytest.raises(BudgetError) as exc:
        plan(snapshot(), [{"type": kind}], NOW)
    assert exc.value.code == "unsupported_action"


def balances(state):
    return {"pool": state["pool"], **{name: b["balance"] for name, b in state["buckets"].items()}}


def assert_postings_reconcile(before, result):
    for account, balance in balances(result["snapshot"]).items():
        assert balance == balances(before).get(account, 0) + sum(
            p["amount"] for p in result["postings"] if p["account"] == account)
    ids = {e["transaction"]["id"] for e in result["events"]}
    assert all(p["transaction_id"] in ids for p in result["postings"])


@settings(max_examples=80, derandomize=True, database=None)
@given(income=st.integers(1, MAX_AMOUNT), expense=st.integers(1, MAX_AMOUNT),
       replacement=st.integers(1, MAX_AMOUNT))
def test_ordered_multiple_revisions_then_batch_undo_preserve_audit_and_conservation(income, expense, replacement):
    initial = snapshot()
    created = plan(initial, [action("income", format_money(income)[1:], description="Pay"),
                             action("allocate", format_money(income)[1:], bucket_name="Travel"),
                             action("expense", format_money(expense)[1:], bucket_name="Travel",
                                    description="Ticket")], NOW)
    state = created["snapshot"]
    old_income, old_allocation, old_expense = deepcopy(state["transactions"])
    actions = [
        action("correct", transaction_id=old_expense["id"], changes={"amount_inr": format_money(replacement)[1:]}),
        action("correct", transaction_id=old_income["id"], changes={"description": "Corrected pay"}),
        action("correct", transaction_id=old_expense["id"], changes={"bucket_name": "Food"}),
        action("undo", batch_id=old_income["batch_id"]),
    ]
    original = deepcopy((state, actions))
    result = plan(state, actions, NOW)
    assert (state, actions) == original
    assert balances(result["snapshot"]) == balances(initial)
    assert result["snapshot"]["revision"] == state["revision"] + 1
    assert_postings_reconcile(initial, created)
    assert_postings_reconcile(state, result)
    events = result["events"]
    assert [e["transaction"]["id"] for e in events] == [
        old_expense["id"], old_income["id"], old_expense["id"],
        old_expense["id"], old_allocation["id"], old_income["id"],
    ]
    assert events[0]["previous"] == old_expense
    assert events[2]["previous"] == events[0]["transaction"]
    assert events[3]["previous"] == events[2]["transaction"]
    assert events[5]["previous"] == events[1]["transaction"]
    assert [t["revision"] for t in result["snapshot"]["transactions"]] == [3, 2, 4]
    assert all(not t["active"] for t in result["snapshot"]["transactions"])
    assert all(e["transaction"]["batch_id"] == old_income["batch_id"] for e in events)
    assert events[0]["transaction"]["bucket"] == "Travel"  # Earlier audit payload is not aliased.
    assert events[2]["transaction"]["active"]


def test_undo_last_skips_inactive_batches_and_uses_current_revisions_not_correction_order():
    first = plan(snapshot(), [action("income", "10", description="First")], NOW)["snapshot"]
    first_id = first["transactions"][0]["id"]
    second = plan(first, [action("income", "3", description="Second"),
                          action("allocate", "3", bucket_name="Travel")], NOW)["snapshot"]
    second_ids = [t["id"] for t in second["transactions"][1:]]
    revised = plan(second, [action("correct", transaction_id=first_id, changes={"amount_inr": "12"}),
                            action("correct", transaction_id=second_ids[1], changes={"amount_inr": "2"}),
                            action("set_target", "1", bucket_name="Travel")], NOW)["snapshot"]
    original = deepcopy(revised)
    undone = plan(revised, [action("undo", last=True), action("undo", last=True)], NOW)
    assert [e["transaction"]["id"] for e in undone["events"]] == [*reversed(second_ids), first_id]
    assert [e["previous"]["amount"] for e in undone["events"]] == [200, 300, 1200]
    assert balances(undone["snapshot"]) == balances(snapshot())
    assert undone["snapshot"]["targets"] == revised["targets"]
    assert revised == original
    assert_postings_reconcile(revised, undone)


@pytest.mark.parametrize("kind,initial,fields,drain,account", [
    ("opening", snapshot(onboarded=False), {}, action("allocate", "0.01", bucket_name="Travel"), "pool"),
    ("income", snapshot(), {"description": "Pay"}, action("allocate", "0.01", bucket_name="Travel"), "pool"),
    ("allocate", snapshot(pool=100), {"bucket_name": "Travel"},
     action("expense", "0.01", bucket_name="Travel", description="Spent"), "Travel"),
    ("transfer", snapshot(travel=100), {"source_bucket": "Travel", "destination_bucket": "Food"},
     action("expense", "0.01", bucket_name="Food", description="Spent"), "Food"),
])
def test_reversal_requires_exact_source_restoration_and_does_not_use_other_accounts(kind, initial, fields, drain, account):
    state = plan(initial, [action(kind, "1", **fields), drain], NOW)["snapshot"]
    tx = state["transactions"][0]
    original = deepcopy(state)
    with pytest.raises(BudgetError) as exc:
        plan(state, [action("undo", transaction_id=tx["id"])], NOW)
    assert exc.value.code == "insufficient_source_funds"
    assert exc.value.message == f"{account} has ₹0.99; restore ₹0.01 to cover ₹1.00"
    assert state == original
    # Explicitly undo the dependent spending/allocation first; no implicit funding.
    result = plan(state, [action("undo", transaction_id=state["transactions"][1]["id"]),
                           action("undo", transaction_id=tx["id"])], NOW)
    assert balances(result["snapshot"]) == balances(initial)
    assert_postings_reconcile(state, result)


@settings(max_examples=80, derandomize=True, database=None)
@given(amount=st.integers(2, 1000000), available=st.integers(-1000000, 1000000))
def test_allocation_downward_correction_checks_only_net_destination_reduction(amount, available):
    state = plan(snapshot(pool=amount), [action("allocate", format_money(amount)[1:], bucket_name="Travel")], NOW)["snapshot"]
    reduction = amount - 1
    # A subsequent expense leaves the available source amount; valid snapshots may be negative.
    if available < amount:
        state = plan(state, [action("expense", format_money(amount - available)[1:],
                                    bucket_name="Travel", description="Spent")], NOW)["snapshot"]
    old = deepcopy(state)
    correction = action("correct", transaction_id=state["transactions"][0]["id"], changes={"amount_inr": "0.01"})
    if available < reduction:
        with pytest.raises(BudgetError) as exc:
            plan(state, [correction], NOW)
        assert exc.value.code == "insufficient_source_funds"
    else:
        result = plan(state, [correction], NOW)
        assert result["snapshot"]["pool"] == reduction
        assert result["snapshot"]["buckets"]["Travel"]["balance"] == min(available, amount) - reduction
        assert_postings_reconcile(state, result)
    assert state == old


def test_transfer_correction_checks_both_changed_accounts_and_leaves_failed_batch_pure():
    state = plan(snapshot(travel=200), [action("transfer", "2", source_bucket="Travel", destination_bucket="Food"),
                                       action("expense", "1", bucket_name="Food", description="Spent")], NOW)["snapshot"]
    tx_id = state["transactions"][0]["id"]
    correction = action("correct", transaction_id=tx_id,
                        changes={"source_bucket": "Food", "destination_bucket": "Travel"})
    actions = [action("create_bucket", name="Uncommitted"),
               action("set_target", "1", bucket_name="Food"), correction]
    original = deepcopy((state, actions))
    with pytest.raises(BudgetError) as exc:
        plan(state, actions, NOW)
    assert exc.value.code == "insufficient_source_funds"
    assert exc.value.message == "Food has ₹1.00; restore ₹3.00 to cover ₹4.00"
    assert (state, actions) == original
    restored = plan(state, [action("income", "3", description="Explicit restoration"),
                            action("allocate", "3", bucket_name="Food"), correction], NOW)
    assert restored["snapshot"]["buckets"]["Food"]["balance"] == 0
    assert restored["snapshot"]["buckets"]["Travel"]["balance"] == 400
    assert_postings_reconcile(state, restored)


@pytest.mark.parametrize("selector", ["transaction_id", "batch_id"])
@pytest.mark.parametrize("alter", [str.upper, lambda value: " " + value, lambda value: value.replace("-", ""),
                                   lambda value: "a7608469-2348-423d-8c94-ae151601513f"])
def test_undo_identifiers_are_exact_owner_scoped_not_normalized(selector, alter):
    state = plan(snapshot(), [action("income", "1", description="Pay")], NOW)["snapshot"]
    tx = state["transactions"][0]
    tx["id"] = "b53b8599-6f96-4b90-9935-f7f757513acd"
    tx["batch_id"] = "9943750e-97a7-4870-9c8d-a6a59631fd5a"
    identifier = tx["id" if selector == "transaction_id" else "batch_id"]
    original = deepcopy(state)
    with pytest.raises(BudgetError) as exc:
        plan(state, [action("undo", **{selector: alter(identifier)})], NOW)
    assert exc.value.code == ("unknown_transaction" if selector == "transaction_id" else "nothing_to_undo")
    assert state == original


def test_exact_generated_ids_connect_snapshot_events_postings_and_metadata(monkeypatch):
    from budget_bot.domain import planner

    ids = [str(UUID(int=i)) for i in range(1, 5)]
    generated = iter(UUID(value) for value in ids)
    monkeypatch.setattr(planner, "uuid4", lambda: next(generated))
    state = snapshot()
    actions = [action("create_bucket", name="Fun"), action("income", "1", description="Pay"),
               action("set_target", "1", bucket_name="Fun")]
    original = deepcopy((state, actions))
    result = plan(state, actions, NOW)
    tx = result["snapshot"]["transactions"][0]
    assert tx["batch_id"] == ids[0] and tx["id"] == ids[2]
    assert result["snapshot"]["buckets"]["Fun"]["id"] == ids[1]
    assert result["snapshot"]["targets"][0]["id"] == ids[3]
    assert result["metadata"][0]["bucket"]["id"] == ids[1]
    assert result["metadata"][1]["bucket_id"] == ids[1]
    assert result["metadata"][1]["id"] == ids[3]
    assert result["events"][0]["transaction"] == tx
    assert result["postings"][0]["transaction_id"] == ids[2]
    result["snapshot"]["transactions"][0]["description"] = "Mutated output"
    result["snapshot"]["targets"][0]["amount"] = 999
    result["actions"][0]["name"] = "Mutated output"
    assert result["events"][0]["transaction"]["description"] == "Pay"
    assert result["metadata"][1]["amount"] == 100
    assert (state, actions) == original


@pytest.mark.parametrize("expense_date,target", [
    ("2026-07-31", None), ("2026-08-31", 200), ("2026-09-30", None), ("2026-10-06", 300),
])
def test_target_history_selects_month_then_latest_revision_and_respects_removal(expense_date, target):
    august = datetime(2026, 8, 1, tzinfo=timezone.utc)
    september = datetime(2026, 9, 1, tzinfo=timezone.utc)
    state = plan(snapshot(), [action("set_target", "1", bucket_name="Travel"),
                              action("set_target", "2", bucket_name="Travel")], august)["snapshot"]
    state = plan(state, [action("set_target", bucket_name="Travel", remove=True)], september)["snapshot"]
    state = plan(state, [action("set_target", "3", bucket_name="Travel")], NOW)["snapshot"]
    assert [v["revision"] for v in state["targets"]] == [1, 2, 1, 1]
    assert [v["amount"] for v in state["targets"]] == [100, 200, None, 300]
    # Selection must not rely on incoming history list order.
    state["targets"].reverse()
    original = deepcopy(state)
    result = plan(state, [action("expense", "4", bucket_name="Travel", description="Ticket",
                                 date_expression=expense_date)], NOW)
    warnings = [w for w in result["warnings"] if "monthly target" in w]
    assert warnings == ([] if target is None else [
        f"Travel monthly target {format_money(target)} reached or exceeded in {expense_date[:7]}: ₹4.00 spent"])
    assert state == original
    assert result["snapshot"]["targets"] == state["targets"]


def test_target_feedback_counts_only_current_active_expenses_after_moves_and_undo():
    state = plan(snapshot(), [action("set_target", "2", bucket_name="Travel"),
                              action("set_target", "1", bucket_name="Food"),
                              action("expense", "2", bucket_name="Travel", description="Ticket")], NOW)["snapshot"]
    tx_id = state["transactions"][0]["id"]
    moved = plan(state, [action("correct", transaction_id=tx_id,
                                changes={"bucket_name": "Food", "amount_inr": "1"})], NOW)
    assert [w for w in moved["warnings"] if "monthly target" in w] == [
        "Food monthly target ₹1.00 reached or exceeded in 2026-10: ₹1.00 spent"]
    undone = plan(moved["snapshot"], [action("undo", transaction_id=tx_id)], NOW)
    assert not any("monthly target" in w for w in undone["warnings"])
    assert balances(undone["snapshot"]) == balances(snapshot())


@pytest.mark.parametrize("zone,today,yesterday", [
    ("Asia/Kolkata", "2026-02-01", "2026-01-31"),
    ("America/Los_Angeles", "2026-01-31", "2026-01-30"),
])
def test_expense_dates_and_target_month_use_snapshot_timezone(zone, today, yesterday):
    received = datetime(2026, 1, 31, 20, tzinfo=timezone.utc)
    state = snapshot(pool=500)
    state["timezone"] = zone
    state["opening_date"] = "2026-01-01"
    current = plan(state, [action("set_target", "1", bucket_name="Travel"),
                           action("expense", "1", bucket_name="Travel", description="Today")], received)
    assert current["snapshot"]["transactions"][0]["date"] == today
    assert current["snapshot"]["targets"][0]["effective_month"] == today[:7] + "-01"
    assert not any("Historical" in w for w in current["warnings"])
    old = plan(state, [action("expense", "1", bucket_name="Travel", description="Yesterday",
                              date_expression="yesterday")], received)
    assert old["snapshot"]["transactions"][0]["date"] == yesterday
    assert old["snapshot"]["pool"] == 500
    assert old["snapshot"]["buckets"]["Travel"]["balance"] == -100
    assert any("Historical" in w for w in old["warnings"])
    if zone == "America/Los_Angeles":
        with pytest.raises(BudgetError) as exc:
            plan(state, [action("expense", "1", bucket_name="Travel", description="Future locally",
                                date_expression="2026-02-01")], received)
        assert exc.value.code == "future_expense"


@pytest.mark.parametrize("operation", ["undo", "correct"])
def test_expense_revision_cannot_overflow_balance_and_remains_pure(operation):
    state = plan(snapshot(travel=2**63 - 1), [action("expense", "0.02", bucket_name="Travel", description="Ticket"),
                                             action("income", "0.02", description="Pay"),
                                             action("allocate", "0.02", bucket_name="Travel")], NOW)["snapshot"]
    tx_id = state["transactions"][0]["id"]
    original = deepcopy(state)
    fields = {"changes": {"amount_inr": "0.01"}} if operation == "correct" else {}
    with pytest.raises(BudgetError) as exc:
        plan(state, [action(operation, transaction_id=tx_id, **fields)], NOW)
    assert exc.value.code == "invalid_amount"
    assert state == original


def test_supported_balance_boundaries_and_aggregate_above_per_action_limit_are_exact():
    upper = plan(snapshot(pool=2**63 - 2), [action("income", "0.01", description="Boundary")], NOW)
    assert upper["snapshot"]["pool"] == 2**63 - 1
    lower = plan(snapshot(travel=-(2**63) + 1), [action("expense", "0.01", bucket_name="Travel",
                                                      description="Boundary")], NOW)
    assert lower["snapshot"]["buckets"]["Travel"]["balance"] == -(2**63)
    aggregate = plan(snapshot(pool=MAX_AMOUNT), [action("income", "999999999.99", description="Pay")], NOW)
    assert aggregate["snapshot"]["pool"] == 2 * MAX_AMOUNT


def test_repeated_undo_or_correction_after_undo_rejects_whole_ordered_batch():
    state = plan(snapshot(), [action("income", "1", description="Pay")], NOW)["snapshot"]
    tx_id = state["transactions"][0]["id"]
    for last in (action("undo", transaction_id=tx_id),
                 action("correct", transaction_id=tx_id, changes={"amount_inr": "2"})):
        actions = [action("undo", transaction_id=tx_id), last]
        original = deepcopy((state, actions))
        with pytest.raises(BudgetError) as exc:
            plan(state, actions, NOW)
        assert exc.value.code == "already_undone"
        assert (state, actions) == original


def test_target_fallback_is_bucket_scoped_when_only_another_bucket_has_history():
    state = snapshot()
    state["buckets"]["Travel"]["target"] = 100
    result = plan(state, [action("set_target", "3", bucket_name="Food"),
                          action("expense", "1", bucket_name="Travel", description="Ticket")], NOW)
    assert any("Travel monthly target ₹1.00" in w for w in result["warnings"])


def test_eight_ordered_revisions_preserve_every_intermediate_event():
    state = plan(snapshot(), [action("income", "1", description="Pay")], NOW)["snapshot"]
    original = deepcopy(state)
    tx = state["transactions"][0]
    actions = [action("correct", transaction_id=tx["id"], changes={"amount_inr": str(n)}) for n in range(2, 10)]
    result = plan(state, actions, NOW)
    assert result["snapshot"]["pool"] == 900
    assert len(result["events"]) == 8
    previous = tx
    for event in result["events"]:
        assert event["previous"] == previous
        assert event["transaction"]["revision"] == previous["revision"] + 1
        assert event["transaction"]["id"] == tx["id"]
        previous = event["transaction"]
    assert_postings_reconcile(state, result)
    assert state == original


def test_correction_rejects_foreign_id_and_future_date_without_changing_inputs():
    state = plan(snapshot(), [action("expense", "1", bucket_name="Travel", description="Ticket")], NOW)["snapshot"]
    for identifier, changes, code in [
        ("a7608469-2348-423d-8c94-ae151601513f", {"amount_inr": "2"}, "unknown_transaction"),
        (state["transactions"][0]["id"], {"date_expression": "2026-10-07"}, "future_expense"),
    ]:
        actions = [action("correct", transaction_id=identifier, changes=changes)]
        original = deepcopy((state, actions))
        with pytest.raises(BudgetError) as exc:
            plan(state, actions, NOW)
        assert exc.value.code == code
        assert (state, actions) == original
