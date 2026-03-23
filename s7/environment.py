"""
S7 Environment builder -- wires up Slack API calls and built-in functions.

Creates the root Environment that the interpreter uses, injecting:
  - Context atoms (#!, @!)
  - Arithmetic / logic operators
  - Slack API wrappers (members, addto, kick, send, send2, echo)
  - Utility functions (str, strjoin, length, index, list helpers)
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional

from .interpreter import Environment, S7Error


def resolve(items: Any) -> List[str]:
    """Recursively flatten lists and extract IDs from Slack blue-pill entities."""
    out: List[str] = []
    if not isinstance(items, list):
        items = [items]
    for i in items:
        if isinstance(i, list):
            out.extend(resolve(i))
        else:
            match = re.search(r"([UCG][A-Z0-9]+)", str(i))
            out.append(match.group(1) if match else str(i))
    return list(dict.fromkeys(out))  # unique, order-preserved


def build_environment(
    client: Any,
    channel_id: str,
    user_id: str,
    power_users: List[str],
    echo_collector: List[str],
    storage: Any = None,
    trigger_id: str | None = None,
    callback_macro: str | None = None,
) -> Environment:
    """
    Build the root S7 environment with all built-ins.

    Parameters
    ----------
    client : slack_sdk.WebClient (or compatible)
    channel_id : current channel where the command was invoked
    user_id : Slack user ID of the person who ran the command
    power_users : list of user IDs allowed to use privileged functions
    echo_collector : mutable list that echo() appends messages to
    storage : S7Store instance for persistent key-value storage
    trigger_id : Slack trigger_id for opening modals (from interactive events)
    callback_macro : macro name to call when modal is submitted
    """

    def _echo(*args: Any) -> None:
        msg = " ".join(str(a) for a in args)
        echo_collector.append(msg)

    def _send(target: Any, *args: Any) -> None:
        # _send(target, message...) or _send(message...) defaults to #!
        if args:
            # _send(target, message...)
            target_id = resolve(target)[0]
            text = f"[<@{user_id}>] " + " ".join(str(a) for a in args)
        else:
            # _send(message...) - send to current channel
            target_id = channel_id
            text = f"[<@{user_id}>] " + str(target)
        client.chat_postMessage(channel=target_id, text=text)

    def _send2(target: Any, *args: Any) -> None:
        if args:
            target_id = resolve(target)[0]
            if user_id in power_users:
                text = " ".join(str(a) for a in args)
            else:
                text = f"[<@{user_id}>] " + " ".join(str(a) for a in args)
        else:
            target_id = channel_id
            if user_id in power_users:
                text = str(target)
            else:
                text = f"[<@{user_id}>] " + str(target)
        client.chat_postMessage(channel=target_id, text=text)

    def _members(channel: Any) -> List[str]:
        cid = resolve(channel)[0]
        result: List[str] = []
        cursor = None
        while True:
            kwargs: Dict[str, Any] = {"channel": cid, "limit": 1000}
            if cursor:
                kwargs["cursor"] = cursor
            resp = client.conversations_members(**kwargs)
            result.extend(resp["members"])
            cursor = resp.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
        return result

    def _addto(*args: Any) -> None:
        all_ids = resolve(list(args))
        users = [uid for uid in all_ids if uid.startswith("U")]
        channels = [cid for cid in all_ids if cid.startswith("C")]
        for c in channels:
            if users:
                try:
                    client.conversations_invite(channel=c, users=users)
                except Exception as e:
                    echo_collector.append(f"addto error ({c}): {e}")

    def _kick(channel: Any, *targets: Any) -> None:
        if user_id not in power_users:
            raise S7Error("kick requires power-user privileges")
        cid = resolve(channel)[0]
        user_ids = resolve(list(targets))
        for uid in user_ids:
            if uid.startswith("U"):
                try:
                    client.conversations_kick(channel=cid, user=uid)
                except Exception as e:
                    echo_collector.append(f"kick error ({uid}): {e}")

    def _email(member: Any) -> str:
        """Get email of a member."""
        uid = resolve([member])[0]
        if not uid.startswith("U"):
            raise S7Error("email requires a user ID")
        resp = client.users_info(user=uid)
        user = resp["user"]
        profile = user.get("profile", {})
        return profile.get("email", user.get("name", "") + "@unknown")

    def _profile(member: Any, field: str = "display_name") -> str:
        """Get profile field of a member. Fields: display_name, real_name, title, status_text, etc."""
        uid = resolve([member])[0]
        if not uid.startswith("U"):
            raise S7Error("profile requires a user ID")
        resp = client.users_info(user=uid)
        user = resp["user"]
        profile = user.get("profile", {})
        return profile.get(field, user.get(field, ""))

    def _strjoin(sep: Any, *lists: Any) -> str:
        flat = resolve(list(lists))
        return str(sep).join(flat)

    def _str_cast(arg: Any) -> str:
        if isinstance(arg, list):
            return "[" + ", ".join(str(x) for x in arg) + "]"
        return str(arg)

    def _length(x: Any) -> int:
        if isinstance(x, (list, tuple, str)):
            return len(x)
        raise S7Error(f"length: unsupported type {type(x).__name__}")

    def _index(collection: Any, idx: Any) -> Any:
        return collection[int(idx)]

    def _flatten(*args: Any) -> List[Any]:
        out: List[Any] = []
        for a in args:
            if isinstance(a, list):
                out.extend(_flatten(*a))
            else:
                out.append(a)
        return out

    def _filter_fn(fn: Any, lst: Any) -> List[Any]:
        if not isinstance(lst, list):
            raise S7Error("filter: second argument must be a list")
        return [x for x in lst if fn(x)]

    def _map_fn(fn: Any, lst: Any) -> List[Any]:
        if not isinstance(lst, list):
            raise S7Error("map: second argument must be a list")
        return [fn(x) for x in lst]

    def _append(lst: Any, *items: Any) -> List[Any]:
        if not isinstance(lst, list):
            lst = [lst]
        return lst + list(items)

    def _range_fn(*args: Any) -> List[int]:
        int_args = [int(a) for a in args]
        return list(range(*int_args))

    def _store(key: Any, value: Any) -> None:
        """Store a value under a key (user-scoped)."""
        if storage is None:
            raise S7Error("storage not available")
        storage.set(user_id, str(key), value)

    def _read(key: Any) -> Any:
        """Read a value from storage by key. Returns nil if not found."""
        if storage is None:
            raise S7Error("storage not available")
        return storage.get(user_id, str(key))

    def _delete_key(key: Any) -> bool:
        """Delete a key from storage. Returns true if it existed."""
        if storage is None:
            raise S7Error("storage not available")
        return storage.delete(user_id, str(key))

    def _sendi(target: Any, text: Any, *buttons: Any) -> None:
        """
        Send a message with interactive buttons.
        (sendi #channel "message" "btn1:macro1" "btn2:macro2" ...)
        Each button is "label:macro_name" or just "label" (calls macro with same name).
        """
        target_id = resolve(target)[0]
        text_str = str(text)
        
        button_elements = []
        for btn in buttons:
            btn_str = str(btn)
            if ":" in btn_str:
                label, macro_name = btn_str.split(":", 1)
            else:
                label = btn_str
                macro_name = btn_str
            button_elements.append({
                "type": "button",
                "text": {"type": "plain_text", "text": label},
                "action_id": f"s7_button_{macro_name}",
                "value": macro_name,
            })
        
        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": text_str}},
        ]
        if button_elements:
            blocks.append({"type": "actions", "elements": button_elements})
        
        client.chat_postMessage(
            channel=target_id,
            text=text_str,
            blocks=blocks,
        )

    def _showui(title: Any, callback: Any, *fields: Any) -> None:
        """
        Open a modal form dialog.
        (showui "title" "callback_macro" "field1" "field2" ...)
        
        Each field creates a text input. When submitted, the callback macro
        is called with args containing the field values in order.
        
        Note: This requires a trigger_id which is only available from
        interactive contexts (button clicks, shortcuts).
        """
        if trigger_id is None:
            raise S7Error("showui requires an interactive trigger (use from a button callback)")
        
        title_str = str(title)[:24]  # Slack limits title to 24 chars
        callback_str = str(callback)
        
        # Build input blocks for each field
        blocks = []
        for i, field in enumerate(fields):
            field_str = str(field)
            blocks.append({
                "type": "input",
                "block_id": f"field_{i}",
                "element": {
                    "type": "plain_text_input",
                    "action_id": f"input_{i}",
                    "placeholder": {"type": "plain_text", "text": field_str},
                },
                "label": {"type": "plain_text", "text": field_str},
            })
        
        # Encode callback info in private_metadata
        import json
        metadata = json.dumps({
            "callback_macro": callback_str,
            "channel_id": channel_id,
            "user_id": user_id,
            "field_count": len(fields),
        })
        
        client.views_open(
            trigger_id=trigger_id,
            view={
                "type": "modal",
                "callback_id": "s7_modal_submit",
                "title": {"type": "plain_text", "text": title_str},
                "submit": {"type": "plain_text", "text": "Submit"},
                "close": {"type": "plain_text", "text": "Cancel"},
                "private_metadata": metadata,
                "blocks": blocks,
            },
        )

    bindings: Dict[str, Any] = {
        # Context atoms
        "#!": lambda: channel_id,
        "@!": lambda: user_id,

        # Arithmetic
        "+": lambda a, b: a + b,
        "-": lambda a, b: a - b,
        "*": lambda a, b: a * b,
        "/": lambda a, b: a / b if b != 0 else _raise(S7Error("Division by zero")),

        # Comparison
        ">": lambda a, b: a > b,
        "<": lambda a, b: a < b,
        ">=": lambda a, b: a >= b,
        "<=": lambda a, b: a <= b,
        "!=": lambda a, b: a != b,
        "=": lambda a, b: a == b,
        "==": lambda a, b: a == b,
        "eq": lambda a, b: a == b,

        # Logic
        "and": lambda a, b: a and b,
        "or": lambda a, b: a or b,
        "&&": lambda a, b: a and b,
        "||": lambda a, b: a or b,
        "not": lambda a: not a,

        # Boolean literals (also handled in parser, but available as symbols)
        "true": True,
        "false": False,
        "nil": None,

        # Collection
        "index": _index,
        "length": _length,
        "flatten": _flatten,
        "append": _append,
        "range": _range_fn,
        "filter": _filter_fn,
        "map": _map_fn,

        # Strings
        "str": _str_cast,
        "strjoin": _strjoin,
        "concat": lambda *a: "".join(str(x) for x in a),

        # Communication
        "echo": _echo,
        "send": _send,
        "send2": _send2,

        # Membership
        "members": _members,
        "addto": _addto,
        "kick": _kick,
        "email": _email,
        "profile": _profile,

        # Math extras
        "abs": lambda x: abs(x),
        "min": lambda *a: min(a),
        "max": lambda *a: max(a),
        "mod": lambda a, b: a % b,

        # Type checks
        "number?": lambda x: isinstance(x, (int, float)),
        "string?": lambda x: isinstance(x, str),
        "list?": lambda x: isinstance(x, list),
        "nil?": lambda x: x is None,

        # Debug
        "type": lambda x: type(x).__name__,

        # Storage
        "store": _store,
        "read": _read,
        "delete": _delete_key,

        # Interactive UI
        "sendi": _sendi,
        "showui": _showui,
    }

    return Environment(bindings)


def _raise(exc: Exception) -> None:
    raise exc
