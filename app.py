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
from pathlib import Path
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
DB_PATH = os.environ.get("S7_DB_PATH", "s7_macros.db")
STORAGE_DB_PATH = os.environ.get("S7_STORAGE_DB_PATH", "s7_storage.db")
MAX_STEP_LIMIT = 500000
HELP_DIR = Path(__file__).resolve().parent / "help_pages"

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
    step_limit = MAX_STEP_LIMIT
    local_macros_ref: dict = {}

    env = build_environment(
        client=client,
        channel_id=channel_id,
        user_id=user_id,
        echo_collector=echo_lines,
        storage=storage_ref,
        trigger_id=trigger_id,
        local_macros_ref=local_macros_ref,
    )
    if extra_bindings:
        for k, v in extra_bindings.items():
            env.set(k, v)

    interp = Interpreter(
        env,
        step_limit=step_limit,
        macro_store=macro_store_ref,
        caller_user_id=user_id,
        local_macros_ref=local_macros_ref,
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
    if page_num < 1:
        return None

    page_path = HELP_DIR / str(page_num)
    if not page_path.exists():
        return None

    content = page_path.read_text(encoding="utf-8").strip()
    if not content:
        logger.error("Help page file is empty: %s", page_path)
        return f":warning: Help page {page_num} is empty."

    return content


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

@app.action(re.compile(r"s7_(button|inline)_.*"))
def handle_s7_button(ack, body, client, respond):
    """Handle button clicks from sendi messages."""
    ack()
    
    action = body.get("actions", [{}])[0]
    action_id = action.get("action_id", "")
    
    # The clicking user runs the macro (user-dependent)
    user_id = body.get("user", {}).get("id", "")
    channel_id = body.get("channel", {}).get("id", "")
    trigger_id = body.get("trigger_id", None)
    
    if not action.get("value", ""):
        return
    
    # --- Inline (local) macro path ---
    if action_id.startswith("s7_inline_"):
        code = action.get("value", "")
        try:
            result, echoes = execute_s7(
                code, client, channel_id, user_id,
                extra_bindings={"args": []},
                macro_store_ref=macro_store,
                storage_ref=storage,
                trigger_id=trigger_id,
            )
            parts: List[str] = []
            if echoes:
                parts.extend(echoes)
            if result is not None and not echoes:
                parts.append(f"Result: `{result}`")
            if parts:
                client.chat_postEphemeral(
                    channel=channel_id, user=user_id, text="\n".join(parts),
                )
        except StepLimitExceeded as e:
            client.chat_postEphemeral(
                channel=channel_id, user=user_id,
                text=f":warning: *Execution killed:* {e}",
            )
        except S7Error as e:
            client.chat_postEphemeral(
                channel=channel_id, user=user_id,
                text=f":x: *S7 Error:* {e}",
            )
        except Exception as e:
            logger.error("Unhandled error in inline button handler: %s", traceback.format_exc())
            client.chat_postEphemeral(
                channel=channel_id, user=user_id,
                text=f":x: *Internal error:* {e}",
            )
        return
    
    # --- Stored macro path ---
    macro_name = action.get("value", "")
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
    callback_inline = metadata.get("callback_inline", None)
    is_inline = metadata.get("is_inline", False)
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
    
    # Execute callback - inline (local macro) or stored macro
    if is_inline and callback_inline:
        # Inline code from local macro
        code = callback_inline
    else:
        # Look up stored macro
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
