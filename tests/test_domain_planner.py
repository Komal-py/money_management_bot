from copy import deepcopy
from datetime import datetime, timezone
from uuid import UUID

import pytest

from budget_bot.domain.errors import BudgetError
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
