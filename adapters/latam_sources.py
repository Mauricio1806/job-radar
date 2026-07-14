"""
Get on Board + Jobicy Adapters
================================
Fontes onde REALMENTE estão as vagas LATAM remote USD:
- Ruzora, Artefact, staffing partners USD publicam no Get on Board
- Jobicy tem RSS gratuito com vagas remote LATAM

Get on Board Public API: https://api-doc.getonbrd.com/
- Sem autenticação necessária
- /api/v0/categories/data-science-analytics/jobs
- /api/v0/search/jobs?q=data+engineer&remote=true

Jobicy RSS: https://jobicy.com/jobs-rss-feed
- Sem autenticação
- Filtro por categoria: data-science
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import requests

from adapters import JobPosting, _strip_html

logger = logging.getLogger(__name__)

POLITE_DELAY = 2.0
TIMEOUT = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


# ──────────────────────────────────────────────────────────────────────
# GET ON BOARD
# ──────────────────────────────────────────────────────────────────────
GOB_BASE = "https://www.getonbrd.com/api/v0"

GOB_ENDPOINTS = [
    # Categoria principal DE/Analytics
    f"{GOB_BASE}/categories/data-science-analytics/jobs?per_page=100&expand[]=company",
    # Busca por termos específicos
    f"{GOB_BASE}/search/jobs?q=data+engineer&per_page=50&expand[]=company",
    f"{GOB_BASE}/search/jobs?q=analytics+engineer&per_page=50&expand[]=company",
    f"{GOB_BASE}/search/jobs?q=engenheiro+de+dados&per_page=50&expand[]=company",
]


def _parse_gob_job(item: dict) -> JobPosting | None:
    """Parse job from Get on Board API response."""
    attrs = item.get("attributes", {}) or {}
    if not attrs:
        # Formato flat (search endpoint retorna diferente)
        attrs = item

    title = (attrs.get("title") or "").strip()
    if not title:
        return None

    # Remote check
    remote = attrs.get("remote", False)
    remote_modality = attrs.get("remote_modality", "") or ""
    if not remote and "remote" not in remote_modality.lower():
        return None

    # Localização
    locations = attrs.get("locations") or []
    if isinstance(locations, list):
        location = ", ".join(str(loc) for loc in locations[:2]) if locations else "Remote LATAM"
    else:
        location = str(locations) or "Remote LATAM"

    # Empresa
    company_obj = attrs.get("company") or {}
    if isinstance(company_obj, dict):
        company_name = (company_obj.get("data", {}) or {}).get("attributes", {}).get("name", "") \
                       or company_obj.get("name", "") or "Unknown"
    else:
        company_name = str(company_obj) or "Unknown"

    # Descrição
    description = _strip_html(
        attrs.get("description", "") or attrs.get("functions", "") or ""
    )

    # URL
    url = attrs.get("url") or attrs.get("applyUrl") or ""
    if not url:
        job_id = item.get("id", "")
        url = f"https://www.getonbrd.com/jobs/{job_id}"

    # Data publicação
    published_at = attrs.get("published_at") or attrs.get("updated_at")
    posted_at = None
    if published_at:
        try:
            posted_at = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            pass

    external_id = str(item.get("id", "")) or url.split("/")[-1]

    return JobPosting(
        ats="getonboard",
        company_handle=company_name[:50],
        external_id=external_id,
        title=title,
        location=location,
        remote_flag=True,
        description=description[:2000],
        url=url,
        posted_at=posted_at,
        raw={"_company_label": company_name},
    )


def fetch_getonboard(handle: str = "all") -> list[JobPosting]:
    seen_ids: set[str] = set()
    out: list[JobPosting] = []

    for endpoint in GOB_ENDPOINTS:
        try:
            response = requests.get(endpoint, headers=HEADERS, timeout=TIMEOUT)
            if response.status_code == 404:
                logger.warning("GetOnBoard 404: %s", endpoint)
                continue
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            logger.error("GetOnBoard fetch failed [%s]: %s", endpoint, exc)
            continue

        # API retorna {"data": [...]} ou lista direta
        items = []
        if isinstance(data, dict):
            items = data.get("data", []) or []
        elif isinstance(data, list):
            items = data

        new_this = 0
        for item in items:
            ext_id = str(item.get("id", ""))
            if ext_id and ext_id in seen_ids:
                continue
            if ext_id:
                seen_ids.add(ext_id)
            job = _parse_gob_job(item)
            if job:
                out.append(job)
                new_this += 1

        logger.info("GetOnBoard [%s...]: %d jobs", endpoint[40:70], new_this)
        time.sleep(POLITE_DELAY)

    logger.info("GetOnBoard TOTAL: %d remote jobs", len(out))
    return out


# ──────────────────────────────────────────────────────────────────────
# JOBICY RSS
# ──────────────────────────────────────────────────────────────────────
JOBICY_FEEDS = [
    "https://jobicy.com/?feed=job_feed&job_categories=data-science&job_types=full-time&search_region=latam",
    "https://jobicy.com/?feed=job_feed&job_categories=data-science&job_types=full-time&search_region=brazil",
    "https://jobicy.com/?feed=job_feed&job_categories=data-science&job_types=contractor",
]


def _parse_pubdate(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(value)
    except (ValueError, TypeError):
        return None


def fetch_jobicy(handle: str = "all") -> list[JobPosting]:
    seen_ids: set[str] = set()
    out: list[JobPosting] = []

    for feed_url in JOBICY_FEEDS:
        try:
            response = requests.get(feed_url, headers=HEADERS, timeout=TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Jobicy feed failed [%s]: %s", feed_url, exc)
            continue

        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            logger.warning("Jobicy RSS parse failed: %s", exc)
            continue

        new_this = 0
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            description = _strip_html(item.findtext("description") or "")
            pub_date = item.findtext("pubDate")

            if not title or not link:
                continue

            ext_id = link.split("?")[0].rstrip("/").split("/")[-1]
            if ext_id in seen_ids:
                continue
            seen_ids.add(ext_id)

            # Filtra roles relevantes
            title_lower = title.lower()
            if not any(k in title_lower for k in (
                "data engineer", "analytics engineer", "engenheiro de dados",
                "data platform", "databricks engineer", "bi engineer",
                "data developer", "big data", "dbt engineer",
            )):
                continue

            # Location do RSS
            location = item.findtext("{http://www.w3.org/2005/Atom}content") or "Remote"
            company_tag = item.findtext("{https://jobicy.com/}company") or "Unknown"
            location_tag = item.findtext("{https://jobicy.com/}location") or "Remote LATAM"

            posted_at = _parse_pubdate(pub_date)

            out.append(JobPosting(
                ats="jobicy",
                company_handle=company_tag[:50],
                external_id=ext_id,
                title=title,
                location=location_tag,
                remote_flag=True,
                description=description[:2000],
                url=link,
                posted_at=posted_at,
                raw={"_company_label": company_tag},
            ))
            new_this += 1

        logger.info("Jobicy [%s]: %d DE jobs", feed_url[-40:], new_this)
        time.sleep(POLITE_DELAY)

    logger.info("Jobicy TOTAL: %d jobs", len(out))
    return out


# Registry
LATAM_ADAPTERS = {
    "getonboard": fetch_getonboard,
    "jobicy": fetch_jobicy,
}
