"""
LATAM Remote Sources v16
========================
Fontes onde vagas DE LATAM remote USD realmente aparecem.

Inclui:
1. Get on Board — API pública, Ruzora/Artefact/staffing LATAM
2. Remotive — API JSON gratuita, category=data, vagas globais remote
3. Himalayas — API JSON, remote-first, startups US
4. We Work Remotely — RSS feed, vagas remote globais
5. Jobicy — RSS feed LATAM/Brazil

Filtros críticos:
- max_days_old=7 (descarta vagas velhas de uma vez)
- Bloqueia vagas com restrição geográfica explícita (Chile only, etc.)
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

import requests

from adapters import JobPosting, _strip_html

logger = logging.getLogger(__name__)

POLITE_DELAY = 2.0
TIMEOUT = 20
MAX_AGE_DAYS = 7   # Descarta vagas mais velhas que 7 dias

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/xml, */*",
}

# Regiões bloqueadas — vagas com localização explícita nesses países
# quando NÃO mencionam "all latam" ou "anywhere"
BLOCKED_COUNTRY_ONLY = (
    "chile", "mexico", "colombia", "argentina", "peru",
    "uruguay", "paraguay", "venezuela", "ecuador",
)


def _is_too_old(published_at: datetime | None) -> bool:
    if not published_at:
        return False
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    return published_at < cutoff


def _parse_iso(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _is_brazil_accessible(location: str, description: str) -> bool:
    """
    Retorna False se a vaga está restrita a um país específico que NÃO é Brasil.
    Retorna True se é "Remote LATAM", "Anywhere", "Brazil" ou sem restrição clara.
    """
    loc_lower = (location or "").lower()
    desc_lower = (description or "")[:500].lower()
    combined = loc_lower + " " + desc_lower

    # Se menciona Brasil explicitamente = OK
    if any(k in combined for k in ("brazil", "brasil", "br ")):
        return True

    # Se é "anywhere" ou "worldwide" = OK
    if any(k in combined for k in ("anywhere", "worldwide", "latam", "latin america",
                                    "all latam", "toda latam", "anywhere in latam")):
        return True

    # Se menciona APENAS outro país sem incluir Brasil = bloqueia
    for country in BLOCKED_COUNTRY_ONLY:
        if country in loc_lower:
            # Verifica se tem "latam" ou "anywhere" em algum lugar
            if not any(k in combined for k in ("latam", "latin america", "anywhere", "worldwide")):
                return False

    return True  # Benefício da dúvida


# ──────────────────────────────────────────────────────────────────────
# GET ON BOARD
# ──────────────────────────────────────────────────────────────────────
GOB_BASE = "https://www.getonbrd.com/api/v0"
GOB_ENDPOINTS = [
    f"{GOB_BASE}/categories/data-science-analytics/jobs?per_page=100&expand[]=company",
    f"{GOB_BASE}/search/jobs?q=data+engineer&per_page=50&expand[]=company",
    f"{GOB_BASE}/search/jobs?q=analytics+engineer&per_page=50&expand[]=company",
    f"{GOB_BASE}/search/jobs?q=engenheiro+de+dados&per_page=50&expand[]=company",
    f"{GOB_BASE}/search/jobs?q=databricks+engineer&per_page=50&expand[]=company",
]


def _parse_gob_job(item: dict) -> JobPosting | None:
    attrs = item.get("attributes", {}) or {}
    if not attrs:
        attrs = item

    title = (attrs.get("title") or "").strip()
    if not title:
        return None

    remote = attrs.get("remote", False)
    remote_modality = attrs.get("remote_modality", "") or ""
    if not remote and "remote" not in remote_modality.lower():
        return None

    locations = attrs.get("locations") or []
    if isinstance(locations, list):
        location = ", ".join(str(loc) for loc in locations[:2]) if locations else "Remote LATAM"
    else:
        location = str(locations) or "Remote LATAM"

    company_obj = attrs.get("company") or {}
    if isinstance(company_obj, dict):
        company_name = ((company_obj.get("data", {}) or {})
                        .get("attributes", {}).get("name", "")
                        or company_obj.get("name", "") or "Unknown")
    else:
        company_name = str(company_obj) or "Unknown"

    description = _strip_html(
        attrs.get("description", "") or attrs.get("functions", "") or ""
    )

    # Filtro: vaga acessível para Brasil?
    if not _is_brazil_accessible(location, description):
        return None

    published_at = _parse_iso(attrs.get("published_at") or attrs.get("updated_at"))

    # Filtro de data
    if _is_too_old(published_at):
        return None

    url = attrs.get("url") or attrs.get("applyUrl") or ""
    if not url:
        job_id = item.get("id", "")
        url = f"https://www.getonbrd.com/jobs/{job_id}"

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
        posted_at=published_at,
        raw={"_company_label": company_name},
    )


def fetch_getonboard(handle: str = "all") -> list[JobPosting]:
    seen_ids: set[str] = set()
    out: list[JobPosting] = []

    for endpoint in GOB_ENDPOINTS:
        try:
            response = requests.get(endpoint, headers=HEADERS, timeout=TIMEOUT)
            if response.status_code == 404:
                continue
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            logger.error("GetOnBoard [%s]: %s", endpoint[-40:], exc)
            continue

        items = data.get("data", []) if isinstance(data, dict) else data
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

        logger.info("GetOnBoard [%s...]: %d jobs", endpoint[-40:], new_this)
        time.sleep(POLITE_DELAY)

    logger.info("GetOnBoard TOTAL: %d remote jobs (max %d days)", len(out), MAX_AGE_DAYS)
    return out


# ──────────────────────────────────────────────────────────────────────
# REMOTIVE — API JSON gratuita
# ──────────────────────────────────────────────────────────────────────
def fetch_remotive(handle: str = "data") -> list[JobPosting]:
    url = "https://remotive.com/api/remote-jobs"
    try:
        response = requests.get(url, params={"category": "data"}, headers=HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("Remotive: %s", exc)
        return []

    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    out: list[JobPosting] = []
    seen: set[str] = set()
    for j in jobs:
        ext_id = str(j.get("id", ""))
        if ext_id in seen:
            continue
        seen.add(ext_id)

        title = (j.get("title") or "").strip()
        # Filtro de título — só DE/Analytics Engineer
        title_lower = title.lower()
        if not any(k in title_lower for k in (
            "data engineer", "analytics engineer", "engenheiro de dados",
            "data platform", "databricks", "dbt engineer", "bi engineer",
        )):
            continue

        published_at = _parse_iso(j.get("publication_date"))
        if _is_too_old(published_at):
            continue

        company_name = j.get("company_name", "Unknown")
        location = j.get("candidate_required_location", "") or "Worldwide"
        description = _strip_html(j.get("description", ""))

        out.append(JobPosting(
            ats="remotive",
            company_handle=company_name[:50],
            external_id=ext_id,
            title=title,
            location=location,
            remote_flag=True,
            description=description[:2000],
            url=j.get("url", ""),
            posted_at=published_at,
            raw={"_company_label": company_name},
        ))

    logger.info("Remotive: %d DE/Analytics jobs (max %d days)", len(out), MAX_AGE_DAYS)
    time.sleep(POLITE_DELAY)
    return out


# ──────────────────────────────────────────────────────────────────────
# HIMALAYAS — API JSON
# ──────────────────────────────────────────────────────────────────────
def fetch_himalayas(handle: str = "data-engineer") -> list[JobPosting]:
    url = "https://himalayas.app/jobs/api"
    out: list[JobPosting] = []
    seen: set[str] = set()

    for query in ("data engineer", "analytics engineer", "databricks"):
        try:
            response = requests.get(url, params={"title": query}, headers=HEADERS, timeout=TIMEOUT)
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            logger.error("Himalayas [%s]: %s", query, exc)
            continue

        jobs = data.get("jobs", []) if isinstance(data, dict) else []
        for j in jobs:
            ext_id = str(j.get("guid") or j.get("id", ""))
            if ext_id in seen:
                continue
            seen.add(ext_id)

            title = (j.get("title") or "").strip()
            published_at = _parse_iso(j.get("pubDate") or j.get("publishedAt"))
            if _is_too_old(published_at):
                continue

            company_obj = j.get("company") or {}
            company_name = (company_obj.get("name") if isinstance(company_obj, dict)
                            else str(company_obj)) or "Unknown"

            locations = j.get("locationRestrictions") or j.get("locations") or []
            location = (", ".join(str(l) for l in locations[:2])
                        if isinstance(locations, list) else str(locations)) or "Remote"

            description = _strip_html(j.get("excerpt") or j.get("description", ""))
            apply_url = j.get("applicationLink") or f"https://himalayas.app/jobs/{j.get('slug', '')}"

            out.append(JobPosting(
                ats="himalayas",
                company_handle=company_name[:50],
                external_id=ext_id,
                title=title,
                location=location,
                remote_flag=True,
                description=description[:2000],
                url=apply_url,
                posted_at=published_at,
                raw={"_company_label": company_name},
            ))

        time.sleep(POLITE_DELAY)

    logger.info("Himalayas TOTAL: %d jobs", len(out))
    return out


# ──────────────────────────────────────────────────────────────────────
# WE WORK REMOTELY — RSS
# ──────────────────────────────────────────────────────────────────────
def fetch_wwr(handle: str = "data") -> list[JobPosting]:
    feed_url = "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss"
    try:
        response = requests.get(feed_url, headers=HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
        root = ET.fromstring(response.content)
    except (requests.RequestException, ET.ParseError) as exc:
        logger.error("WWR: %s", exc)
        return []

    out: list[JobPosting] = []
    seen: set[str] = set()

    for item in root.iter("item"):
        title_full = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description = _strip_html(item.findtext("description") or "")
        pub_date = item.findtext("pubDate")

        title_lower = title_full.lower()
        if not any(k in title_lower for k in (
            "data engineer", "analytics engineer", "databricks",
            "data platform", "dbt", "engenheiro de dados",
        )):
            continue

        ext_id = link.split("/")[-1] if link else title_full[:40]
        if ext_id in seen:
            continue
        seen.add(ext_id)

        # Parse company: "Company: Title"
        if ":" in title_full:
            company_name, _, title = title_full.partition(":")
            company_name = company_name.strip()
            title = title.strip()
        else:
            company_name = "WWR"
            title = title_full

        published_at = _parse_pubdate(pub_date)
        if _is_too_old(published_at):
            continue

        out.append(JobPosting(
            ats="wwr",
            company_handle=company_name[:50],
            external_id=ext_id,
            title=title,
            location="Worldwide",
            remote_flag=True,
            description=description[:2000],
            url=link,
            posted_at=published_at,
            raw={"_company_label": company_name},
        ))

    logger.info("WWR: %d DE jobs (max %d days)", len(out), MAX_AGE_DAYS)
    time.sleep(POLITE_DELAY)
    return out


def _parse_pubdate(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(value)
    except (ValueError, TypeError):
        return None


# ──────────────────────────────────────────────────────────────────────
# JOBICY RSS
# ──────────────────────────────────────────────────────────────────────
JOBICY_FEEDS = [
    "https://jobicy.com/?feed=job_feed&job_categories=data-science&job_types=full-time&search_region=latam",
    "https://jobicy.com/?feed=job_feed&job_categories=data-science&job_types=full-time&search_region=brazil",
    "https://jobicy.com/?feed=job_feed&job_categories=data-science&job_types=contractor",
]


def fetch_jobicy(handle: str = "all") -> list[JobPosting]:
    seen_ids: set[str] = set()
    out: list[JobPosting] = []

    for feed_url in JOBICY_FEEDS:
        try:
            response = requests.get(feed_url, headers=HEADERS, timeout=TIMEOUT)
            response.raise_for_status()
            root = ET.fromstring(response.content)
        except (requests.RequestException, ET.ParseError) as exc:
            logger.warning("Jobicy [%s]: %s", feed_url[-30:], exc)
            continue

        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            description = _strip_html(item.findtext("description") or "")
            pub_date = item.findtext("pubDate")

            if not title or not link:
                continue

            title_lower = title.lower()
            if not any(k in title_lower for k in (
                "data engineer", "analytics engineer", "engenheiro de dados",
                "data platform", "databricks", "bi engineer", "dbt",
            )):
                continue

            ext_id = link.split("?")[0].rstrip("/").split("/")[-1]
            if ext_id in seen_ids:
                continue
            seen_ids.add(ext_id)

            published_at = _parse_pubdate(pub_date)
            if _is_too_old(published_at):
                continue

            company_tag = item.findtext("{https://jobicy.com/}company") or "Unknown"
            location_tag = item.findtext("{https://jobicy.com/}location") or "Remote LATAM"

            out.append(JobPosting(
                ats="jobicy",
                company_handle=company_tag[:50],
                external_id=ext_id,
                title=title,
                location=location_tag,
                remote_flag=True,
                description=description[:2000],
                url=link,
                posted_at=published_at,
                raw={"_company_label": company_tag},
            ))

    logger.info("Jobicy TOTAL: %d jobs", len(out))
    return out


# Registry
LATAM_ADAPTERS = {
    "getonboard": fetch_getonboard,
    "remotive": fetch_remotive,
    "himalayas": fetch_himalayas,
    "wwr": fetch_wwr,
    "jobicy": fetch_jobicy,
}
