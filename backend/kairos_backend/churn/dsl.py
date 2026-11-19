"""K27 condition DSL: the PRD's condition engine (CN "Collection Orchestration Engine" PRD v3 §5.2/§5.3).

A condition is JSON. Leaf:

    {"field": "whatsapp.status", "op": "<=", "value": "Delivered"}

Operators (PRD: =, ≠, >, <, ≥, ≤, contains, between; plus in / not_in / exists / not_contains):
    eq = ==   ne != ≠   gt >   ge >= ≥   lt <   le <= ≤   contains   not_contains   between [lo, hi]
    in [..]   not_in [..]   exists true|false

Combinators:
    {"all": [c, ...]}  {"any": [c, ...]}  {"not": c}   true (always)   false (never)
    {"chain": [c1, {"join": "OR", ...c2}, {"join": "AND", ...c3}]}
        PRD §5.2 semantics: each row is AND/OR-joined to the next, evaluated strictly LEFT TO RIGHT,
        no grouping: ((c1 OR c2) AND c3). all/any/not give grouping on top (DRAFT extension).

Ordinal status fields (`<channel>.status`, PRD §4): No data < Sent < Delivered < Read < Clicked <
Responded ("Failed" ranks with No data). Comparing such a field with a label compares ranks, so
"sms.status <= Delivered" means "not read yet".

evaluate(cond, feats) -> (bool, [explanations]) — every leaf that decided the result is reported as
"field op value (actual=...)" so a decision can always be explained.
"""
from __future__ import annotations

from typing import Any, Mapping

ORDINAL = ("No data", "Sent", "Delivered", "Read", "Clicked", "Responded")
RANK = {label: i for i, label in enumerate(ORDINAL)}
RANK["Failed"] = 0

OPS = {"eq": "eq", "=": "eq", "==": "eq", "ne": "ne", "!=": "ne", "≠": "ne", "gt": "gt", ">": "gt",
       "ge": "ge", ">=": "ge", "≥": "ge", "lt": "lt", "<": "lt", "le": "le", "<=": "le", "≤": "le",
       "contains": "contains", "not_contains": "not_contains", "between": "between", "in": "in",
       "not_in": "not_in", "exists": "exists"}


def is_ordinal_field(field: str) -> bool:
    return field.endswith(".status") and field.split(".")[0] in (
        "sms", "whatsapp", "email", "ivr", "voicebot", "any")


def _norm(field: str, v: Any) -> Any:
    if is_ordinal_field(field) and isinstance(v, str):
        if v not in RANK:
            raise ValueError(f"{field}: unknown status label {v!r} (use one of {ORDINAL})")
        return RANK[v]
    return v


def _cmp(op: str, a: Any, b: Any) -> bool:
    if op == "exists":
        return (a is not None) == bool(b)
    if op == "eq":
        return a == b
    if op == "ne":
        return a != b
    if a is None:
        return op in ("not_contains", "not_in")
    if op == "contains":
        if isinstance(a, (list, tuple, set, frozenset)):
            return b in a
        return str(b).lower() in str(a).lower()
    if op == "not_contains":
        return not _cmp("contains", a, b)
    if op == "in":
        return a in b
    if op == "not_in":
        return a not in b
    try:
        if op == "gt":
            return a > b
        if op == "ge":
            return a >= b
        if op == "lt":
            return a < b
        if op == "le":
            return a <= b
        if op == "between":
            lo, hi = b
            return lo <= a <= hi
    except TypeError:
        return False
    raise ValueError(f"unknown operator {op!r}")


def validate(cond: Any, path: str = "$") -> None:
    """Raise ValueError if `cond` is not a well-formed condition."""
    if isinstance(cond, bool):
        return
    if not isinstance(cond, dict):
        raise ValueError(f"{path}: condition must be an object or true/false")
    keys = set(cond) - {"join", "note"}
    if keys == {"all"} or keys == {"any"}:
        k = keys.pop()
        if not isinstance(cond[k], list):
            raise ValueError(f"{path}.{k}: must be a list")
        for i, c in enumerate(cond[k]):
            validate(c, f"{path}.{k}[{i}]")
        return
    if keys == {"not"}:
        validate(cond["not"], f"{path}.not")
        return
    if keys == {"chain"}:
        for i, c in enumerate(cond["chain"]):
            if i and str(c.get("join", "AND")).upper() not in ("AND", "OR"):
                raise ValueError(f"{path}.chain[{i}].join must be AND or OR")
            validate({k: v for k, v in c.items() if k != "join"}, f"{path}.chain[{i}]")
        return
    if "field" not in cond or "op" not in cond:
        raise ValueError(f"{path}: leaf needs field and op (got {sorted(cond)})")
    op = OPS.get(cond["op"])
    if op is None:
        raise ValueError(f"{path}: unknown operator {cond['op']!r}")
    if op == "between" and not (isinstance(cond.get("value"), list) and len(cond["value"]) == 2):
        raise ValueError(f"{path}: between needs [lo, hi]")
    if op in ("in", "not_in") and not isinstance(cond.get("value"), list):
        raise ValueError(f"{path}: {op} needs a list")
    _norm(cond["field"], cond.get("value"))


def evaluate(cond: Any, feats: Mapping[str, Any]) -> tuple[bool, list[str]]:
    if cond is True:
        return True, ["always"]
    if cond is False or cond is None:
        return False, ["never"]
    if "all" in cond:
        why = []
        for c in cond["all"]:
            ok, w = evaluate(c, feats)
            why += w
            if not ok:
                return False, why
        return True, why
    if "any" in cond:
        why = []
        for c in cond["any"]:
            ok, w = evaluate(c, feats)
            if ok:
                return True, w
            why += w
        return False, why
    if "not" in cond:
        ok, w = evaluate(cond["not"], feats)
        return (not ok), [f"NOT({'; '.join(w)})"]
    if "chain" in cond:
        result, why = None, []
        for i, c in enumerate(cond["chain"]):
            leaf = {k: v for k, v in c.items() if k != "join"}
            ok, w = evaluate(leaf, feats)
            why += w
            if i == 0:
                result = ok
            elif str(c.get("join", "AND")).upper() == "OR":
                result = result or ok
            else:
                result = result and ok
        return bool(result), why
    field, op = cond["field"], OPS[cond["op"]]
    actual = feats.get(field)
    want = cond.get("value")
    a = _norm(field, actual)
    if op == "between":
        b = [_norm(field, x) for x in want]
    elif op in ("in", "not_in"):
        b = [_norm(field, x) for x in want]
    else:
        b = _norm(field, want)
    ok = _cmp(op, a, b)
    return ok, [f"{field} {cond['op']} {want!r} (actual={actual!r}) -> {ok}"]


def fields_of(cond: Any) -> set[str]:
    """Every field a condition reads (for validation against the feature catalogue)."""
    if not isinstance(cond, dict):
        return set()
    out = set()
    if "field" in cond:
        out.add(cond["field"])
    for k in ("all", "any", "chain"):
        for c in cond.get(k, ()):
            out |= fields_of(c)
    if "not" in cond:
        out |= fields_of(cond["not"])
    return out


def in_scope(scope: Mapping[str, Any] | None, campaign_id: str | None, cohort_id: str | None,
             region: str | None) -> bool:
    """Rule / cohort scoping by campaign, cohort and region (state). Missing key = any."""
    if not scope:
        return True
    for key, val in (("campaigns", campaign_id), ("cohorts", cohort_id), ("regions", region)):
        allowed = scope.get(key)
        if allowed and val not in allowed:
            return False
    return True
