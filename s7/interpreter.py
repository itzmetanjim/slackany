"""
S7 Interpreter -- evaluates the AST in a scoped environment.

Features:
  - Lexical scoping with nested environments
  - Special forms: define, begin, if/elif/else, foreach, lambda, let, list,
    map, filter
  - Step counter for runaway-loop protection
  - All Slack operations delegated to built-in functions injected via environment
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Union

from .parser import Expr, S7String, S7SlackEntity, parse


class S7Error(Exception):
    """Runtime error in S7 evaluation."""


class StepLimitExceeded(S7Error):
    """Raised when the step counter exceeds the configured maximum."""


# ---------------------------------------------------------------------------
# Environment (scope chain)
# ---------------------------------------------------------------------------

class Environment:
    """A dict-based scope with an optional parent for lexical scoping."""

    def __init__(self, bindings: Dict[str, Any] | None = None, parent: Environment | None = None):
        self.bindings: Dict[str, Any] = bindings or {}
        self.parent = parent

    def get(self, name: str) -> Any:
        if name in self.bindings:
            return self.bindings[name]
        if self.parent is not None:
            return self.parent.get(name)
        raise S7Error(f"Undefined symbol: {name!r}")

    def set(self, name: str, value: Any) -> None:
        self.bindings[name] = value

    def update_existing(self, name: str, value: Any) -> bool:
        """Update a variable in the nearest enclosing scope that has it."""
        if name in self.bindings:
            self.bindings[name] = value
            return True
        if self.parent is not None:
            return self.parent.update_existing(name, value)
        return False

    def child(self, bindings: Dict[str, Any] | None = None) -> Environment:
        return Environment(bindings or {}, parent=self)


# ---------------------------------------------------------------------------
# Lambda (user-defined function)
# ---------------------------------------------------------------------------

class S7Lambda:
    def __init__(self, params: List[str], body: List[Expr], closure: Environment):
        self.params = params
        self.body = body
        self.closure = closure

    def __repr__(self) -> str:
        return f"<S7Lambda params={self.params}>"


# ---------------------------------------------------------------------------
# Interpreter
# ---------------------------------------------------------------------------

class Interpreter:
    def __init__(self, env: Environment, step_limit: int = 50000):
        self.global_env = env
        self.step_limit = step_limit
        self.steps = 0
        self.output_lines: List[str] = []  # Collects echo output

    # -- Public API ---------------------------------------------------------

    def run(self, source: str) -> Any:
        """Parse and evaluate an S7 source string. Returns the last result."""
        exprs = parse(source)
        result = None
        for expr in exprs:
            result = self.eval(expr, self.global_env)
        return result

    # -- Core eval ----------------------------------------------------------

    def eval(self, expr: Expr, env: Environment) -> Any:
        self.steps += 1
        if self.steps > self.step_limit:
            raise StepLimitExceeded(
                f"Execution exceeded {self.step_limit} steps — killed."
            )

        # --- Literal types that evaluate to themselves ---
        if expr is None:
            return None
        if isinstance(expr, bool):
            return expr
        if isinstance(expr, (int, float)):
            return expr
        if isinstance(expr, S7String):
            return expr.value  # String literal → raw Python string
        if isinstance(expr, S7SlackEntity):
            return expr.raw  # Slack entity → raw string like "<@U123>"

        # --- Symbol (identifier to look up) ---
        if isinstance(expr, str):
            return self._eval_atom(expr, env)

        # --- List (S-Expression) ---
        if isinstance(expr, list):
            if len(expr) == 0:
                return []
            return self._eval_list(expr, env)

        raise S7Error(f"Cannot evaluate: {expr!r}")

    # -- Atom resolution ----------------------------------------------------

    def _eval_atom(self, name: str, env: Environment) -> Any:
        # Context atoms resolve via environment (they're stored as callables)
        if name in ("#!", "@!"):
            val = env.get(name)
            return val() if callable(val) else val
        # Resolve identifier
        return env.get(name)

    # -- S-Expression evaluation -------------------------------------------

    def _eval_list(self, expr: List[Expr], env: Environment) -> Any:
        head = expr[0]

        # --- Special forms (not evaluated eagerly) ---
        if isinstance(head, str):
            sf = self._special_form(head, expr, env)
            if sf is not _NOT_SPECIAL:
                return sf

        # --- Normal function call: evaluate head then arguments ---
        func = self.eval(head, env)
        args = [self.eval(a, env) for a in expr[1:]]

        if isinstance(func, S7Lambda):
            return self._call_lambda(func, args)

        if callable(func):
            try:
                return func(*args)
            except TypeError as e:
                raise S7Error(f"Call error for {head!r}: {e}") from e

        raise S7Error(f"{head!r} is not callable (got {type(func).__name__})")

    # -- Lambda invocation --------------------------------------------------

    def _call_lambda(self, lam: S7Lambda, args: List[Any]) -> Any:
        child = lam.closure.child(dict(zip(lam.params, args)))
        result = None
        for body_expr in lam.body:
            result = self.eval(body_expr, child)
        return result

    # -- Helper: call a function that may be S7Lambda or Python callable ---

    def _apply(self, func: Any, args: List[Any]) -> Any:
        """Apply a function (S7Lambda or Python callable) to args."""
        if isinstance(func, S7Lambda):
            return self._call_lambda(func, args)
        if callable(func):
            return func(*args)
        raise S7Error(f"Not a function: {func!r}")

    # -- Special Forms ------------------------------------------------------

    _NOT_SPECIAL = object()  # Sentinel

    def _special_form(self, head: str, expr: List[Expr], env: Environment) -> Any:
        if head == "define":
            return self._sf_define(expr, env)
        if head == "set":
            return self._sf_set(expr, env)
        if head == "begin":
            return self._sf_begin(expr, env)
        if head == "if":
            return self._sf_if(expr, env)
        if head == "lambda":
            return self._sf_lambda(expr, env)
        if head == "let":
            return self._sf_let(expr, env)
        if head == "foreach":
            return self._sf_foreach(expr, env)
        if head == "map":
            return self._sf_map(expr, env)
        if head == "filter":
            return self._sf_filter(expr, env)
        if head == "list":
            return [self.eval(a, env) for a in expr[1:]]
        if head == "quote":
            if len(expr) != 2:
                raise S7Error("quote takes exactly 1 argument")
            return expr[1]
        if head == "do":
            return self._sf_begin(expr, env)  # alias
        return _NOT_SPECIAL

    # -- define -------------------------------------------------------------

    def _sf_define(self, expr: List[Expr], env: Environment) -> Any:
        # (define name value)
        # (define (name params...) body...)  -- shorthand lambda
        if len(expr) < 3:
            raise S7Error("define requires at least 2 arguments")
        target = expr[1]
        if isinstance(target, list):
            # Shorthand: (define (f x y) body...)
            name = target[0]
            params = [str(p) for p in target[1:]]
            body = expr[2:]
            env.set(str(name), S7Lambda(params, body, env))
            return None
        name = str(target)
        value = self.eval(expr[2], env)
        env.set(name, value)
        return value

    # -- set (update existing binding) --------------------------------------

    def _sf_set(self, expr: List[Expr], env: Environment) -> Any:
        if len(expr) != 3:
            raise S7Error("set requires exactly 2 arguments")
        name = str(expr[1])
        value = self.eval(expr[2], env)
        if not env.update_existing(name, value):
            env.set(name, value)
        return value

    # -- begin / do ---------------------------------------------------------

    def _sf_begin(self, expr: List[Expr], env: Environment) -> Any:
        result = None
        for sub in expr[1:]:
            result = self.eval(sub, env)
        return result

    # -- if / elif / else ---------------------------------------------------

    def _sf_if(self, expr: List[Expr], env: Environment) -> Any:
        """
        Supports:
          (if cond body)
          (if cond body (elif cond2 body2) ... (else body_else))
        """
        if len(expr) < 3:
            raise S7Error("if requires condition and body")

        cond = self.eval(expr[1], env)
        if cond:
            return self.eval(expr[2], env)

        # Walk remaining clauses looking for elif / else
        i = 3
        while i < len(expr):
            clause = expr[i]
            if isinstance(clause, list) and len(clause) >= 1:
                tag = clause[0]
                if tag == "elif" and len(clause) >= 3:
                    elif_cond = self.eval(clause[1], env)
                    if elif_cond:
                        return self.eval(clause[2], env)
                elif tag == "else" and len(clause) >= 2:
                    return self.eval(clause[1], env)
            i += 1

        return None

    # -- lambda -------------------------------------------------------------

    def _sf_lambda(self, expr: List[Expr], env: Environment) -> S7Lambda:
        # (lambda (params...) body...)
        if len(expr) < 3:
            raise S7Error("lambda requires params and body")
        params_raw = expr[1]
        if not isinstance(params_raw, list):
            raise S7Error("lambda params must be a list")
        params = [str(p) for p in params_raw]
        body = expr[2:]
        return S7Lambda(params, body, env)

    # -- let ----------------------------------------------------------------

    def _sf_let(self, expr: List[Expr], env: Environment) -> Any:
        # (let ((x 1) (y 2)) body...)
        if len(expr) < 3:
            raise S7Error("let requires bindings and body")
        bindings_raw = expr[1]
        if not isinstance(bindings_raw, list):
            raise S7Error("let bindings must be a list of pairs")
        child = env.child()
        for binding in bindings_raw:
            if not isinstance(binding, list) or len(binding) != 2:
                raise S7Error(f"Invalid let binding: {binding}")
            name = str(binding[0])
            val = self.eval(binding[1], child)
            child.set(name, val)
        result = None
        for body_expr in expr[2:]:
            result = self.eval(body_expr, child)
        return result

    # -- foreach ------------------------------------------------------------

    def _sf_foreach(self, expr: List[Expr], env: Environment) -> Any:
        # (foreach item_name list_expr body)
        if len(expr) < 4:
            raise S7Error("foreach requires item_name, list, and body")
        item_name = str(expr[1])
        collection = self.eval(expr[2], env)
        if not isinstance(collection, (list, tuple)):
            raise S7Error(f"foreach expects a list, got {type(collection).__name__}")
        body = expr[3:]
        result = None
        for item in collection:
            child = env.child({item_name: item})
            for body_expr in body:
                result = self.eval(body_expr, child)
        return result

    # -- map (special form to support S7Lambda) -----------------------------

    def _sf_map(self, expr: List[Expr], env: Environment) -> Any:
        # (map fn list)
        if len(expr) != 3:
            raise S7Error("map requires exactly 2 arguments: function and list")
        func = self.eval(expr[1], env)
        collection = self.eval(expr[2], env)
        if not isinstance(collection, list):
            raise S7Error(f"map: second argument must be a list, got {type(collection).__name__}")
        return [self._apply(func, [item]) for item in collection]

    # -- filter (special form to support S7Lambda) --------------------------

    def _sf_filter(self, expr: List[Expr], env: Environment) -> Any:
        # (filter fn list)
        if len(expr) != 3:
            raise S7Error("filter requires exactly 2 arguments: function and list")
        func = self.eval(expr[1], env)
        collection = self.eval(expr[2], env)
        if not isinstance(collection, list):
            raise S7Error(f"filter: second argument must be a list, got {type(collection).__name__}")
        return [item for item in collection if self._apply(func, [item])]


# Make sentinel accessible at module level for the isinstance guard
_NOT_SPECIAL = Interpreter._NOT_SPECIAL
