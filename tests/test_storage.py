"""Tests for the S7 key-value store."""

import os
import tempfile
import pytest
from s7.storage import S7Store


@pytest.fixture
def store():
    """Create an S7Store backed by a temp file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = S7Store(path)
    yield s
    os.unlink(path)


class TestS7Store:
    def test_set_and_get_string(self, store):
        store.set("U123", "name", "Alice")
        assert store.get("U123", "name") == "Alice"

    def test_set_and_get_number(self, store):
        store.set("U123", "count", 42)
        assert store.get("U123", "count") == 42

    def test_set_and_get_list(self, store):
        store.set("U123", "items", [1, 2, 3])
        assert store.get("U123", "items") == [1, 2, 3]

    def test_set_and_get_dict(self, store):
        store.set("U123", "config", {"theme": "dark", "volume": 80})
        assert store.get("U123", "config") == {"theme": "dark", "volume": 80}

    def test_get_nonexistent(self, store):
        assert store.get("U123", "missing") is None

    def test_update_value(self, store):
        store.set("U123", "count", 1)
        store.set("U123", "count", 2)
        assert store.get("U123", "count") == 2

    def test_user_isolation(self, store):
        store.set("U123", "key", "value1")
        store.set("U456", "key", "value2")
        assert store.get("U123", "key") == "value1"
        assert store.get("U456", "key") == "value2"

    def test_delete(self, store):
        store.set("U123", "key", "value")
        assert store.delete("U123", "key") is True
        assert store.get("U123", "key") is None

    def test_delete_nonexistent(self, store):
        assert store.delete("U123", "missing") is False

    def test_list_keys(self, store):
        store.set("U123", "alpha", 1)
        store.set("U123", "beta", 2)
        keys = store.list_keys("U123")
        key_names = [k[0] for k in keys]
        assert "alpha" in key_names
        assert "beta" in key_names
        assert len(keys) == 2

    def test_list_keys_empty(self, store):
        assert store.list_keys("U123") == []

    def test_list_keys_user_isolation(self, store):
        store.set("U123", "key1", 1)
        store.set("U456", "key2", 2)
        keys = store.list_keys("U123")
        assert len(keys) == 1
        assert keys[0][0] == "key1"

    def test_clear_user(self, store):
        store.set("U123", "a", 1)
        store.set("U123", "b", 2)
        store.set("U456", "c", 3)
        count = store.clear_user("U123")
        assert count == 2
        assert store.get("U123", "a") is None
        assert store.get("U123", "b") is None
        assert store.get("U456", "c") == 3  # Other user unaffected

    def test_clear_user_empty(self, store):
        count = store.clear_user("U123")
        assert count == 0
