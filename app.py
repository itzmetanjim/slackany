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
from s7.storage import S7Store

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
STORAGE_DB_PATH = os.environ.get("S7_STORAGE_DB_PATH", "s7_storage.db")
DEFAULT_STEP_LIMIT = 50000

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("s7")

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = App(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)
macro_store = MacroStore(DB_PATH)
storage = S7Store(STORAGE_DB_PATH)

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
    macro_store_ref=None,
    storage_ref=None,
    trigger_id: str | None = None,
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
        storage=storage_ref,
        trigger_id=trigger_id,
    )
    if extra_bindings:
        for k, v in extra_bindings.items():
            env.set(k, v)

    interp = Interpreter(
        env,
        step_limit=step_limit,
        macro_store=macro_store_ref,
        caller_user_id=user_id,
    )
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
            _handle_help(rest, respond)
            return

        # --- Mode: store ---
        if lower_first == "store":
            _handle_store(rest, user_id, respond)
            return

        # --- Mode: direct execution ---
        if first == "":
            if not rest:
                respond("Usage: `/s7 help` for available commands.")
                return
            executed_code = rest
            result, echoes = execute_s7(
                rest, client, channel_id, user_id,
                macro_store_ref=macro_store,
                storage_ref=storage,
            )
            _send_result(respond, result, echoes)
            return

        # --- Mode: macro execution ---
        macro_data = macro_store.get_with_author(first)
        if macro_data is not None:
            code, author = macro_data
            # User-dependent macros: only the author can execute their macro
            if author != user_id:
                respond(
                    f":lock: Macro `{first}` belongs to <@{author}>. "
                    "You can only execute macros you created."
                )
                return
            executed_code = f"; macro: {first}\n{code}"
            args = parse_args_for_macro(rest)
            result, echoes = execute_s7(
                code, client, channel_id, user_id,
                extra_bindings={"args": args},
                macro_store_ref=macro_store,
                storage_ref=storage,
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


def _handle_store(rest: str, user_id: str, respond):
    """Handle /s7 store [clear]."""
    rest = rest.strip().lower()
    if rest == "clear":
        count = storage.clear_user(user_id)
        respond(f":white_check_mark: Cleared {count} stored key(s).")
        return
    
    keys = storage.list_keys(user_id)
    if not keys:
        respond("No stored data. Use `(store \"key\" value)` to save data.")
        return
    lines = ["*Your Stored Keys:*"]
    for key, updated in keys:
        lines.append(f"  `{key}` (updated {updated})")
    respond("\n".join(lines))


def _handle_help(rest: str, respond):
    """Handle /s7 help with multi-page manpage-style tutorial."""
    text = rest.strip()
    
    if not text:
        # Default: show page 1
        page_content = _get_help_page(1)
        respond(page_content)
        return
    
    # Try to parse page number
    if text.isdigit():
        page_num = int(text)
    else:
        # Accept page numbers like: /s7 help 2
        words = text.split()
        if len(words) == 1 and words[0].isdigit():
            page_num = int(words[0])
        else:
            respond(f"Unknown help topic. Use `/s7 help` to start the tutorial.")
            return
    
    page_content = _get_help_page(page_num)
    if page_content:
        respond(page_content)
    else:
        respond(f"Help page {page_num} not found. Use `/s7 help` to start the tutorial.")


def _get_help_page(page_num: int) -> str:
    """Return help page content for the given page number."""
    
    if page_num == 1:
        return (
            "*SlackAny (s7) — Lisp-based Slack Automation*\n\n"
            "=== Welcome to S7 ===\n\n"
            "S7 (pronounced 'sea') is a Lisp-based scripting language for Slack automation.\n"
            "It lets you write macros and automate tasks directly in Slack channels.\n\n"
            "=== Quick Start ===\n\n"
            "To get started:\n"
            "1. Execute S7 code directly: `/s7 (!echo \"Hello\")`\n"
            "2. Create saved macros: `/s7 set hello (!echo \"Hello World\")`\n"
            "3. Execute macros: `/s7 hello`\n"
            "4. View all macros: `/s7 list`\n\n"
            "=== Getting Help ===\n\n"
            "This is page 1. Use `/s7 help 2` to see Built-in Functions.\n"
            "Use `/s7 help 3` to see Storage operations.\n"
            "Use `/s7 help 4` to see Interactive UI.\n"
            "Use `/s7 help 5` to see Control Flow.\n"
            "Use `/s7 help 6` to see Context and Operators.\n\n"
            "=== Examples ===\n\n"
            "Here's a simple example that sends a message to #general:\n"
            "```\n(send #general \"Hello from S7!\")\n```\n\n"
            "You can also create and use macros for reusable automation.\n\n"
            "Use `/s7 help` to start over, or `/s7 help 2` for built-in functions."
        )
    
    elif page_num == 2:
        return (
            "*Built-in Functions (Page 2 of 6)*\n\n"
            "=== Core Communication ===\n"
            "`echo message...` — Log/debug message (visible to bot only)\n"
            "`send target message...` — Send message to user/channel\n"
            "  Target: <@U123> (user), #C123 (channel), or #! (current channel)\n"
            "`sendi target text btn1 macro1 btn2 macro2 ...` — Send with buttons\n"
            "`showui title callback field1 field2 ...` — Open modal form\n\n"
            "=== User Management ===\n"
            "`members #channel` — List all members in a channel\n"
            "`addto @user1 @user2 #channel` — Add users to a channel\n"
            "`kick #channel @user1 @user2` — Remove users from a channel (requires power user)\n"
            "`email @user` — Get email of a Slack user\n"
            "`profile @user [\"display_name|real_name|title|status_text\"]` — Get user profile data\n\n"
            "=== Data Processing ===\n"
            "`str value` — Convert to string representation\n"
            "`strjoin separator list...` — Join list items with separator\n"
            "`concat value1 value2 ...` — Concatenate strings\n"
            "`length collection` — Get size of list or string\n"
            "`index list index` — Get item at position\n"
            "`flatten list...` — Flatten nested lists\n"
            "`append list item...` — Add items to list\n"
            "`range start end` — Create list of numbers\n"
            "`filter function list` — Keep items matching condition\n"
            "`map function list` — Apply function to all items\n"
            "`abs number` — Absolute value\n"
            "`min value1 value2 ...` — Minimum of values\n"
            "`max value1 value2 ...` — Maximum of values\n"
            "`mod dividend divisor` — Remainder after division\n\n"
            "---\n\n"
            "Use `/s7 help` for Page 1 or `/s7 help 3` for Storage."
        )
    
    elif page_num == 3:
        return (
            "*Storage Operations (Page 3 of 6)*\n\n"
            "=== Persistent Storage ===\n"
            "S7 provides per-user key-value storage for your automation.\n\n"
            "=== Save Data ===\n"
            "`(store \"key\" value)` — Save a value for the current user\n"
            "  Example: `(store \"score\" 42)`\n"
            "  Values can be strings, numbers, or lists.\n\n"
            "=== Read Data ===\n"
            "`(read \"key\")` — Read a value from storage\n"
            "  Returns `nil` if the key doesn't exist.\n"
            "  Example: `(read \"score\")` might return 42.\n\n"
            "=== Delete Data ===\n"
            "`(delete \"key\")` — Delete a key from storage\n"
            "  Returns `true` if the key existed, `false` otherwise.\n\n"
            "=== View Your Storage ===\n"
            "`/s7 store` — List all your stored keys and values\n"
            "`/s7 store clear` — Clear all your stored data\n\n"
            "=== Storage in Macros ===\n"
            "Stored values persist between macro executions and are scoped to users.\n"
            "For example: `(store \"last_message\" text)` then `(read \"last_message\")`.\n\n"
            "---\n\n"
            "Use `/s7 help` for Page 1 or `/s7 help 4` for Interactive UI."
        )
    
    elif page_num == 4:
        return (
            "*Interactive UI (Page 4 of 6)*\n\n"
            "=== Buttons & Modals ===\n"
            "S7 supports interactive Slack UI elements to build rich interfaces.\n\n"
            "=== Interactive Buttons ===\n"
            "`sendi #channel \"Choose an option\" \"Yes:yes_macro\" \"No:no_macro\"`\n"
            "Creates a message with clickable buttons. Each button triggers a macro.\n"
            "Button format: `\"label:macro_name\"` or just `\"label\"` (calls macro with same name).\n\n"
            "=== Modal Forms ===\n"
            "`showui \"Form Title\" \"callback_macro\" \"Field 1\" \"Field 2\" ...`\n"
            "Opens a modal dialog with text inputs.\n"
            "When submitted, the callback_macro runs with field values as arguments.\n"
            "Requires trigger_id (automatically handled from button clicks).\n\n"
            "=== Use Cases ===\n"
            "• Feedback forms with ratings and comments\n"
            "• Configuration dialogs for bots\n"
            "• Survey tools\n"
            "• Approval workflows\n\n"
            "=== Important Notes ===\n"
            "• Modals require an interactive trigger_id\n"
            "• Callback macros must belong to the user who opened the modal\n"
            "• Field values are passed in order to the callback macro\n\n"
            "---\n\n"
            "Use `/s7 help` for Page 1 or `/s7 help 5` for Control Flow."
        )
    
    elif page_num == 5:
        return (
            "*Control Flow (Page 5 of 6)*\n\n"
            "=== Branching Logic ===\n"
            "`if condition body` — Execute body if condition is true\n"
            "`elif condition body` — Else-if branch\n"
            "`else body` — Fallback branch\n"
            "  Example: `(if (= x 5) (!echo \"It's five!\"))`\n\n"
            "=== Iteration ===\n"
            "`foreach item_name list body` — Loop over each item\n"
            "  Example: `(foreach user (#C123 members) (!send @user \"Welcome!\"))`\n\n"
            "=== Definition & Lambda ===\n"
            "`define (name params...) body...` — Define named function\n"
            "  Example: `(define (add a b) (+ a b))`\n"
            "`lambda (params...) body...` — Anonymous function\n"
            "`let ((x 10) (y 20)) body...` — Bind local variables\n\n"
            "=== Function Calls ===\n"
            "`(return)` — Exit current function\n"
            "`(return value)` — Return a value from function\n"
            "`(error \"message\")` — Throw an error\n"
            "`(macro \"name\" body)` — Define a local macro\n"
            "`(call \"macro_name\" args...)` — Execute a stored macro\n\n"
            "=== Conditionals & Operators ===\n"
            "Logical operators: `and`, `or`, `not`\n"
            "Comparisons: `=`, `==`, `!=`, `>`, `<`, `>=`, `<=`\n"
            "Boolean literals: `true`, `false`, `nil`\n"
            "Boolean operators: `and`, `or`, `&&`, `||`, `not`\n\n"
            "---\n\n"
            "Use `/s7 help` for Page 1 or `/s7 help 6` for Context."
        )
    
    elif page_num == 6:
        return (
            "*Context & Operators (Page 6 of 6)*\n\n"
            "=== Context Variables ===\n"
            "`#!` — Current channel ID where command was invoked\n"
            "`@!` — Current user ID who invoked the command\n"
            "These are available automatically in all S7 code.\n\n"
            "=== Data Types ===\n"
            "* Numbers: `1`, `-5`, `3.14`\n"
            "* Strings: `\"hello\"`, `\"line1\\nline2\"`\n"
            "* Lists: `[1, 2, 3]`, `(#C123 members)`\n"
            "* Booleans: `true`, `false`\n"
            "* Null: `nil`\n\n"
            "=== Operators ===\n"
            "* Arithmetic: `+`, `-`, `*`, `/`\n"
            "* Comparison: `=`, `==`, `!=`, `>`, `<`, `>=`, `<=`\n"
            "* Logic: `and`, `or`, `not`\n"
            "* Boolean: `&&`, `||`\n\n"
            "=== Lists ===\n"
            "Lists are S7's primary data structure.\n"
            "`[1, 2, 3]` — Create a list\n"
            "`list[0]` — Access by index (0-based)\n"
            "`(length [1, 2, 3])` — Get length\n"
            "`(append [1] 2)` — Add item\n"
            "`(map (+ x 1) [1, 2, 3])` — Apply function\n"
            "`(filter (isEven x) [1, 2, 3, 4])` — Filter items\n"
            "`(flatten [1, [2, [3]]])` — Flatten nested lists\n\n"
            "=== Special Forms ===\n"
            "S7 provides special forms for control flow and definitions.\n"
            "See help page 5 for details.\n\n"
            "=== Variables ===\n"
            "Variables are set using `define` or `let`.\n"
            "Variables can be updated with `set`.\n\n"
            "---\n\n"
            "Tutorial complete! Use `/s7 help` to restart.\n"
            "These pages cover the essential S7 concepts for Slack automation.\n"
        )
    
    else:
        return None


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
# Interactive button handler
# ---------------------------------------------------------------------------

@app.action(re.compile(r"s7_button_.*"))
def handle_s7_button(ack, body, client, respond):
    """Handle button clicks from sendi messages."""
    ack()
    
    action = body.get("actions", [{}])[0]
    action_id = action.get("action_id", "")
    macro_name = action.get("value", "")
    
    # The clicking user runs the macro (user-dependent)
    user_id = body.get("user", {}).get("id", "")
    channel_id = body.get("channel", {}).get("id", "")
    trigger_id = body.get("trigger_id", None)
    
    if not macro_name:
        return
    
    # Look up the macro
    macro_data = macro_store.get_with_author(macro_name)
    if macro_data is None:
        client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=f":x: Macro `{macro_name}` not found.",
        )
        return
    
    code, author = macro_data
    
    # User-dependent check: clicking user must be the author
    if author != user_id:
        client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=f":lock: Macro `{macro_name}` belongs to <@{author}>. You can only execute macros you created.",
        )
        return
    
    try:
        # Execute the macro as the clicking user
        result, echoes = execute_s7(
            code, client, channel_id, user_id,
            extra_bindings={"args": []},
            macro_store_ref=macro_store,
            storage_ref=storage,
            trigger_id=trigger_id,
        )
        
        # Send result as ephemeral to the clicking user
        parts: List[str] = []
        if echoes:
            parts.extend(echoes)
        if result is not None and not echoes:
            parts.append(f"Result: `{result}`")
        if parts:
            client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="\n".join(parts),
            )
    except StepLimitExceeded as e:
        client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=f":warning: *Execution killed:* {e}",
        )
    except S7Error as e:
        client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=f":x: *S7 Error:* {e}",
        )
    except Exception as e:
        logger.error("Unhandled error in button handler: %s", traceback.format_exc())
        client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=f":x: *Internal error:* {e}",
        )


# ---------------------------------------------------------------------------
# Modal submission handler
# ---------------------------------------------------------------------------

@app.view("s7_modal_submit")
def handle_modal_submit(ack, body, client, view):
    """Handle form submissions from showui modals."""
    ack()
    
    import json
    
    # Extract metadata
    try:
        metadata = json.loads(view.get("private_metadata", "{}"))
    except json.JSONDecodeError:
        metadata = {}
    
    callback_macro = metadata.get("callback_macro", "")
    channel_id = metadata.get("channel_id", "")
    original_user_id = metadata.get("user_id", "")
    field_count = metadata.get("field_count", 0)
    
    # The submitting user
    user_id = body.get("user", {}).get("id", "")
    
    # User-dependent check: submitter must be the original user who opened the modal
    if original_user_id != user_id:
        # This shouldn't normally happen, but just in case
        return
    
    if not callback_macro:
        return
    
    # Extract field values
    values = view.get("state", {}).get("values", {})
    args = []
    for i in range(field_count):
        block_id = f"field_{i}"
        action_id = f"input_{i}"
        if block_id in values and action_id in values[block_id]:
            args.append(values[block_id][action_id].get("value", ""))
        else:
            args.append("")
    
    # Look up and execute the callback macro
    macro_data = macro_store.get_with_author(callback_macro)
    if macro_data is None:
        if channel_id:
            client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f":x: Callback macro `{callback_macro}` not found.",
            )
        return
    
    code, author = macro_data
    
    # User-dependent check
    if author != user_id:
        if channel_id:
            client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f":lock: Macro `{callback_macro}` belongs to <@{author}>.",
            )
        return
    
    try:
        result, echoes = execute_s7(
            code, client, channel_id, user_id,
            extra_bindings={"args": args},
            macro_store_ref=macro_store,
            storage_ref=storage,
        )
        
        # Send result as ephemeral
        parts: List[str] = []
        if echoes:
            parts.extend(echoes)
        if result is not None and not echoes:
            parts.append(f"Result: `{result}`")
        if parts and channel_id:
            client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text="\n".join(parts),
            )
    except StepLimitExceeded as e:
        if channel_id:
            client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f":warning: *Execution killed:* {e}",
            )
    except S7Error as e:
        if channel_id:
            client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f":x: *S7 Error:* {e}",
            )
    except Exception as e:
        logger.error("Unhandled error in modal handler: %s", traceback.format_exc())
        if channel_id:
            client.chat_postEphemeral(
                channel=channel_id,
                user=user_id,
                text=f":x: *Internal error:* {e}",
            )

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
