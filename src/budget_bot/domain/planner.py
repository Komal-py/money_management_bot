"""Pure, ordered financial planning. Storage persists the exact returned plan."""

from copy import deepcopy
from uuid import uuid4

from .dates import local_today, resolve_date
from .errors import BudgetError
from .money import format_money, parse_money

FINANCIAL = {"opening", "income", "allocate", "transfer", "expense"}
FIELDS = {
    "opening": {"amount_inr", "date_expression", "description"},
    "income": {"amount_inr", "date_expression", "description"},
    "allocate": {"amount_inr", "bucket_name", "date_expression", "description"},
    "transfer": {"amount_inr", "source_bucket", "destination_bucket", "date_expression", "description"},
    "expense": {"amount_inr", "bucket_name", "date_expression", "description"},
    "create_bucket": {"name"},
    "set_target": {"bucket_name", "amount_inr", "remove"},
    "undo": {"transaction_id", "batch_id", "last"},
    "correct": {"transaction_id", "changes"},
}


def _name(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 60:
        raise BudgetError("invalid_bucket", "Bucket names must contain 1–60 characters")
    return value.strip()


def _bucket(state, value):
    name = _name(value)
    for canonical in state["buckets"]:
        if canonical.casefold() == name.casefold():
            return canonical
    raise BudgetError("unknown_bucket", "Select an existing bucket")


def _description(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 240:
        raise BudgetError("invalid_description", "Description must contain 1–240 characters")
    return value.strip()


def _effects(tx):
    amount, kind = tx["amount"], tx["type"]
    if kind in {"opening", "income"}:
        return {"pool": amount}
    if kind == "expense":
        return {tx["bucket"]: -amount}
    return {tx["source"]: -amount, tx["destination"]: amount}


def _balance(state, account):
    return state["pool"] if account == "pool" else state["buckets"][account]["balance"]


def _require_funds(state, account, reduction):
    available = _balance(state, account)
    if available < reduction:
        raise BudgetError("insufficient_source_funds",
                          f"{account} has {format_money(available)}; restore "
                          f"{format_money(reduction - available)} to cover {format_money(reduction)}")


def _expense_feedback(state, tx, received_at, result):
    if tx["type"] != "expense" or not tx["active"]:
        return
    balance = _balance(state, tx["bucket"])
    result["summary"].append(f"{tx['bucket']} remaining: {format_money(balance)}")
    if balance < 0:
        result["warnings"].append(f"{tx['bucket']} has a negative balance of {format_money(balance)}; no implicit funding")
    if tx["date"] < local_today(received_at, state["timezone"]).isoformat():
        result["warnings"].append("Historical expense deducts current money, not a past balance")
    if state.get("opening_date") and tx["date"] < state["opening_date"]:
        result["warnings"].append("Expense is before opening; do not re-enter spending already included in opening money")
    _target_feedback(state, tx["bucket"], tx["date"][:7] + "-01", result)


def _target_feedback(state, name, month, result):
    history = [v for v in state.get("targets", []) if v["bucket_name"] == name]
    versions = [v for v in history if v["effective_month"] <= month]
    target = (max(versions, key=lambda v: (v["effective_month"], v["revision"]))["amount"]
              if versions else None if history else state["buckets"][name].get("target"))
    if target is None:
        return
    spending = sum(t["amount"] for t in state["transactions"]
                   if t["active"] and t["type"] == "expense" and t["bucket"] == name
                   and t["date"][:7] == month[:7])
    if spending >= target:
        warning = f"{name} monthly target {format_money(target)} reached or exceeded in {month[:7]}: {format_money(spending)} spent"
        if warning not in result["warnings"]:
            result["warnings"].append(warning)


def _set_target(state, action, received_at, result):
    name = _bucket(state, action.get("bucket_name"))
    remove = action.get("remove", False)
    if not isinstance(remove, bool) or (remove and "amount_inr" in action):
        raise BudgetError("invalid_target", "Specify a positive target or remove=true, not both")
    amount = None if remove else parse_money(action.get("amount_inr"))
    month = local_today(received_at, state["timezone"]).replace(day=1).isoformat()
    versions = state.setdefault("targets", [])
    revision = 1 + max((v["revision"] for v in versions
                        if v["bucket_name"] == name and v["effective_month"] == month), default=0)
    version = {"id": str(uuid4()), "bucket_name": name, "bucket_id": state["buckets"][name]["id"],
               "effective_month": month, "amount": amount, "revision": revision}
    versions.append(version)
    state["buckets"][name]["target"] = amount
    result["metadata"].append({"type": "set_target", **deepcopy(version)})
    result["actions"].append({"type": "set_target", "bucket_name": name,
                              **({"remove": True} if remove else {"amount_inr": format_money(amount)[1:]})})
    result["summary"].append(f"{name} monthly target: {'off' if remove else format_money(amount)} from {month}")
    _target_feedback(state, name, month, result)


def _apply(state, effects, transaction_id, postings):
    for account, delta in effects.items():
        balance = _balance(state, account) + delta
        if not -(2**63) <= balance <= 2**63 - 1:
            raise BudgetError("invalid_amount", "Account balance exceeds supported storage range")
        if account == "pool":
            state["pool"] = balance
        else:
            state["buckets"][account]["balance"] = balance
        if delta:
            postings.append({"account": account, "amount": delta, "transaction_id": transaction_id})


def _financial(state, action, received_at, batch_id, check_source=True):
    kind = action["type"]
    canonical = deepcopy(action)
    amount = parse_money(action.get("amount_inr"), allow_zero=kind == "opening")
    canonical["amount_inr"] = format_money(amount)[1:]
    day = resolve_date(action.get("date_expression"), received_at, state["timezone"]).isoformat()
    if kind == "expense" and day > local_today(received_at, state["timezone"]).isoformat():
        raise BudgetError("future_expense", "Expenses cannot be dated in the future")
    canonical["date_expression"] = day
    description = _description(action.get("description")) if kind in {"income", "expense"} else ""
    if description:
        canonical["description"] = description
    tx = {"id": str(uuid4()), "batch_id": batch_id, "type": kind, "amount": amount,
          "bucket": None, "source": None, "destination": None, "description": description,
          "date": day, "active": True, "revision": 1}
    if kind == "opening":
        if state["onboarded"]:
            raise BudgetError("already_onboarded", "Opening money can only be recorded once")
        state["onboarded"] = True
        state["opening_date"] = day
    elif kind in {"allocate", "expense"}:
        name = _bucket(state, action.get("bucket_name"))
        canonical["bucket_name"] = name
        tx["bucket"] = name
        if kind == "allocate":
            tx["source"], tx["destination"] = "pool", name
    elif kind == "transfer":
        source = _bucket(state, action.get("source_bucket"))
        destination = _bucket(state, action.get("destination_bucket"))
        if source == destination:
            raise BudgetError("same_bucket", "Transfer requires two different buckets")
        canonical["source_bucket"], canonical["destination_bucket"] = source, destination
        tx["source"], tx["destination"] = source, destination
    if check_source and kind in {"allocate", "transfer"}:
        _require_funds(state, tx["source"], amount)
    return canonical, tx


def _transaction(state, identifier):
    if not isinstance(identifier, str):
        raise BudgetError("invalid_transaction", "Select a transaction")
    for tx in state["transactions"]:
        if tx["id"] == identifier:
            if not tx["active"]:
                raise BudgetError("already_undone", "This transaction is already undone")
            return tx
    raise BudgetError("unknown_transaction", "Transaction is not in this owner's records")


def _revise(state, old, new, result):
    before = _effects(old)
    after = _effects(new) if new["active"] else {}
    net = {account: after.get(account, 0) - before.get(account, 0)
           for account in dict.fromkeys([*before, *after])}
    for account, delta in net.items():
        if delta < 0 and old["type"] != "expense":
            _require_funds(state, account, -delta)
    _apply(state, net, old["id"], result["postings"])
    new["id"], new["batch_id"], new["revision"] = old["id"], old["batch_id"], old["revision"] + 1
    index = state["transactions"].index(old)
    state["transactions"][index] = new
    result["events"].append({"transaction": deepcopy(new), "previous": deepcopy(old)})


def _correct(state, action, received_at, batch_id, result):
    old = _transaction(state, action.get("transaction_id"))
    changes = action.get("changes")
    if not isinstance(changes, dict) or not changes or set(changes) - FIELDS[old["type"]]:
        raise BudgetError("invalid_correction", "Specify supported replacement fields without changing type")
    if old["type"] == "opening":
        raise BudgetError("invalid_correction", "Opening money cannot be corrected; use an explicit income adjustment")
    replacement = {"type": old["type"], "amount_inr": format_money(old["amount"])[1:],
                   "description": old["description"], "date_expression": old["date"]}
    if old["type"] in {"allocate", "expense"}:
        replacement["bucket_name"] = old["bucket"]
    if old["type"] == "transfer":
        replacement.update(source_bucket=old["source"], destination_bucket=old["destination"])
    replacement.update(changes)
    canonical, new = _financial(state, replacement, received_at, batch_id, check_source=False)
    _revise(state, old, new, result)
    result["actions"].append({"type": "correct", "transaction_id": old["id"],
                              "changes": {field: canonical[field] for field in changes}})
    result["summary"].append(f"Correct {old['id']}: {format_money(old['amount'])} → {format_money(new['amount'])}, {new['date']}")
    _expense_feedback(state, new, received_at, result)
    if old["type"] == "expense":
        _target_feedback(state, old["bucket"], old["date"][:7] + "-01", result)


def _undo(state, action, result):
    selectors = [key for key in ("transaction_id", "batch_id", "last") if key in action]
    if len(selectors) != 1 or ("last" in action and action["last"] is not True):
        raise BudgetError("invalid_undo", "Select one transaction, batch, or last=true")
    if "transaction_id" in action:
        selected = [_transaction(state, action["transaction_id"])]
        canonical = deepcopy(action)
    else:
        batch_id = action.get("batch_id")
        if "last" in action:
            latest = next((t for t in reversed(state["transactions"]) if t["active"] and t["type"] in FINANCIAL), None)
            if latest is None:
                raise BudgetError("nothing_to_undo", "No active financial batch to undo")
            batch_id = latest["batch_id"]
        if not isinstance(batch_id, str):
            raise BudgetError("invalid_undo", "Select a financial batch")
        selected = [t for t in reversed(state["transactions"]) if t["active"] and t["batch_id"] == batch_id]
        if not selected:
            raise BudgetError("nothing_to_undo", "No active transactions in this batch")
        canonical = {"type": "undo", "batch_id": batch_id}
    for old in selected:
        new = {**deepcopy(old), "active": False}
        _revise(state, old, new, result)
        result["summary"].append(f"Undo {old['type']} {old['id']}: {format_money(old['amount'])}")
        if old["type"] == "expense":
            _target_feedback(state, old["bucket"], old["date"][:7] + "-01", result)
    result["actions"].append(canonical)


def plan(snapshot: dict, actions: list[dict], received_at) -> dict:
    if not isinstance(actions, list) or not 1 <= len(actions) <= 8:
        raise BudgetError("invalid_batch", "A batch must contain 1–8 ordered actions")
    state = deepcopy(snapshot)
    local_today(received_at, state["timezone"])
    result = {"snapshot": state, "postings": [], "events": [], "metadata": [],
              "warnings": [], "actions": [], "summary": []}
    batch_id = str(uuid4())
    for action in actions:
        if (not isinstance(action, dict) or not isinstance(action.get("type"), str)
                or action["type"] not in FIELDS):
            raise BudgetError("unsupported_action", "Unsupported budgeting action")
        kind = action["type"]
        if set(action) - FIELDS[kind] - {"type"}:
            raise BudgetError("invalid_action", "Unexpected action fields")
        if not state["onboarded"] and kind not in {"opening", "create_bucket", "set_target"}:
            raise BudgetError("not_onboarded", "Record opening money before financial actions")
        if kind == "create_bucket":
            name = _name(action.get("name"))
            if name.casefold() == "pool":
                raise BudgetError("invalid_bucket", "The pool account name is reserved")
            if any(name.casefold() == n.casefold() for n in state["buckets"]):
                raise BudgetError("duplicate_bucket", "A bucket with that name already exists")
            bucket = {"id": str(uuid4()), "balance": 0, "target": None}
            state["buckets"][name] = bucket
            result["metadata"].append({"type": kind, "name": name, "bucket": deepcopy(bucket)})
            result["actions"].append({"type": kind, "name": name})
            result["summary"].append(f"Create bucket {name}")
        elif kind == "set_target":
            _set_target(state, action, received_at, result)
        elif kind == "correct":
            _correct(state, action, received_at, batch_id, result)
        elif kind == "undo":
            _undo(state, action, result)
        elif kind in FINANCIAL:
            canonical, tx = _financial(state, action, received_at, batch_id)
            _apply(state, _effects(tx), tx["id"], result["postings"])
            state["transactions"].append(tx)
            result["events"].append({"transaction": deepcopy(tx), "previous": None})
            result["actions"].append(canonical)
            result["summary"].append(f"{kind}: {format_money(tx['amount'])} on {tx['date']}")
            _expense_feedback(state, tx, received_at, result)
        else:
            raise BudgetError("unsupported_action", "Unsupported budgeting action")
    state["revision"] += 1
    return result
