"""
SlackAny (s7) -- Slack Bolt application entry point.

Handles the /s7 (and /slackany) slash command with routing logic:
  1. /s7 set <name>\n<code>      -- store a macro
  2. /s7 remove <name>            -- delete a macro
  3. /s7 list                     -- list all macros
  4. /s7 <name> <args...>         -- execute a stored macro
  5. /s7\n<code>                   -- direct execution
"""

from __future__ import annotations

import logging
import os
import re
import traceback
from typing import List

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from s7.environment import build_environment
from s7.interpreter import Interpreter, S7Error, StepLimitExceeded
from s7.macros import MacroStore
from s7.parser import parse

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN", "")
POWER_USERS: List[str] = [
    u.strip() for u in os.environ.get("POWER_USERS", "").split(",") if u.strip()
]
DB_PATH = os.environ.get("S7_DB_PATH", "s7_macros.db")
DEFAULT_STEP_LIMIT = 50000

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("s7")

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = App(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)
macro_store = MacroStore(DB_PATH)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_command_text(text: str) -> tuple[str, str]:
    """
    Split slash command text into the first word and the rest.
    Returns (first_word, remainder).  If text starts with a newline,
    first_word will be empty (direct execution mode).
    """
    text = text.strip()
    if not text:
        return ("", "")
    # If the text starts with '(' it's direct code execution
    if text.startswith("("):
        return ("", text)
    parts = text.split(None, 1)
    first = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    return (first, rest)


def parse_args_for_macro(raw: str) -> List[str]:
    """
    Parse the remaining text after a macro name into an args list.
    Slack entities are preserved as-is.  Other tokens are split on whitespace.
    """
    tokens: List[str] = []
    pattern = re.compile(r"(<[@#][^>]+>)")
    parts = pattern.split(raw)
    for part in parts:
        if pattern.match(part):
            tokens.append(part)
        else:
            tokens.extend(part.split())
    return tokens


def execute_s7(
    code: str,
    client,
    channel_id: str,
    user_id: str,
    extra_bindings: dict | None = None,
) -> tuple[str | None, List[str]]:
    """
    Execute S7 code and return (result, echo_lines).
    Raises on error.
    """
    echo_lines: List[str] = []
    step_limit = DEFAULT_STEP_LIMIT
    if user_id in POWER_USERS:
        step_limit = 500000  # 10x for power users

    env = build_environment(
        client=client,
        channel_id=channel_id,
        user_id=user_id,
        power_users=POWER_USERS,
        echo_collector=echo_lines,
    )
    if extra_bindings:
        for k, v in extra_bindings.items():
            env.set(k, v)

    interp = Interpreter(env, step_limit=step_limit)
    result = interp.run(code)
    return result, echo_lines


# ---------------------------------------------------------------------------
# Slash command handler
# ---------------------------------------------------------------------------


def handle_s7_command(ack, body, client, respond):
    """Main handler for /s7 and /slackany."""
    ack()  # Acknowledge within 3 seconds

    text: str = body.get("text", "")
    channel_id: str = body.get("channel_id", "")
    user_id: str = body.get("user_id", "")
    executed_code: str = ""

    try:
        first, rest = parse_command_text(text)
        lower_first = first.lower()

        # --- Mode: set ---
        if lower_first == "set":
            _handle_set(rest, user_id, respond)
            return

        # --- Mode: remove ---
        if lower_first == "remove":
            _handle_remove(rest, user_id, respond)
            return

        # --- Mode: list ---
        if lower_first == "list":
            _handle_list(respond)
            return

        # --- Mode: help ---
        if lower_first == "help":
            _handle_help(respond)
            return

        # --- Mode: direct execution ---
        if first == "":
            if not rest:
                respond("Usage: `/s7 help` for available commands.")
                return
            executed_code = rest
            result, echoes = execute_s7(
                rest, client, channel_id, user_id
            )
            _send_result(respond, result, echoes)
            return

        # --- Mode: macro execution ---
        code = macro_store.get(first)
        if code is not None:
            executed_code = f"; macro: {first}\n{code}"
            args = parse_args_for_macro(rest)
            result, echoes = execute_s7(
                code, client, channel_id, user_id,
                extra_bindings={"args": args},
            )
            _send_result(respond, result, echoes)
            return

        # --- Unknown ---
        respond(f"Unknown macro `{first}`. Use `/s7 list` to see available macros.")

    except StepLimitExceeded as e:
        respond(f":warning: *Execution killed:* {e}")
        respond(f"```\n{executed_code}\n```")
    except S7Error as e:
        respond(f":x: *S7 Error:* {e}")
        respond(f"```\n{executed_code}\n```")
    except Exception as e:
        logger.error("Unhandled error in /s7: %s", traceback.format_exc())
        respond(f":x: *Internal error:* {e}")
        respond(f"```\n{executed_code}\n```")


# -- Sub-handlers -----------------------------------------------------------


def _handle_set(rest: str, user_id: str, respond):
    """Handle /s7 set <name>\n<code>."""
    parts = rest.split("\n", 1)
    name = parts[0].strip()
    if not name:
        respond("Usage: `/s7 set <name>`\n```<code>```")
        return
    code = parts[1].strip() if len(parts) > 1 else ""
    if not code:
        respond(f"No code provided for macro `{name}`.")
        return

    # Validate that the code parses
    try:
        parse(code)
    except Exception as e:
        respond(f":x: *Parse error:* {e}")
        return

    macro_store.set(name, code, user_id)
    respond(f":white_check_mark: Macro `{name}` saved.")


def _handle_remove(rest: str, user_id: str, respond):
    """Handle /s7 remove <name>."""
    name = rest.strip()
    if not name:
        respond("Usage: `/s7 remove <name>`")
        return
    if macro_store.remove(name):
        respond(f":white_check_mark: Macro `{name}` removed.")
    else:
        respond(f"Macro `{name}` not found.")


def _handle_list(respond):
    """Handle /s7 list."""
    macros = macro_store.list_all()
    if not macros:
        respond("No macros defined yet. Use `/s7 set <name>` to create one.")
        return
    lines = ["*Saved Macros:*"]
    for name, author, updated in macros:
        lines.append(f"  `{name}` — by <@{author}> (updated {updated})")
    respond("\n".join(lines))


def _handle_help(respond):
    """Handle /s7 help."""
    respond(
        "*SlackAny (s7) — Lisp-based Slack Automation*\n\n"
        "*Commands:*\n"
        "• `/s7 (code...)` — Execute S7 code directly\n"
        "• `/s7 set <name>\\n<code>` — Save a named macro\n"
        "• `/s7 remove <name>` — Delete a macro\n"
        "• `/s7 list` — Show all saved macros\n"
        "• `/s7 <name> <args...>` — Execute a saved macro\n\n"
        "*Built-in Functions:*\n"
        "`echo`, `send`, `send2`, `members`, `addto`, `kick`, "
        "`str`, `strjoin`, `concat`, `length`, `index`, `flatten`, "
        "`append`, `range`, `filter`, `map`, `abs`, `min`, `max`, `mod`\n\n"
        "*Control Flow:*\n"
        "`if`/`elif`/`else`, `foreach`, `begin`, `define`, `lambda`, `let`\n\n"
        "*Context:*\n"
        "`#!` → current channel, `@!` → current user\n\n"
        "*Operators:*\n"
        "`+` `-` `*` `/` `>` `<` `>=` `<=` `!=` `==` `and` `or` `not`"
    )


def _send_result(respond, result, echoes: List[str]):
    """Format and send execution results back to the user as ephemeral."""
    parts: List[str] = []
    if echoes:
        parts.extend(echoes)
    if result is not None and not echoes:
        parts.append(f"Result: `{result}`")
    if parts:
        respond("\n".join(parts))
    # Silent on success with no output


# ---------------------------------------------------------------------------
# Register command handlers
# ---------------------------------------------------------------------------

app.command("/s7")(handle_s7_command)
app.command("/slackany")(handle_s7_command)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if SLACK_APP_TOKEN:
        logger.info("Starting S7 in Socket Mode...")
        handler = SocketModeHandler(app, SLACK_APP_TOKEN)
        handler.start()
    else:
        logger.info("Starting S7 HTTP server on port 3000...")
        app.start(port=int(os.environ.get("PORT", 3000)))
