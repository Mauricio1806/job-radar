"""
Adzuna Adapter v10 — remote-only, no enrich theater
====================================================
Mudanças:
- DESCARTA vagas non-remote na origem (não vai nem pro banco)
- Retorna company_name real da vaga (Data Meaning, não "Adzuna Global")
- Removido enrich que não funciona (Adzuna bloqueia bot)
- posted_at real da fonte (Adzuna reporta data original da vaga)
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
    {"id": "br-data-engineer", "country": "br", "what": "data engineer"},
    {"id": "br-analytics-engineer", "country": "br", "what": "analytics engineer"},
    {"id": "br-senior-data-engineer", "country": "br", "what": "senior data engineer"},
    {"id": "us-de-remote-latam", "country": "us", "what": "data engineer remote latin america"},
    {"id": "us-de-latam", "country": "us", "what": "data engineer latam"},
    {"id": "us-analytics-latam", "country": "us", "what": "analytics engineer latin america"},
    {"id": "ca-de-remote-latam", "country": "ca", "what": "data engineer remote latin america"},
]

# Sinais explícitos de remote (precisa 1 desses aparecer)
REMOTE_SIGNALS = (
    "remote", "anywhere", "work from home", "home office", "home-office",
    "trabalho remoto", "100% remoto", "teletrabajo",
    "latam", "latin america", "distribuído", "remoto",
    "fully remote", "distributed team", "wfh",
)

# Sinais explícitos de PRESENCIAL/HÍBRIDO (se aparecer, descarta)
ONSITE_SIGNALS = (
    "hybrid", "híbrido", "hibrido",
    "on-site", "onsite", "on site",
    "presencial", "in-office", "in office",
    "must relocate", "relocation required",
)


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
        "results_per_page": RESULTS_PER_PAGE,
        "what": params.get("what", ""),
        "max_days_old": MAX_DAYS_OLD,
        "sort_by": "date",
        "content-type": "application/json",
    }

    try:
        response = requests.get(url, params=query_params, timeout=TIMEOUT)
        if response.status_code == 429:
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


def _is_confidently_remote(title: str, description: str, location: str) -> bool:
    """
    True SÓ se houver sinal explícito de remote E não houver conflito com hybrid/onsite.
    """
    haystack = (title + " " + description + " " + location).lower()

    # Se menciona hybrid/onsite explicitamente, descarta mesmo se falar "remote" também
    for onsite in ONSITE_SIGNALS:
        if onsite in haystack:
            # Exceção: "remote or hybrid" — se remote aparece perto, ainda aceita
            # Mas normalmente hybrid = presencial parcial = fora
            return False

    # Precisa ter sinal claro de remote
    for signal in REMOTE_SIGNALS:
        if signal in haystack:
            return True

    return False


def _parse_job(item: dict, query_id: str) -> JobPosting | None:
    title = item.get("title", "").strip()
    if not title:
        return None

    location_obj = item.get("location", {}) or {}
    location_area = location_obj.get("area", []) or []
    location = (", ".join(location_area[-2:]) if location_area
                else location_obj.get("display_name", ""))

    description = _strip_html(item.get("description", ""))

    # ⚠️ GATE REMOTE: descarta se não for confidently remote
    if not _is_confidently_remote(title, description, location):
        return None

    company_name = (item.get("company") or {}).get("display_name", "Empresa")

    created = item.get("created")
    posted_at = None
    if created:
        try:
            posted_at = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            pass

    return JobPosting(
        ats="adzuna",
        company_handle=company_name[:50],   # nome real da empresa vai como handle
        external_id=str(item.get("id", "")),
        title=title,
        location=location,
        remote_flag=True,  # já garantimos que é remote
        description=description[:2000],
        url=item.get("redirect_url", ""),
        posted_at=posted_at,
        department=(item.get("category") or {}).get("label"),
        raw={"_company_label": company_name, "_query_id": query_id},
    )


def fetch_adzuna(handle: str = "all") -> list[JobPosting]:
    seen_ids: set[str] = set()
    out: list[JobPosting] = []
    discarded_non_remote = 0

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

        kept = 0
        discarded = 0
        for item in results:
            item_id = str(item.get("id", ""))
            if not item_id or item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            job = _parse_job(item, query_id)
            if job:
                out.append(job)
                kept += 1
            else:
                discarded += 1

        discarded_non_remote += discarded
        logger.info("Adzuna [%s]: kept=%d, discarded=%d (non-remote)",
                    query_id, kept, discarded)
        time.sleep(POLITE_DELAY)

    logger.info("Adzuna TOTAL: %d remote jobs (%d discarded non-remote)",
                len(out), discarded_non_remote)
    return out


ADZUNA_ADAPTERS = {
    "adzuna": fetch_adzuna,
}
