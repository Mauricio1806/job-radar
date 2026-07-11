"""
Telegram notifier v9
====================
Adiciona no output:
- Domínio final (jazzhr.com, greenhouse.io, etc.) — mostra pra onde o link leva
- Badge ✓ = ATS confiável | ? = domínio desconhecido
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

TG_API = "https://api.telegram.org"
MAX_MESSAGE_LEN = 3800
BATCH_SIZE = 8

CONFIDENT_DOMAINS = {
    "jazzhr.com", "applytojob.com", "boards.greenhouse.io", "greenhouse.io",
    "jobs.lever.co", "lever.co", "kenoby.com", "gupy.io",
    "jobs.ashbyhq.com", "ashbyhq.com", "linkedin.com", "workable.com",
    "smartrecruiters.com", "workday.com", "myworkdayjobs.com",
    "solides.jobs", "solides.com", "personio.com", "personio.de",
    "jobvite.com", "icims.com", "bamboohr.com", "recruitee.com",
    "teamtailor.com", "breezy.hr", "vagas.com.br", "catho.com.br",
    "infojobs.com.br", "trampos.co",
}


def _domain_from_url(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return ""


def _domain_badge(url: str) -> str:
    d = _domain_from_url(url)
    if not d:
        return ""
    for known in CONFIDENT_DOMAINS:
        if known in d:
            return f"✓ {d}"
    # Se o link ainda é do Adzuna, sinalizar
    if "adzuna" in d:
        return f"⚠️ via Adzuna (não é link direto)"
    return f"? {d}"


def _time_on_radar(first_seen_iso: str | None) -> tuple[str, str]:
    if not first_seen_iso:
        return ("agora", "🔥🔥")
    try:
        first_seen = datetime.fromisoformat(first_seen_iso.replace("Z", "+00:00"))
        if first_seen.tzinfo is None:
            first_seen = first_seen.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - first_seen
        minutes = delta.total_seconds() / 60

        if minutes < 5:
            return ("acabou de aparecer", "🔥🔥")
        if minutes < 60:
            return (f"há {int(minutes)}min no radar", "🔥🔥")
        if minutes < 360:
            hours = int(minutes / 60)
            return (f"há {hours}h no radar", "🔥")
        if minutes < 1440:
            hours = int(minutes / 60)
            return (f"há {hours}h no radar", "⚡")
        if minutes < 4320:
            days = int(minutes / 1440)
            return (f"há {days}d no radar", "")
        days = int(minutes / 1440)
        return (f"há {days}d no radar", "⚠️")
    except (ValueError, AttributeError):
        return ("desconhecido", "❓")


def _format_job(row: sqlite3.Row) -> str:
    title = row["title"]
    company = row["company_name"]
    location = row["location"] or "—"
    score = row["score"]
    url = row["url"]
    remote = "🌎 Remote" if row["remote_flag"] else "📍 Onsite/Hybrid"
    keywords = (row["matched_keywords"] or "").replace('"', "")

    radar_label, radar_marker = _time_on_radar(row["first_seen_at"])

    tier = row["tier"] or ""
    tier_label = {"T1": "🟢 T1", "T2": "🔵 T2", "T3": "⚪ T3"}.get(tier, "")

    visa = ""
    try:
        if row["visa_sponsorship"]:
            visa = " | 🛂 Visa Sponsor"
    except (IndexError, KeyError):
        pass

    domain_badge = _domain_badge(url)
    domain_line = f"\n🎯 {domain_badge}" if domain_badge else ""

    recruiter = ""
    if row["recruiter_name"]:
        recruiter = f"\n👤 {row['recruiter_name']}"
        if row["recruiter_email"]:
            recruiter += f" — {row['recruiter_email']}"

    return (
        f"{radar_marker} <b>{_escape(title)}</b>\n"
        f"🏢 {_escape(company)} | {remote} | {tier_label}{visa}\n"
        f"📌 {_escape(location)}\n"
        f"📡 <b>{radar_label}</b>"
        f"{domain_line}\n"
        f"⭐ Score: <b>{score}</b> | 🔖 {_escape(keywords[:120])}"
        f"{recruiter}\n"
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

    # Ordenar por SCORE DESC primeiro (não por tempo)
    rows.sort(key=lambda r: (-r["score"], r["first_seen_at"] or ""))

    notified_ids: list[str] = []
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        fresh_1h = sum(1 for r in batch if _minutes_on_radar(r["first_seen_at"]) < 60)
        fresh_24h = sum(1 for r in batch if _minutes_on_radar(r["first_seen_at"]) < 1440)

        header = (
            f"🎯 <b>{len(batch)} vagas novas no radar</b>\n"
            f"({fresh_1h} 🔥🔥 &lt; 1h | {fresh_24h} ⚡ &lt; 24h)\n\n"
        )
        body = "\n\n━━━━━━━━━━━━━━\n\n".join(_format_job(r) for r in batch)
        message = header + body
        if len(message) > MAX_MESSAGE_LEN:
            message = message[:MAX_MESSAGE_LEN] + "\n\n… (truncated)"
        if _send(token, chat_id, message):
            notified_ids.extend(r["id"] for r in batch)
        time.sleep(1.5)

    return notified_ids


def _minutes_on_radar(first_seen_iso: str | None) -> float:
    if not first_seen_iso:
        return 0
    try:
        first_seen = datetime.fromisoformat(first_seen_iso.replace("Z", "+00:00"))
        if first_seen.tzinfo is None:
            first_seen = first_seen.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - first_seen).total_seconds() / 60
    except (ValueError, AttributeError):
        return 99999
