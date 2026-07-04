"""Webhook notification utility for pipeline events."""

from __future__ import annotations

from typing import Any

import requests

from src.utils.logger import get_logger

logger = get_logger("notify")


def send_webhook(
    url: str,
    title: str,
    body: str,
    color: str = "good",
    fields: dict[str, str] | None = None,
) -> bool:
    """Send a notification via webhook (Discord/Slack compatible).

    Args:
        url: Webhook URL.
        title: Message title.
        body: Message body text.
        color: Color indicator ('good', 'warning', 'danger').
        fields: Optional key-value fields to include.

    Returns:
        True if sent successfully.
    """
    if not url:
        return False

    color_map = {"good": 0x2ECC71, "warning": 0xF39C12, "danger": 0xE74C3C}
    embed_color = color_map.get(color, 0x3498DB)

    embed_fields = []
    if fields:
        for name, value in fields.items():
            embed_fields.append({"name": name, "value": str(value), "inline": True})

    # Discord webhook format
    payload: dict[str, Any] = {
        "embeds": [
            {
                "title": title,
                "description": body,
                "color": embed_color,
                "fields": embed_fields,
            }
        ]
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.debug("Webhook sent: %s", title)
        return True
    except requests.RequestException as exc:
        logger.warning("Failed to send webhook: %s", exc)
        return False


def notify_pipeline_complete(
    webhook_url: str,
    fills_count: int = 0,
    events_count: int = 0,
    wallets_scored: int = 0,
    errors: list[str] | None = None,
) -> bool:
    """Send pipeline completion notification."""
    fields = {
        "Fills取得": str(fills_count),
        "イベント検出": str(events_count),
        "ウォレットスコア": str(wallets_scored),
    }

    if errors:
        return send_webhook(
            url=webhook_url,
            title="⚠ Pipeline 完了 (エラーあり)",
            body=f"エラー {len(errors)} 件:\n" + "\n".join(f"- {e}" for e in errors[:5]),
            color="warning",
            fields=fields,
        )

    return send_webhook(
        url=webhook_url,
        title="Pipeline 完了",
        body="全フェーズ正常終了",
        color="good",
        fields=fields,
    )
