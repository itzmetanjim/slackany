"""Tests for the S7 parser."""

import pytest
from s7.parser import parse, ParseError, S7String, S7SlackEntity


class TestParseAtoms:
    def test_integer(self):
        assert parse("42") == [42]

    def test_float(self):
        assert parse("3.14") == [3.14]

    def test_string(self):
        assert parse('"hello"') == [S7String("hello")]

    def test_symbol(self):
        assert parse("foo") == ["foo"]

    def test_bool_true(self):
        assert parse("true") == [True]

    def test_bool_false(self):
        assert parse("false") == [False]

    def test_nil(self):
        assert parse("nil") == [None]

    def test_slack_user(self):
        assert parse("<@U12345>") == [S7SlackEntity("<@U12345>")]

    def test_slack_channel(self):
        assert parse("<#C12345|general>") == [S7SlackEntity("<#C12345|general>")]

    def test_context_atoms(self):
        assert parse("#! @!") == ["#!", "@!"]


class TestParseSExpressions:
    def test_simple_call(self):
        assert parse("(echo 1 2)") == [["echo", 1, 2]]

    def test_nested_call(self):
        assert parse("(echo (+ 1 2))") == [["echo", ["+", 1, 2]]]

    def test_empty_parens(self):
        assert parse("()") == [[]]

    def test_multiple_top_level(self):
        result = parse("(echo 1) (echo 2)")
        assert result == [["echo", 1], ["echo", 2]]


class TestInfixTransform:
    def test_simple_infix(self):
        # (A + B) -> (+ A B)
        result = parse("(1 + 2)")
        assert result == [["+", 1, 2]]

    def test_comparison(self):
        result = parse("(x > 5)")
        assert result == [[">", "x", 5]]

    def test_logical_and(self):
        result = parse("(a && b)")
        assert result == [["&&", "a", "b"]]

    def test_chained_infix(self):
        # (1 + 2 + 3) -> (+ (+ 1 2) 3)
        result = parse("(1 + 2 + 3)")
        assert result == [["+", ["+", 1, 2], 3]]

    def test_mixed_infix(self):
        # (1 + 2 * 3) -> (* (+ 1 2) 3) — left-to-right, no precedence
        result = parse("(1 + 2 * 3)")
        assert result == [["*", ["+", 1, 2], 3]]

    def test_nested_infix(self):
        result = parse("((length x) > 5)")
        assert result == [[">", ["length", "x"], 5]]


class TestIndexTransform:
    def test_simple_index(self):
        # mylist[0] -> (index mylist 0)
        result = parse("mylist[0]")
        assert result == [["index", "mylist", 0]]

    def test_chained_index(self):
        # a[0][1] -> (index (index a 0) 1)
        result = parse("a[0][1]")
        assert result == [["index", ["index", "a", 0], 1]]


class TestNotTransform:
    def test_prefix_bang(self):
        result = parse("!x")
        assert result == [["not", "x"]]

    def test_prefix_not_keyword(self):
        result = parse("not x")
        assert result == [["not", "x"]]

    def test_not_in_sexp_is_function_call(self):
        """(not true) inside s-expr should be a normal function call."""
        result = parse("(not true)")
        assert result == [["not", True]]

    def test_bang_in_sexp_is_prefix(self):
        """!flag inside s-expr should work as prefix."""
        result = parse("(if !flag 1)")
        assert result == [["if", ["not", "flag"], 1]]


class TestComplexExpressions:
    def test_if_elif_else(self):
        code = '(if (x > 5) (echo "big") (elif (x > 2) (echo "mid")) (else (echo "small")))'
        result = parse(code)
        assert result[0][0] == "if"
        assert result[0][1] == [">", "x", 5]
        assert result[0][2] == ["echo", S7String("big")]

    def test_foreach(self):
        code = '(foreach item mylist (echo item))'
        result = parse(code)
        assert result == [["foreach", "item", "mylist", ["echo", "item"]]]

    def test_define(self):
        code = '(define x 42)'
        result = parse(code)
        assert result == [["define", "x", 42]]

    def test_begin_block(self):
        code = '(begin (define x 1) (echo x))'
        result = parse(code)
        assert result == [["begin", ["define", "x", 1], ["echo", "x"]]]


class TestParseErrors:
    def test_unclosed_paren(self):
        with pytest.raises(ParseError):
            parse("(echo 1")

    def test_unexpected_rparen(self):
        with pytest.raises((ParseError, IndexError)):
            parse(")")
