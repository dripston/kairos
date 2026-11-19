"""JSON that never carries a non-finite number.

Python's json writes float('inf') / float('nan') as the bare words Infinity / NaN, which are not JSON, and
Starlette's JSONResponse refuses them (ValueError -> HTTP 500). One such value in an audit row used to turn
every later GET /audit, /events/since and /hitl/audit into a 500 (E2E QA, 2026-11-20). Everything that writes
JSON to the database or to an HTTP response goes through `finite()` so a non-finite float becomes null.

    finite({"dpd_age_hours": float("inf")})   -> {"dpd_age_hours": None}
    dumps(obj, **json_kwargs)                  -> json.dumps(finite(obj), allow_nan=False, **json_kwargs)

api.SafeJSONResponse (the app-wide FastAPI response class) uses finite() too. This module stays standard library
only, so the ledger can use it without FastAPI.
"""
from __future__ import annotations

import json
import math
from typing import Any


def finite(obj: Any) -> Any:
    """A copy of `obj` with every non-finite float (inf, -inf, nan), at any depth, replaced by None.
    Dicts, lists and tuples are walked (a tuple comes back as a list, as JSON would write it); anything
    else is returned unchanged."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: finite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [finite(v) for v in obj]
    return obj


def dumps(obj: Any, **kwargs: Any) -> str:
    """json.dumps for storage: non-finite floats become null, and allow_nan=False makes sure none slip by."""
    return json.dumps(finite(obj), allow_nan=False, **kwargs)

