#!/usr/bin/env python3
"""React Tool — agent-driven message reactions.

Lets the agent decide when and what to react to a user's message, instead
of using hardcoded lifecycle hooks (👀 on start, 👍/👎 on complete).

Usage (called by the agent):
    react(emoji="👍")
    react(emoji="🔥")

The tool reads the current session context (platform, chat_id, message_id)
from gateway session vars, so the agent doesn't need to specify those.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)


TELEGRAM_EMOJI_ALLOWLIST = frozenset({
    # Thumbs & hands
    "👍", "👎", "👏", "🙌", "🤌", "🫡", "🤝", "✊", "👊", "✋",
    # Hearts
    "❤", "🧡", "💛", "💚", "💙", "💜", "🖤", "🤍", "🤎", "💕", "💗", "💖",
    # Faces
    "😂", "😅", "🤣", "😭", "😤", "😡", "🥹", "😍", "🤩", "😎", "🧐", "🤔",
    "🙄", "😏", "😈", "🤯", "🥶", "🥵", "😱", "🤗", "🫠", "😴", "😮‍💨",
    # Symbols
    "🔥", "💯", "🎯", "💪", "🎉", "🎊", "✨", "💡", "📌", "✅", "❌", "💀",
    "🫶", "🌟", "⭐", "🏆", "🥇", "👀", "🫣", "🤷", "🤦",
})


def _send_telegram_reaction(token: str, chat_id: str, message_id: str, emoji: str) -> dict:
    """Send a reaction emoji to a Telegram message using the Bot API."""
    try:
        from telegram import Bot
        import asyncio

        async def _do():
            bot = Bot(token=token)
            await bot.set_message_reaction(
                chat_id=int(chat_id),
                message_id=int(message_id),
                reaction=emoji,
                is_big=False,
            )

        asyncio.run(_do())
        return {"success": True, "platform": "telegram", "emoji": emoji}
    except ImportError:
        return {"error": "python-telegram-bot not installed. Run: pip install python-telegram-bot"}
    except Exception as e:
        return {"error": f"Telegram reaction failed: {e}"}


def _send_discord_reaction(token: str, chat_id: str, message_id: str, emoji: str) -> dict:
    """Send a reaction emoji to a Discord message via REST API."""
    import urllib.parse

    try:
        import httpx
    except ImportError:
        return {"error": "httpx not installed. Run: pip install httpx"}

    try:
        # Discord needs the emoji URL-encoded (Unicode emojis work raw with PUT)
        encoded_emoji = urllib.parse.quote(emoji, safe="")
        url = f"https://discord.com/api/v10/channels/{chat_id}/messages/{message_id}/reactions/{encoded_emoji}/@me"

        with httpx.Client() as client:
            resp = client.put(url, headers={"Authorization": f"Bot {token}"})
            if resp.status_code in (200, 204):
                return {"success": True, "platform": "discord", "emoji": emoji}
            return {"error": f"Discord reaction failed (HTTP {resp.status_code}): {resp.text[:200]}"}
    except Exception as e:
        return {"error": f"Discord reaction failed: {e}"}


def _get_platform_token(platform: str) -> str:
    """Get the bot token for a platform from environment or config."""
    env_var_map = {
        "telegram": "TELEGRAM_BOT_TOKEN",
        "discord": "DISCORD_BOT_TOKEN",
        "signal": "SIGNAL_USERNAME",  # Signal doesn't have a simple bot token
        "matrix": "MATRIX_ACCESS_TOKEN",
    }

    env_var = env_var_map.get(platform)
    if env_var:
        token = os.getenv(env_var, "")
        if token:
            return token

    # Fall back to gateway config
    try:
        from gateway.config import load_gateway_config, Platform
        config = load_gateway_config()
        p = Platform(platform)
        pconfig = config.platforms.get(p)
        if pconfig and pconfig.token:
            return pconfig.token
    except Exception:
        pass

    return ""


def react_tool(args: dict, **kw) -> str:
    """Handle react tool calls from the agent."""
    emoji = (args.get("emoji") or "").strip()

    if not emoji:
        return json.dumps({"error": "emoji is required"})

    # Read session context (concurrency-safe via contextvars)
    try:
        from gateway.session_context import get_session_env
        platform = get_session_env("HERMES_SESSION_PLATFORM", "")
        chat_id = get_session_env("HERMES_SESSION_CHAT_ID", "")
        message_id = get_session_env("HERMES_SESSION_MESSAGE_ID", "")
    except Exception:
        return json.dumps({"error": "No session context available. This tool only works inside the messaging gateway."})

    if not platform:
        return json.dumps({"error": "No platform in session context. Reactions are only available on messaging platforms."})
    if not chat_id:
        return json.dumps({"error": "No chat_id in session context."})
    if not message_id:
        return json.dumps({"error": "No message_id in session context — cannot react. This message may not support reactions."})

    platform = platform.lower()

    # Validate emoji for Telegram (Discord accepts anything)
    if platform == "telegram" and emoji not in TELEGRAM_EMOJI_ALLOWLIST:
        return json.dumps({
            "error": f"Emoji '{emoji}' is not supported by Telegram reactions. "
                     f"Use one of the standard Telegram reaction emojis."
        })

    token = _get_platform_token(platform)
    if not token:
        return json.dumps({"error": f"No bot token found for platform '{platform}'"})

    if platform == "telegram":
        result = _send_telegram_reaction(token, chat_id, message_id, emoji)
    elif platform == "discord":
        result = _send_discord_reaction(token, chat_id, message_id, emoji)
    else:
        result = {"error": f"Reactions not yet supported for platform '{platform}'"}

    logger.info("[react] %s → %s on %s:%s", emoji, result.get("success", False), platform, chat_id)
    return json.dumps(result, ensure_ascii=False)


def check_react_requirements() -> bool:
    """React tool requires the session to be inside the messaging gateway."""
    try:
        from gateway.session_context import get_session_env
        platform = get_session_env("HERMES_SESSION_PLATFORM", "")
        return bool(platform)
    except Exception:
        return False


# --- OpenAI Function-Calling Schema ---

REACT_SCHEMA = {
    "name": "react",
    "description": (
        "React to the user's last message with an emoji. "
        "Use this instead of text when a non-verbal reaction is more natural — "
        "a 👍 for agreement, 😂 for something genuinely funny, 💡 when someone "
        "makes a great point, 🔥 for something cool, 🫡 for 'on it', etc. "
        "Don't overuse it — only react when it adds meaning. "
        "The reaction is automatically applied to the current message in the "
        "current conversation — no need to specify a target."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "emoji": {
                "type": "string",
                "description": (
                    "The reaction emoji to send. Common options: "
                    "👍 (agreement), ❤ (love), 😂 (funny), 🔥 (amazing), "
                    "💡 (great idea), 🫡 (acknowledged), 🤔 (interesting), "
                    "👏 (applause), 💯 (perfect), 🎉 (celebration), "
                    "✅ (done), 👀 (noticed), 🤝 (deal), 💪 (impressive), "
                    "🙌 (excited), 🫶 (appreciation)."
                ),
            },
        },
        "required": ["emoji"],
    },
}


# --- Registry ---
from tools.registry import registry, tool_error

registry.register(
    name="react",
    toolset="messaging",
    schema=REACT_SCHEMA,
    handler=lambda args, **kw: react_tool(args, **kw),
    check_fn=check_react_requirements,
    emoji="💬",
)
