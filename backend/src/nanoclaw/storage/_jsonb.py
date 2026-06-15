"""JSONB deserialization utility for asyncpg JSONB columns.

asyncpg auto-deserializes JSONB columns to Python objects (list or dict),
but in some code paths (e.g. raw SQLAlchemy Row) the value may still be
a JSON string. This module provides a consistent deserialization helper.
"""

from __future__ import annotations

import json
from typing import Any


def deserialize_jsonb(val: Any) -> Any:
    """Deserialize a JSONB column value, handling both pre-deserialized
    Python objects (list/dict) and raw JSON strings.

    Args:
        val: The column value from SQLAlchemy Row — may be a Python
             list, dict, JSON string, or None.

    Returns:
        The deserialized Python object (list, dict, or None).
    """
    if val is None:
        return None
    if isinstance(val, (list, dict)):
        return val
    return json.loads(val)


def deserialize_jsonb_list(val: Any) -> list:
    """Like ``deserialize_jsonb`` but always returns a list.

    Raises ``TypeError`` if the value is not a list after deserialization.
    """
    result = deserialize_jsonb(val)
    if result is None:
        return []
    if isinstance(result, list):
        return result
    raise TypeError(f"Expected JSONB array, got {type(result).__name__}")
