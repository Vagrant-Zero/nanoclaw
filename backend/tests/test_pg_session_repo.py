"""Tests for PgSessionRepo JSONB deserialization fixes.

asyncpg auto-deserializes JSONB columns to Python objects (list/dict).
These tests verify that the code handles both formats: pre-deserialized
list/dict and raw JSON strings (for backward compatibility).
"""

from __future__ import annotations

import json

from nanoclaw.models.chat import ChatMessage

MOCK_JSONB_LIST = [
    {"content": "hello", "role": "user", "metadata": {}},
    {"content": "hi there", "role": "assistant", "metadata": {}},
]


def test_append_message_list_input() -> None:
    """append_message: row.history is already a list from asyncpg JSONB."""
    history = (
        list(MOCK_JSONB_LIST)
        if isinstance(MOCK_JSONB_LIST, list)
        else json.loads(MOCK_JSONB_LIST)
    )
    new_msg = ChatMessage(content="how are you?", role="user")
    history.append(new_msg.to_dict())
    assert len(history) == 3
    assert history[2]["content"] == "how are you?"
    print("PASS: append_message with list input adds message correctly")


def test_append_message_string_input() -> None:
    """append_message: row.history is a JSON string (backward compat)."""
    json_str = json.dumps(MOCK_JSONB_LIST)
    history = (
        list(json.loads(json_str))
        if isinstance(json.loads(json_str), list)
        else json.loads(json_str)
    )
    new_msg = ChatMessage(content="how are you?", role="user")
    history.append(new_msg.to_dict())
    assert len(history) == 3
    assert history[2]["content"] == "how are you?"
    print("PASS: append_message with string input adds message correctly")


def test_get_history_list_input() -> None:
    """get(): row.history is already a list from asyncpg JSONB."""
    raw = (
        list(MOCK_JSONB_LIST)
        if isinstance(MOCK_JSONB_LIST, list)
        else json.loads(MOCK_JSONB_LIST)
    )
    messages = [ChatMessage.from_dict(m) for m in raw]
    assert len(messages) == 2
    assert messages[0].content == "hello"
    assert messages[1].content == "hi there"
    print("PASS: get() with list input deserializes correctly")


def test_get_history_string_input() -> None:
    """get(): row.history is a JSON string (backward compat)."""
    json_str = json.dumps(MOCK_JSONB_LIST)
    parsed = json.loads(json_str)
    raw = list(parsed) if isinstance(parsed, list) else json.loads(json_str)
    messages = [ChatMessage.from_dict(m) for m in raw]
    assert len(messages) == 2
    assert messages[0].content == "hello"
    print("PASS: get() with string input deserializes correctly")
