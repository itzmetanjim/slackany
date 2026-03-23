"""Tests for the S7 interpreter."""

import pytest
from s7.interpreter import Interpreter, Environment, S7Error, StepLimitExceeded


def make_interpreter(extra_bindings=None, step_limit=50000):
    """Create a minimal interpreter for testing (no Slack client)."""
    echo_lines = []

    bindings = {
        "#!": lambda: "C_TEST",
        "@!": lambda: "U_TEST",
        "+": lambda a, b: a + b,
        "-": lambda a, b: a - b,
        "*": lambda a, b: a * b,
        "/": lambda a, b: a / b,
        ">": lambda a, b: a > b,
        "<": lambda a, b: a < b,
        ">=": lambda a, b: a >= b,
        "<=": lambda a, b: a <= b,
        "!=": lambda a, b: a != b,
        "==": lambda a, b: a == b,
        "=": lambda a, b: a == b,
        "and": lambda a, b: a and b,
        "or": lambda a, b: a or b,
        "&&": lambda a, b: a and b,
        "||": lambda a, b: a or b,
        "not": lambda a: not a,
        "true": True,
        "false": False,
        "nil": None,
        "index": lambda l, i: l[int(i)],
        "length": lambda x: len(x),
        "flatten": lambda *a: _flatten(*a),
        "append": lambda lst, *items: (lst if isinstance(lst, list) else [lst]) + list(items),
        "range": lambda *a: list(range(*[int(x) for x in a])),
        "str": lambda x: str(x),
        "strjoin": lambda sep, *lists: str(sep).join(str(i) for i in _flatten(*lists)),
        "concat": lambda *a: "".join(str(x) for x in a),
        "echo": lambda *a: echo_lines.append(" ".join(str(x) for x in a)),
        "abs": lambda x: abs(x),
        "min": lambda *a: min(a),
        "max": lambda *a: max(a),
        "mod": lambda a, b: a % b,
        "filter": lambda fn, lst: [x for x in lst if fn(x)],
        "map": lambda fn, lst: [fn(x) for x in lst],
        "number?": lambda x: isinstance(x, (int, float)),
        "string?": lambda x: isinstance(x, str),
        "list?": lambda x: isinstance(x, list),
        "nil?": lambda x: x is None,
        "type": lambda x: type(x).__name__,
        "eq": lambda a, b: a == b,
    }
    if extra_bindings:
        bindings.update(extra_bindings)

    env = Environment(bindings)
    interp = Interpreter(env, step_limit=step_limit)
    return interp, echo_lines


def _flatten(*args):
    out = []
    for a in args:
        if isinstance(a, list):
            out.extend(_flatten(*a))
        else:
            out.append(a)
    return out


# ---------------------------------------------------------------------------
# Arithmetic
# ---------------------------------------------------------------------------

class TestArithmetic:
    def test_add(self):
        interp, _ = make_interpreter()
        assert interp.run("(+ 1 2)") == 3

    def test_subtract(self):
        interp, _ = make_interpreter()
        assert interp.run("(- 10 3)") == 7

    def test_multiply(self):
        interp, _ = make_interpreter()
        assert interp.run("(* 4 5)") == 20

    def test_divide(self):
        interp, _ = make_interpreter()
        assert interp.run("(/ 10 2)") == 5.0

    def test_nested_arithmetic(self):
        interp, _ = make_interpreter()
        assert interp.run("(+ (* 2 3) (- 10 4))") == 12

    def test_infix_arithmetic(self):
        interp, _ = make_interpreter()
        assert interp.run("(1 + 2)") == 3

    def test_infix_comparison(self):
        interp, _ = make_interpreter()
        assert interp.run("(5 > 3)") is True
        assert interp.run("(2 > 3)") is False


# ---------------------------------------------------------------------------
# Variables & Scoping
# ---------------------------------------------------------------------------

class TestVariables:
    def test_define_and_use(self):
        interp, _ = make_interpreter()
        assert interp.run("(begin (define x 42) x)") == 42

    def test_define_expression(self):
        interp, _ = make_interpreter()
        assert interp.run("(begin (define x (+ 1 2)) x)") == 3

    def test_nested_scope(self):
        interp, _ = make_interpreter()
        result = interp.run("""
            (begin
                (define x 10)
                (define y (+ x 5))
                y)
        """)
        assert result == 15


# ---------------------------------------------------------------------------
# Control Flow
# ---------------------------------------------------------------------------

class TestControlFlow:
    def test_if_true(self):
        interp, echoes = make_interpreter()
        interp.run('(if true (echo "yes"))')
        assert echoes == ["yes"]

    def test_if_false(self):
        interp, echoes = make_interpreter()
        interp.run('(if false (echo "yes"))')
        assert echoes == []

    def test_if_else(self):
        interp, echoes = make_interpreter()
        interp.run('(if false (echo "yes") (else (echo "no")))')
        assert echoes == ["no"]

    def test_if_elif_else(self):
        interp, echoes = make_interpreter()
        interp.run("""
            (if false (echo "first")
                (elif true (echo "second"))
                (else (echo "third")))
        """)
        assert echoes == ["second"]

    def test_if_with_expression(self):
        interp, echoes = make_interpreter()
        interp.run('(if (5 > 3) (echo "bigger"))')
        assert echoes == ["bigger"]


# ---------------------------------------------------------------------------
# Foreach
# ---------------------------------------------------------------------------

class TestForeach:
    def test_basic_foreach(self):
        interp, echoes = make_interpreter()
        interp.run("""
            (begin
                (define mylist (list 1 2 3))
                (foreach x mylist (echo x)))
        """)
        assert echoes == ["1", "2", "3"]

    def test_foreach_with_body(self):
        interp, echoes = make_interpreter()
        interp.run("""
            (begin
                (define nums (list 10 20))
                (foreach n nums (echo (+ n 1))))
        """)
        assert echoes == ["11", "21"]


# ---------------------------------------------------------------------------
# Lambda
# ---------------------------------------------------------------------------

class TestLambda:
    def test_basic_lambda(self):
        interp, _ = make_interpreter()
        result = interp.run("""
            (begin
                (define double (lambda (x) (* x 2)))
                (double 5))
        """)
        assert result == 10

    def test_lambda_closure(self):
        interp, _ = make_interpreter()
        result = interp.run("""
            (begin
                (define offset 100)
                (define add_offset (lambda (x) (+ x offset)))
                (add_offset 5))
        """)
        assert result == 105

    def test_shorthand_define(self):
        interp, _ = make_interpreter()
        result = interp.run("""
            (begin
                (define (square x) (* x x))
                (square 7))
        """)
        assert result == 49


# ---------------------------------------------------------------------------
# Let
# ---------------------------------------------------------------------------

class TestLet:
    def test_basic_let(self):
        interp, _ = make_interpreter()
        result = interp.run("(let ((x 10) (y 20)) (+ x y))")
        assert result == 30


# ---------------------------------------------------------------------------
# List Operations
# ---------------------------------------------------------------------------

class TestLists:
    def test_list_literal(self):
        interp, _ = make_interpreter()
        result = interp.run("(list 1 2 3)")
        assert result == [1, 2, 3]

    def test_index(self):
        interp, _ = make_interpreter()
        result = interp.run("(index (list 10 20 30) 1)")
        assert result == 20

    def test_index_sugar(self):
        interp, _ = make_interpreter()
        result = interp.run("""
            (begin
                (define arr (list 10 20 30))
                arr[1])
        """)
        assert result == 20

    def test_length(self):
        interp, _ = make_interpreter()
        result = interp.run("(length (list 1 2 3))")
        assert result == 3

    def test_append(self):
        interp, _ = make_interpreter()
        result = interp.run("(append (list 1 2) 3)")
        assert result == [1, 2, 3]

    def test_range(self):
        interp, _ = make_interpreter()
        result = interp.run("(range 5)")
        assert result == [0, 1, 2, 3, 4]

    def test_map(self):
        interp, _ = make_interpreter()
        result = interp.run("""
            (begin
                (define (double x) (* x 2))
                (map double (list 1 2 3)))
        """)
        assert result == [2, 4, 6]

    def test_filter(self):
        interp, _ = make_interpreter()
        result = interp.run("""
            (begin
                (define (big x) (x > 2))
                (filter big (list 1 2 3 4)))
        """)
        assert result == [3, 4]


# ---------------------------------------------------------------------------
# String Operations
# ---------------------------------------------------------------------------

class TestStrings:
    def test_str_cast(self):
        interp, _ = make_interpreter()
        result = interp.run("(str 42)")
        assert result == "42"

    def test_concat(self):
        interp, _ = make_interpreter()
        result = interp.run('(concat "hello" " " "world")')
        assert result == "hello world"

    def test_strjoin(self):
        interp, _ = make_interpreter()
        result = interp.run('(strjoin ", " (list 1 2 3))')
        assert result == "1, 2, 3"


# ---------------------------------------------------------------------------
# Context Atoms
# ---------------------------------------------------------------------------

class TestContext:
    def test_channel_context(self):
        interp, _ = make_interpreter()
        result = interp.run("#!")
        assert result == "C_TEST"

    def test_user_context(self):
        interp, _ = make_interpreter()
        result = interp.run("@!")
        assert result == "U_TEST"


# ---------------------------------------------------------------------------
# Echo
# ---------------------------------------------------------------------------

class TestEcho:
    def test_simple_echo(self):
        interp, echoes = make_interpreter()
        interp.run('(echo "hello" "world")')
        assert echoes == ["hello world"]

    def test_echo_number(self):
        interp, echoes = make_interpreter()
        interp.run("(echo (+ 1 2))")
        assert echoes == ["3"]


# ---------------------------------------------------------------------------
# Not / Logic
# ---------------------------------------------------------------------------

class TestLogic:
    def test_not_true(self):
        interp, _ = make_interpreter()
        assert interp.run("(not true)") is False

    def test_not_false(self):
        interp, _ = make_interpreter()
        assert interp.run("(not false)") is True

    def test_prefix_bang(self):
        interp, _ = make_interpreter()
        assert interp.run("!true") is False

    def test_and(self):
        interp, _ = make_interpreter()
        assert interp.run("(and true false)") is False
        assert interp.run("(and true true)") is True

    def test_or(self):
        interp, _ = make_interpreter()
        assert interp.run("(or false true)") is True


# ---------------------------------------------------------------------------
# Step Limit
# ---------------------------------------------------------------------------

class TestStepLimit:
    def test_exceeds_step_limit(self):
        interp, _ = make_interpreter(step_limit=10)
        with pytest.raises(StepLimitExceeded):
            interp.run("""
                (begin
                    (define (loop n) (if (n > 0) (loop (- n 1))))
                    (loop 100))
            """)


# ---------------------------------------------------------------------------
# Error Handling
# ---------------------------------------------------------------------------

class TestErrors:
    def test_undefined_symbol(self):
        interp, _ = make_interpreter()
        with pytest.raises(S7Error, match="Undefined symbol"):
            interp.run("undefined_var")

    def test_not_callable(self):
        interp, _ = make_interpreter()
        with pytest.raises(S7Error):
            interp.run("(42 1 2)")


# ---------------------------------------------------------------------------
# Return
# ---------------------------------------------------------------------------

class TestReturn:
    def test_return_no_value(self):
        interp, _ = make_interpreter()
        result = interp.run("""
            (begin
                (return)
                (+ 1 2))
        """)
        assert result is None

    def test_return_with_value(self):
        interp, _ = make_interpreter()
        result = interp.run("""
            (begin
                (return 42)
                (+ 1 2))
        """)
        assert result == 42

    def test_return_in_lambda(self):
        interp, _ = make_interpreter()
        result = interp.run("""
            (begin
                (define (early x)
                    (if (x > 5)
                        (return "big"))
                    "small")
                (early 10))
        """)
        assert result == "big"

    def test_return_in_lambda_fallthrough(self):
        interp, _ = make_interpreter()
        result = interp.run("""
            (begin
                (define (early x)
                    (if (x > 5)
                        (return "big"))
                    "small")
                (early 3))
        """)
        assert result == "small"

    def test_return_in_foreach(self):
        interp, _ = make_interpreter()
        result = interp.run("""
            (begin
                (define result 0)
                (foreach x (list 1 2 3 4 5)
                    (if (= x 3)
                        (return x))
                    (set result x))
                result)
        """)
        # return exits the entire execution, not just the foreach
        assert result == 3


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------

class TestErrorForm:
    def test_error_throws(self):
        interp, _ = make_interpreter()
        with pytest.raises(S7Error, match="Something went wrong"):
            interp.run('(error "Something went wrong")')

    def test_error_with_expression(self):
        interp, _ = make_interpreter()
        with pytest.raises(S7Error, match="Value is 42"):
            interp.run('(error (concat "Value is " 42))')


# ---------------------------------------------------------------------------
# Local Macros
# ---------------------------------------------------------------------------

class TestLocalMacros:
    def test_macro_define_and_call(self):
        interp, _ = make_interpreter()
        result = interp.run("""
            (begin
                (macro "double" (* (index args 0) 2))
                (call "double" 21))
        """)
        assert result == 42

    def test_macro_with_multiple_args(self):
        interp, _ = make_interpreter()
        result = interp.run("""
            (begin
                (macro "sum3" (+ (index args 0) (+ (index args 1) (index args 2))))
                (call "sum3" 10 20 30))
        """)
        assert result == 60

    def test_local_macro_scoped_to_execution(self):
        interp, _ = make_interpreter()
        # First execution defines a local macro
        interp.run('(macro "test" 123)')
        # Should be able to call it in the same interpreter
        result = interp.run('(call "test")')
        assert result == 123

    def test_call_undefined_macro_without_store(self):
        interp, _ = make_interpreter()
        with pytest.raises(S7Error, match="macro store not available"):
            interp.run('(call "nonexistent")')
