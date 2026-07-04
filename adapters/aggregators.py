"""
Job Board Aggregators
=====================
Adapters pra agregadores remote-first com API JSON pública.

Esses são CRÍTICOS pro volume: cada um agrega vagas de centenas de empresas.
São o ponto de maior ROI do pipeline.

Adapters incluídos:
- Remotive (https://remotive.com/api/remote-jobs)
- Remote OK (https://remoteok.com/api)
- Himalayas (https://himalayas.app/jobs/api/jobs)
- Arbeitnow (https://www.arbeitnow.com/api/job-board-api)
- We Work Remotely (RSS feed)
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree as ET

import requests

# Reusa schema do módulo principal
from adapters import JobPosting, _strip_html, _detect_remote, _parse_iso, _http_get

logger = logging.getLogger(__name__)

POLITE_DELAY = 2.5

HEADERS = {
    "User-Agent": "JobRadar/1.0 (contact: mauricio.esquivel1806@gmail.com)",
    "Accept": "application/json, text/xml, */*",
}


# ──────────────────────────────────────────────────────────────────────
# REMOTIVE
# ──────────────────────────────────────────────────────────────────────
def fetch_remotive(handle: str = "data") -> list[JobPosting]:
    """
    GET https://remotive.com/api/remote-jobs?category={handle}
    Categorias: software-dev, data, devops, etc.
    """
    url = "https://remotive.com/api/remote-jobs"
    params = {"category": handle} if handle else {}
    data = _http_get(url, params=params)
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    out: list[JobPosting] = []
    for j in jobs:
        out.append(JobPosting(
            ats="remotive",
            company_handle=handle or "all",
            external_id=str(j.get("id", "")),
            title=j.get("title", "").strip(),
            location=j.get("candidate_required_location", "") or "Worldwide",
            remote_flag=True,    # remotive é 100% remote
            description=_strip_html(j.get("description", "")),
            url=j.get("url", ""),
            posted_at=_parse_iso(j.get("publication_date")),
            department=j.get("category"),
            raw={"company_name": j.get("company_name"), **j},
        ))
        # company_name pra dedupe correto
        out[-1].raw["_company_label"] = j.get("company_name", "Remotive")
    time.sleep(POLITE_DELAY)
    return out


# ──────────────────────────────────────────────────────────────────────
# REMOTE OK
# ──────────────────────────────────────────────────────────────────────
def fetch_remoteok(handle: str = "data-engineer") -> list[JobPosting]:
    """
    GET https://remoteok.com/api
    Retorna array com primeiro elemento sendo metadata.
    Filtra por tags depois.
    """
    url = "https://remoteok.com/api"
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    data = response.json()

    # Primeiro elemento é metadata, descartar
    if isinstance(data, list) and len(data) > 0 and "legal" in data[0]:
        data = data[1:]

    target_tag = handle.lower().replace("-", " ")
    out: list[JobPosting] = []
    for j in data:
        tags = " ".join(j.get("tags", [])).lower()
        position = (j.get("position") or "").lower()
        # Filtro por tag/position
        if target_tag not in tags and target_tag not in position and "data" not in tags:
            continue

        loc = j.get("location", "") or "Worldwide"
        out.append(JobPosting(
            ats="remoteok",
            company_handle=handle,
            external_id=str(j.get("id", "")),
            title=j.get("position", "").strip(),
            location=loc,
            remote_flag=True,
            description=_strip_html(j.get("description", "")),
            url=j.get("url") or j.get("apply_url", ""),
            posted_at=_parse_iso(j.get("date")),
            department=", ".join(j.get("tags", [])[:3]),
            raw={"company_name": j.get("company"), **j},
        ))
        out[-1].raw["_company_label"] = j.get("company", "RemoteOK")
    time.sleep(POLITE_DELAY)
    return out


# ──────────────────────────────────────────────────────────────────────
# HIMALAYAS
# ──────────────────────────────────────────────────────────────────────
def fetch_himalayas(handle: str = "data-engineer") -> list[JobPosting]:
    """
    GET https://himalayas.app/jobs/api?title={handle}
    """
    url = "https://himalayas.app/jobs/api"
    params = {"title": handle.replace("-", " ")}
    try:
        data = _http_get(url, params=params)
    except Exception as exc:
        logger.warning("himalayas failed: %s", exc)
        return []
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    out: list[JobPosting] = []
    for j in jobs:
        company_obj = j.get("company") or {}
        company_name = company_obj.get("name") if isinstance(company_obj, dict) else str(company_obj)
        locations = j.get("locationRestrictions") or j.get("locations") or []
        if isinstance(locations, list):
            location = ", ".join(str(loc) for loc in locations[:3])
        else:
            location = str(locations)
        out.append(JobPosting(
            ats="himalayas",
            company_handle=handle,
            external_id=str(j.get("guid") or j.get("id", "")),
            title=j.get("title", "").strip(),
            location=location or "Remote",
            remote_flag=True,
            description=_strip_html(j.get("excerpt") or j.get("description", "")),
            url=j.get("applicationLink") or f"https://himalayas.app/jobs/{j.get('slug', '')}",
            posted_at=_parse_iso(j.get("pubDate") or j.get("publishedAt")),
            department=j.get("category"),
            raw={"company_name": company_name, **j},
        ))
        out[-1].raw["_company_label"] = company_name or "Himalayas"
    time.sleep(POLITE_DELAY)
    return out


# ──────────────────────────────────────────────────────────────────────
# ARBEITNOW (EU visa sponsorship focused)
# ──────────────────────────────────────────────────────────────────────
def fetch_arbeitnow(handle: str = "visa-sponsorship") -> list[JobPosting]:
    """
    GET https://www.arbeitnow.com/api/job-board-api
    Filtra por tag 'visa-sponsorship' depois (a API não filtra direto).
    """
    url = "https://www.arbeitnow.com/api/job-board-api"
    data = _http_get(url)
    jobs = data.get("data", []) if isinstance(data, dict) else []
    out: list[JobPosting] = []
    for j in jobs:
        tags = [t.lower() for t in (j.get("tags") or [])]
        # Se filtro é visa-sponsorship, força match
        if handle == "visa-sponsorship" and "visa-sponsorship" not in tags:
            continue
        out.append(JobPosting(
            ats="arbeitnow",
            company_handle=handle,
            external_id=str(j.get("slug", "")),
            title=j.get("title", "").strip(),
            location=j.get("location", "") or "Germany",
            remote_flag=bool(j.get("remote", False)),
            description=_strip_html(j.get("description", "")),
            url=j.get("url", ""),
            posted_at=_parse_iso(j.get("created_at")),
            department=", ".join(j.get("job_types") or [])[:80],
            raw={"company_name": j.get("company_name"), **j},
        ))
        out[-1].raw["_company_label"] = j.get("company_name", "Arbeitnow")
    time.sleep(POLITE_DELAY)
    return out


# ──────────────────────────────────────────────────────────────────────
# WE WORK REMOTELY (RSS feed)
# ──────────────────────────────────────────────────────────────────────
def fetch_wwr(handle: str = "data") -> list[JobPosting]:
    """
    RSS feed: https://weworkremotely.com/categories/remote-{handle}-jobs.rss
    """
    if handle == "data":
        feed_url = "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss"
    else:
        feed_url = f"https://weworkremotely.com/categories/remote-{handle}-jobs.rss"

    try:
        response = requests.get(feed_url, headers=HEADERS, timeout=20)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("WWR feed failed: %s", exc)
        return []

    out: list[JobPosting] = []
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        logger.warning("WWR RSS parse failed: %s", exc)
        return []

    # RSS namespace
    for item in root.iter("item"):
        title_full = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description = _strip_html(item.findtext("description") or "")
        pub_date = item.findtext("pubDate")

        # WWR formata título como "Company: Job Title"
        if ":" in title_full:
            company_name, _, title = title_full.partition(":")
            company_name = company_name.strip()
            title = title.strip()
        else:
            company_name = "WWR"
            title = title_full

        # Filtra só vagas relacionadas a data
        haystack = (title + " " + description).lower()
        if not any(k in haystack for k in ["data engineer", "analytics engineer", "data platform"]):
            continue

        out.append(JobPosting(
            ats="wwr",
            company_handle=handle,
            external_id=link.split("/")[-1] if link else title_full[:40],
            title=title,
            location="Worldwide",
            remote_flag=True,
            description=description,
            url=link,
            posted_at=_parse_pubdate(pub_date),
            raw={"company_name": company_name, "_company_label": company_name},
        ))
    time.sleep(POLITE_DELAY)
    return out


def _parse_pubdate(value: str | None) -> datetime | None:
    """RFC 822 → datetime."""
    if not value:
        return None
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(value)
    except (ValueError, TypeError):
        return None


# Registry pros aggregators
AGGREGATOR_ADAPTERS = {
    "remotive": fetch_remotive,
    "remoteok": fetch_remoteok,
    "himalayas": fetch_himalayas,
    "arbeitnow": fetch_arbeitnow,
    "wwr": fetch_wwr,
}
