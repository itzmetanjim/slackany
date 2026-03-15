"""
S7 Lexer -- tokenizes Slack-formatted S7-Lisp source code.

Handles:
  - Slack "blue pill" entities: <@U...>, <#C...|name>
  - Strings: "double quoted"
  - Numbers: integers and floats
  - Context atoms: #! and @!
  - Parentheses, brackets
  - Symbols / operators
"""

import re
from typing import List, Tuple

# Token types
TOK_LPAREN = "LPAREN"
TOK_RPAREN = "RPAREN"
TOK_LBRACKET = "LBRACKET"
TOK_RBRACKET = "RBRACKET"
TOK_STRING = "STRING"
TOK_NUMBER = "NUMBER"
TOK_SYMBOL = "SYMBOL"
TOK_SLACK_ENTITY = "SLACK_ENTITY"
TOK_CONTEXT = "CONTEXT"

Token = Tuple[str, str]  # (type, value)

# Patterns tried in order
_PATTERNS: List[Tuple[str, str]] = [
    (TOK_SLACK_ENTITY, r"<@U[A-Z0-9]+\|[^>]*>"),     # User mention with label
    (TOK_SLACK_ENTITY, r"<@U[A-Z0-9]+>"),           # User mention
    (TOK_SLACK_ENTITY, r"<#C[A-Z0-9]+\|[^>]*>"),    # Channel mention with label
    (TOK_SLACK_ENTITY, r"<#C[A-Z0-9]+>"),            # Channel mention without label
    (TOK_STRING, r'"(?:[^"\\]|\\.)*"'),               # Double-quoted string
    (TOK_NUMBER, r"-?\d+\.\d+"),                      # Float (before int)
    (TOK_NUMBER, r"-?\d+"),                            # Integer
    (TOK_CONTEXT, r"#!"),                              # Current channel
    (TOK_CONTEXT, r"@!"),                              # Current user
    (TOK_LPAREN, r"\("),
    (TOK_RPAREN, r"\)"),
    (TOK_LBRACKET, r"\["),
    (TOK_RBRACKET, r"\]"),
    # Operators (multi-char first)
    (TOK_SYMBOL, r">=|<=|!=|&&|\|\|"),
    (TOK_SYMBOL, r"[+\-*/><!=]"),
    # General symbols (identifiers, keywords)
    (TOK_SYMBOL, r"[A-Za-z_][A-Za-z0-9_]*"),
]

_MASTER_RE = re.compile(
    "|".join(f"(?P<T{i}>{pat})" for i, (_, pat) in enumerate(_PATTERNS))
)


def tokenize(source: str) -> List[Token]:
    """Tokenize an S7 source string into a list of (type, value) tokens."""
    tokens: List[Token] = []
    pos = 0
    while pos < len(source):
        # Skip whitespace and commas (commas are whitespace in Lisp)
        if source[pos] in " \t\r\n,":
            pos += 1
            continue
        # Try comment (;; to end of line)
        if source[pos] == ";" and pos + 1 < len(source) and source[pos + 1] == ";":
            while pos < len(source) and source[pos] != "\n":
                pos += 1
            continue
        m = _MASTER_RE.match(source, pos)
        if not m:
            raise SyntaxError(
                f"S7 Lexer: unexpected character {source[pos]!r} at position {pos}"
            )
        for i, (tok_type, _) in enumerate(_PATTERNS):
            val = m.group(f"T{i}")
            if val is not None:
                # Strip quotes from strings
                if tok_type == TOK_STRING:
                    val = val[1:-1].replace('\\"', '"').replace("\\\\", "\\")
                tokens.append((tok_type, val))
                break
        pos = m.end()
    return tokens
