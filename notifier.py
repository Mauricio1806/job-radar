"""
Telegram notifier v10
=====================
- Frescor via posted_at REAL da fonte (Adzuna reporta data original)
- Mostra empresa REAL (Data Meaning), não "Adzuna Global"
- Removido texto confuso sobre "via Adzuna"
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Iterable

import requests

logger = logging.getLogger(__name__)

TG_API = "https://api.telegram.org"
MAX_MESSAGE_LEN = 3800
BATCH_SIZE = 8


def _time_since_posted(posted_at_iso: str | None,
                        first_seen_iso: str | None) -> tuple[str, str]:
    """
    Prioriza posted_at (data real da fonte). Se não tiver, cai pra first_seen.

    - 🔥🔥 = < 24h posted na fonte (Adzuna reporta data original)
    - 🔥   = 1-3 dias
    - ⚡   = 3-7 dias
    - (sem)= > 7 dias
    - ❓   = sem data
    """
    iso = posted_at_iso or first_seen_iso
    label_prefix = "postada" if posted_at_iso else "no radar"

    if not iso:
        return ("data desconhecida", "❓")
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        hours = delta.total_seconds() / 3600

        if hours < 1:
            return (f"{label_prefix} agora há pouco", "🔥🔥")
        if hours < 24:
            h = int(hours)
            return (f"{label_prefix} há {h}h", "🔥🔥")
        if hours < 72:
            d = int(hours / 24)
            return (f"{label_prefix} há {d}d", "🔥")
        if hours < 168:
            d = int(hours / 24)
            return (f"{label_prefix} há {d}d", "⚡")
        d = int(hours / 24)
        return (f"{label_prefix} há {d}d", "")
    except (ValueError, AttributeError):
        return ("data inválida", "❓")


def _format_job(row: sqlite3.Row) -> str:
    title = row["title"]
    location = row["location"] or "—"
    score = row["score"]
    url = row["url"]
    keywords = (row["matched_keywords"] or "").replace('"', "")

    # Empresa REAL: prioriza ats_handle (que agora é o company_name real da Adzuna)
    # Fallback pra company_name da tabela companies
    empresa = row["company_name"]
    try:
        ats_handle = row["ats_handle"]
        # Se ats_handle parece nome de empresa (não é query_id nem handle técnico)
        if ats_handle and " " in ats_handle and len(ats_handle) > 5:
            empresa = ats_handle
    except (IndexError, KeyError):
        pass

    posted_label, freshness = _time_since_posted(
        row["posted_at"], row["first_seen_at"]
    )

    tier = row["tier"] or ""
    tier_label = {"T1": "🟢 T1", "T2": "🔵 T2", "T3": "⚪ T3"}.get(tier, "")

    return (
        f"{freshness} <b>{_escape(title)}</b>\n"
        f"🏢 {_escape(empresa)} | 🌎 Remote | {tier_label}\n"
        f"📌 {_escape(location)}\n"
        f"📅 <b>{posted_label}</b>\n"
        f"⭐ Score: <b>{score}</b> | 🔖 {_escape(keywords[:120])}\n"
        f"🔗 {url}"
    )


def _escape(s: str) -> str:
    if not s:
        return ""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _send(token: str, chat_id: str, text: str) -> bool:
    url = f"{TG_API}/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": True,
    }
    try:
        response = requests.post(url, json=payload, timeout=15)
        if response.status_code == 200:
            return True
        logger.error("telegram error %s: %s", response.status_code, response.text[:200])
        return False
    except requests.RequestException as exc:
        logger.error("telegram request failed: %s", exc)
        return False


def notify_jobs(rows: Iterable[sqlite3.Row]) -> list[str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.warning("TELEGRAM_BOT_TOKEN/CHAT_ID not set, skipping notify")
        return []

    rows = list(rows)
    if not rows:
        return []

    # Ordenar por FRESCOR (posted_at recente primeiro) e depois score
    def sort_key(r):
        try:
            posted = r["posted_at"] or r["first_seen_at"] or ""
        except (IndexError, KeyError):
            posted = ""
        return (posted, r["score"])

    rows.sort(key=sort_key, reverse=True)

    notified_ids: list[str] = []
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        fresh_24h = sum(1 for r in batch if _hours_since_posted(r) < 24)
        fresh_72h = sum(1 for r in batch if _hours_since_posted(r) < 72)

        header = (
            f"🎯 <b>{len(batch)} vagas remote</b>\n"
            f"({fresh_24h} 🔥🔥 postadas &lt; 24h | {fresh_72h} 🔥 &lt; 3d)\n\n"
        )
        body = "\n\n━━━━━━━━━━━━━━\n\n".join(_format_job(r) for r in batch)
        message = header + body
        if len(message) > MAX_MESSAGE_LEN:
            message = message[:MAX_MESSAGE_LEN] + "\n\n… (truncated)"
        if _send(token, chat_id, message):
            notified_ids.extend(r["id"] for r in batch)
        time.sleep(1.5)

    return notified_ids


def _hours_since_posted(row: sqlite3.Row) -> float:
    try:
        iso = row["posted_at"] or row["first_seen_at"]
    except (IndexError, KeyError):
        return 99999
    if not iso:
        return 99999
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    except (ValueError, AttributeError):
        return 99999
