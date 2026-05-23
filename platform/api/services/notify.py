"""Slack notification service."""

import os
from typing import Literal

import httpx
import structlog

log = structlog.get_logger(__name__)

_SLACK_WEBHOOK_URL = os.environ.get("SLACK_PLATFORM_WEBHOOK", "")

_LEVEL_EMOJI = {
    "info": "ℹ️",
    "success": "✅",
    "warning": "⚠️",
    "error": "❌",
}


async def notify_slack(
    message: str,
    level: Literal["info", "success", "warning", "error"] = "info",
) -> None:
    """
    Post a message to the platform Slack channel.

    Webhook URL sourced from environment variable — never hardcoded.
    Silently swallows errors (notifications are non-critical).
    """
    if not _SLACK_WEBHOOK_URL:
        log.debug("notify_slack.skipped", reason="SLACK_PLATFORM_WEBHOOK not set")
        return

    emoji = _LEVEL_EMOJI.get(level, "ℹ️")
    payload = {"text": f"{emoji} {message}"}

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(_SLACK_WEBHOOK_URL, json=payload)
            response.raise_for_status()
        log.debug("notify_slack.sent", level=level)
    except Exception as e:
        # Notification failure must never affect provisioning flow
        log.warning("notify_slack.failed", error=str(e))
