"""Tests for the S7 macro store."""

import os
import tempfile
import pytest
from s7.macros import MacroStore


@pytest.fixture
def store():
    """Create a MacroStore backed by a temp file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = MacroStore(path)
    yield s
    os.unlink(path)


class TestMacroStore:
    def test_set_and_get(self, store):
        store.set("greet", '(echo "hello")', "U123")
        assert store.get("greet") == '(echo "hello")'

    def test_get_nonexistent(self, store):
        assert store.get("missing") is None

    def test_update(self, store):
        store.set("greet", '(echo "v1")', "U123")
        store.set("greet", '(echo "v2")', "U456")
        assert store.get("greet") == '(echo "v2")'

    def test_remove(self, store):
        store.set("greet", '(echo "hi")', "U123")
        assert store.remove("greet") is True
        assert store.get("greet") is None

    def test_remove_nonexistent(self, store):
        assert store.remove("missing") is False

    def test_list_all(self, store):
        store.set("alpha", "(echo 1)", "U1")
        store.set("beta", "(echo 2)", "U2")
        items = store.list_all()
        names = [i[0] for i in items]
        assert "alpha" in names
        assert "beta" in names
        assert len(items) == 2

    def test_list_all_empty(self, store):
        assert store.list_all() == []

    def test_get_with_author(self, store):
        store.set("greet", '(echo "hello")', "U123")
        result = store.get_with_author("greet")
        assert result is not None
        code, author = result
        assert code == '(echo "hello")'
        assert author == "U123"

    def test_get_with_author_nonexistent(self, store):
        assert store.get_with_author("missing") is None

    def test_get_with_author_after_update(self, store):
        store.set("greet", '(echo "v1")', "U123")
        store.set("greet", '(echo "v2")', "U456")
        result = store.get_with_author("greet")
        assert result is not None
        code, author = result
        assert code == '(echo "v2")'
        assert author == "U456"  # Author updated to whoever last saved it
