"""
Adzuna Adapter — LATAM Remote USD ONLY
========================================
Removidas queries EU. Focado 100% Fase 1 (USD remote).

Cobertura:
- BR: data engineer / analytics engineer / senior data engineer
- US: data engineer "remote latin america" / "latam"
- CA: data engineer remote latam
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

import requests

from adapters import JobPosting, _strip_html

logger = logging.getLogger(__name__)

BASE_URL = "https://api.adzuna.com/v1/api/jobs"
POLITE_DELAY = 1.5
TIMEOUT = 30
MAX_DAYS_OLD = 3
RESULTS_PER_PAGE = 50


QUERIES = [
    # ─── BR remote (contract PJ USD) ───
    {"id": "br-data-engineer",
     "country": "br",
     "what": "data engineer",
     "results_per_page": RESULTS_PER_PAGE},
    {"id": "br-analytics-engineer",
     "country": "br",
     "what": "analytics engineer",
     "results_per_page": RESULTS_PER_PAGE},
    {"id": "br-senior-data-engineer",
     "country": "br",
     "what": "senior data engineer",
     "results_per_page": RESULTS_PER_PAGE},

    # ─── US contratando remote LATAM ───
    {"id": "us-de-remote-latam",
     "country": "us",
     "what": "data engineer remote latin america",
     "results_per_page": RESULTS_PER_PAGE},
    {"id": "us-de-latam",
     "country": "us",
     "what": "data engineer latam",
     "results_per_page": RESULTS_PER_PAGE},
    {"id": "us-analytics-latam",
     "country": "us",
     "what": "analytics engineer latin america",
     "results_per_page": RESULTS_PER_PAGE},

    # ─── CA contratando remote LATAM ───
    {"id": "ca-de-remote-latam",
     "country": "ca",
     "what": "data engineer remote latin america",
     "results_per_page": RESULTS_PER_PAGE},
]


def _fetch_page(country: str, page: int, params: dict) -> dict | None:
    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        logger.warning("ADZUNA_APP_ID/ADZUNA_APP_KEY not set — skipping")
        return None

    url = f"{BASE_URL}/{country}/search/{page}"
    query_params = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": params.get("results_per_page", 50),
        "what": params.get("what", ""),
        "max_days_old": MAX_DAYS_OLD,
        "sort_by": "date",
        "content-type": "application/json",
    }

    try:
        response = requests.get(url, params=query_params, timeout=TIMEOUT)
        if response.status_code == 429:
            logger.warning("Adzuna rate limited (429)")
            time.sleep(30)
            return None
        if response.status_code == 401:
            logger.error("Adzuna 401 — check ADZUNA_APP_ID/KEY")
            return None
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("Adzuna fetch failed [%s]: %s", country, exc)
        return None


def _parse_job(item: dict, query_id: str) -> JobPosting | None:
    location_obj = item.get("location", {}) or {}
    location_area = location_obj.get("area", []) or []
    location = (", ".join(location_area[-2:]) if location_area
                else location_obj.get("display_name", ""))

    description = _strip_html(item.get("description", ""))
    title = item.get("title", "").strip()
    if not title:
        return None

    haystack = (title + " " + description[:500] + " " + location).lower()
    remote_flag = any(
        k in haystack for k in ("remote", "anywhere", "work from home",
                                "home office", "trabalho remoto", "teletrabajo",
                                "100% remoto", "remoto", "remoto brasil")
    )

    company_name = (item.get("company") or {}).get("display_name", "Unknown")

    created = item.get("created")
    posted_at = None
    if created:
        try:
            posted_at = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            pass

    return JobPosting(
        ats="adzuna",
        company_handle=query_id,
        external_id=str(item.get("id", "")),
        title=title,
        location=location,
        remote_flag=remote_flag,
        description=description[:2000],
        url=item.get("redirect_url", ""),
        posted_at=posted_at,
        department=(item.get("category") or {}).get("label"),
        raw={"_company_label": company_name},
    )


def fetch_adzuna(handle: str = "all") -> list[JobPosting]:
    seen_ids: set[str] = set()
    out: list[JobPosting] = []

    for query in QUERIES:
        country = query["country"]
        query_id = query["id"]

        data = _fetch_page(country, 1, query)
        if not data:
            continue

        results = data.get("results", [])
        if not results:
            logger.info("Adzuna [%s]: 0 jobs", query_id)
            continue

        new_this_page = 0
        for item in results:
            item_id = str(item.get("id", ""))
            if not item_id or item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            job = _parse_job(item, query_id)
            if job:
                out.append(job)
                new_this_page += 1

        logger.info("Adzuna [%s]: %d jobs (%d new após dedup)",
                    query_id, len(results), new_this_page)
        time.sleep(POLITE_DELAY)

    logger.info("Adzuna TOTAL: %d unique jobs (max_days_old=%d)",
                len(out), MAX_DAYS_OLD)
    return out


ADZUNA_ADAPTERS = {
    "adzuna": fetch_adzuna,
}
