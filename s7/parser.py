"""
S7 Parser -- builds an AST from tokens and applies transformations.

Transformations:
  - Infix:  (A + B)      -> (+ A B)
  - Index:  mylist[0]     -> (index mylist 0)
  - Not:    !A / not A    -> (not A)

String literals are wrapped in S7String to distinguish them from symbols.
"""

from typing import Any, List, Union
from .lexer import (
    Token,
    TOK_LPAREN,
    TOK_RPAREN,
    TOK_LBRACKET,
    TOK_RBRACKET,
    TOK_STRING,
    TOK_NUMBER,
    TOK_SYMBOL,
    TOK_SLACK_ENTITY,
    TOK_CONTEXT,
    tokenize,
)


class S7String:
    """Wrapper for string literals so they aren't confused with symbol names."""
    __slots__ = ("value",)

    def __init__(self, value: str):
        self.value = value

    def __repr__(self) -> str:
        return f'S7String({self.value!r})'

    def __eq__(self, other: object) -> bool:
        if isinstance(other, S7String):
            return self.value == other.value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(("S7String", self.value))


class S7SlackEntity:
    """Wrapper for Slack blue-pill entities (<@U...>, <#C...>)."""
    __slots__ = ("raw",)

    def __init__(self, raw: str):
        self.raw = raw

    def __repr__(self) -> str:
        return f'S7SlackEntity({self.raw!r})'

    def __eq__(self, other: object) -> bool:
        if isinstance(other, S7SlackEntity):
            return self.raw == other.raw
        return NotImplemented

    def __hash__(self) -> int:
        return hash(("S7SlackEntity", self.raw))

    def __str__(self) -> str:
        return self.raw


# AST node types
Atom = Union[str, int, float, bool, None, S7String, S7SlackEntity]
Expr = Union[Atom, List["Expr"]]

INFIX_OPS = {"+", "-", "*", "/", ">", "<", ">=", "<=", "!=", "&&", "||", "and", "or"}


class ParseError(Exception):
    pass


# ---------------------------------------------------------------------------
# Recursive-descent parser
# ---------------------------------------------------------------------------

class Parser:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Token | None:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def advance(self) -> Token:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def expect(self, tok_type: str) -> Token:
        tok = self.peek()
        if tok is None or tok[0] != tok_type:
            raise ParseError(
                f"Expected {tok_type}, got {tok[0] if tok else 'EOF'}"
            )
        return self.advance()

    # -- Entry point --------------------------------------------------------

    def parse_program(self) -> List[Expr]:
        """Parse all top-level expressions."""
        exprs: List[Expr] = []
        while self.peek() is not None:
            exprs.append(self.parse_expr())
        return exprs

    # -- Expression (top-level, supports prefix not/!) ----------------------

    def parse_expr(self) -> Expr:
        tok = self.peek()
        if tok is None:
            raise ParseError("Unexpected end of input")

        # Prefix not: !expr (works everywhere)
        if tok[0] == TOK_SYMBOL and tok[1] == "!":
            self.advance()
            operand = self.parse_element()
            return ["not", operand]

        # 'not' keyword as prefix (only at top level / outside s-expr head)
        if tok[0] == TOK_SYMBOL and tok[1] == "not":
            # Peek ahead: if next token after 'not' is NOT '(' or is a simple
            # atom, treat as prefix.  But since this is called from top-level
            # and from parse_element for ! only, this branch handles standalone
            # `not x` usage.
            self.advance()
            operand = self.parse_expr()
            return ["not", operand]

        if tok[0] == TOK_LPAREN:
            result = self.parse_sexp()
        else:
            result = self.parse_atom()

        # Check for postfix indexing: expr[index]
        while self.peek() and self.peek()[0] == TOK_LBRACKET:
            self.advance()  # consume [
            idx = self.parse_element()
            self.expect(TOK_RBRACKET)
            result = ["index", result, idx]

        return result

    # -- Element (inside s-expressions, no 'not' keyword prefix) ------------

    def parse_element(self) -> Expr:
        """Parse an expression element inside an s-expression.
        Only ! prefix is supported here (not the 'not' keyword),
        so that (not x) is parsed as a normal function call."""
        tok = self.peek()
        if tok is None:
            raise ParseError("Unexpected end of input")

        # Prefix !: works inside s-expressions
        if tok[0] == TOK_SYMBOL and tok[1] == "!":
            self.advance()
            operand = self.parse_element()
            return ["not", operand]

        if tok[0] == TOK_LPAREN:
            result = self.parse_sexp()
        else:
            result = self.parse_atom()

        # Check for postfix indexing: expr[index]
        while self.peek() and self.peek()[0] == TOK_LBRACKET:
            self.advance()  # consume [
            idx = self.parse_element()
            self.expect(TOK_RBRACKET)
            result = ["index", result, idx]

        return result

    # -- S-Expression -------------------------------------------------------

    def parse_sexp(self) -> Expr:
        """Parse ( ... ) with infix detection.  Uses parse_element to avoid
        the 'not'-keyword prefix stealing tokens."""
        self.expect(TOK_LPAREN)
        elements: List[Expr] = []
        while self.peek() and self.peek()[0] != TOK_RPAREN:
            elements.append(self.parse_element())
        self.expect(TOK_RPAREN)

        # Infix transform: if the second element is an infix operator
        if len(elements) >= 3 and isinstance(elements[1], str) and elements[1] in INFIX_OPS:
            op = elements[1]
            left = elements[0]
            right = elements[2]
            # Handle chained infix: (a + b + c) -> (+ (+ a b) c)
            result: list = [op, left, right]
            i = 3
            while i < len(elements):
                if i + 1 < len(elements) and isinstance(elements[i], str) and elements[i] in INFIX_OPS:
                    result = [elements[i], result, elements[i + 1]]
                    i += 2
                else:
                    result.append(elements[i])
                    i += 1
            return result

        # Function call style: (func arg1 arg2 ...)
        return elements

    # -- Atoms --------------------------------------------------------------

    def parse_atom(self) -> Atom:
        tok = self.advance()
        if tok[0] == TOK_NUMBER:
            if "." in tok[1]:
                return float(tok[1])
            return int(tok[1])
        if tok[0] == TOK_STRING:
            return S7String(tok[1])
        if tok[0] == TOK_SLACK_ENTITY:
            return S7SlackEntity(tok[1])
        if tok[0] == TOK_CONTEXT:
            return tok[1]  # "#!" or "@!"
        if tok[0] == TOK_SYMBOL:
            if tok[1] == "true":
                return True
            if tok[1] == "false":
                return False
            if tok[1] == "nil":
                return None
            return tok[1]  # identifier / operator
        raise ParseError(f"Unexpected token: {tok}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse(source: str) -> List[Expr]:
    """Tokenize and parse an S7 source string into a list of AST expressions."""
    tokens = tokenize(source)
    parser = Parser(tokens)
    return parser.parse_program()
