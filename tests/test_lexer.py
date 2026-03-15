"""Tests for the S7 lexer."""

import pytest
from s7.lexer import tokenize, TOK_LPAREN, TOK_RPAREN, TOK_NUMBER, TOK_STRING, TOK_SYMBOL, TOK_SLACK_ENTITY, TOK_CONTEXT, TOK_LBRACKET, TOK_RBRACKET


class TestTokenizeAtoms:
    def test_integer(self):
        tokens = tokenize("42")
        assert tokens == [(TOK_NUMBER, "42")]

    def test_negative_integer(self):
        tokens = tokenize("-7")
        assert tokens == [(TOK_NUMBER, "-7")]

    def test_float(self):
        tokens = tokenize("3.14")
        assert tokens == [(TOK_NUMBER, "3.14")]

    def test_string(self):
        tokens = tokenize('"hello world"')
        assert tokens == [(TOK_STRING, "hello world")]

    def test_string_with_escape(self):
        tokens = tokenize(r'"say \"hi\""')
        assert tokens == [(TOK_STRING, 'say "hi"')]

    def test_symbol(self):
        tokens = tokenize("foo")
        assert tokens == [(TOK_SYMBOL, "foo")]

    def test_user_entity(self):
        tokens = tokenize("<@U12345ABC>")
        assert tokens == [(TOK_SLACK_ENTITY, "<@U12345ABC>")]

    def test_channel_entity_with_label(self):
        tokens = tokenize("<#C12345|general>")
        assert tokens == [(TOK_SLACK_ENTITY, "<#C12345|general>")]

    def test_channel_entity_no_label(self):
        tokens = tokenize("<#C12345>")
        assert tokens == [(TOK_SLACK_ENTITY, "<#C12345>")]

    def test_context_channel(self):
        tokens = tokenize("#!")
        assert tokens == [(TOK_CONTEXT, "#!")]

    def test_context_user(self):
        tokens = tokenize("@!")
        assert tokens == [(TOK_CONTEXT, "@!")]


class TestTokenizeExpressions:
    def test_simple_sexp(self):
        tokens = tokenize("(+ 1 2)")
        assert tokens == [
            (TOK_LPAREN, "("),
            (TOK_SYMBOL, "+"),
            (TOK_NUMBER, "1"),
            (TOK_NUMBER, "2"),
            (TOK_RPAREN, ")"),
        ]

    def test_nested_sexp(self):
        tokens = tokenize("(echo (+ 1 2))")
        assert len(tokens) == 8

    def test_infix_style(self):
        tokens = tokenize("(A + B)")
        types = [t[0] for t in tokens]
        assert types == [TOK_LPAREN, TOK_SYMBOL, TOK_SYMBOL, TOK_SYMBOL, TOK_RPAREN]

    def test_brackets(self):
        tokens = tokenize("mylist[0]")
        assert tokens == [
            (TOK_SYMBOL, "mylist"),
            (TOK_LBRACKET, "["),
            (TOK_NUMBER, "0"),
            (TOK_RBRACKET, "]"),
        ]

    def test_multichar_operators(self):
        tokens = tokenize(">= <= != && ||")
        vals = [t[1] for t in tokens]
        assert vals == [">=", "<=", "!=", "&&", "||"]

    def test_whitespace_and_commas_ignored(self):
        tokens = tokenize("(echo 1, 2, 3)")
        # Commas treated as whitespace
        vals = [t[1] for t in tokens]
        assert vals == ["(", "echo", "1", "2", "3", ")"]

    def test_comment_skipped(self):
        tokens = tokenize(";; this is a comment\n(echo 1)")
        vals = [t[1] for t in tokens]
        assert vals == ["(", "echo", "1", ")"]


class TestTokenizeErrors:
    def test_unexpected_char(self):
        with pytest.raises(SyntaxError, match="unexpected character"):
            tokenize("§")
