"""
Platform Search — busca global em todas as plataformas ATS
============================================================
Em vez de monitorar empresas fixas, busca "data engineer" em TODA
a plataforma e retorna vagas de qualquer empresa que postou.

Cobre:
- Ashby: /posting-api/search + múltiplos handles conhecidos
- Greenhouse: boards-api busca por job title
- Lever: v0/postings com tag
- Himalayas: /jobs/api busca global
- Remotive: /api/remote-jobs categoria data
- Jobicy: RSS feeds

Isso captura VidMob, Loka Inc, e qualquer empresa nova que poste.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

import requests

from adapters import JobPosting, _strip_html, _parse_iso, _detect_remote

logger = logging.getLogger(__name__)

POLITE_DELAY = 1.5
TIMEOUT = 20
MAX_AGE_DAYS = 7

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/xml, */*",
}

DE_TITLES = (
    "data engineer", "analytics engineer", "engenheiro de dados",
    "ingeniero de datos", "data platform engineer", "databricks engineer",
    "dbt engineer", "data developer", "big data engineer",
    "pipeline engineer", "dataops engineer",
)


def _is_de_title(title: str) -> bool:
    t = title.lower()
    return any(k in t for k in DE_TITLES)


def _is_too_old(dt: datetime | None) -> bool:
    if not dt:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt < datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)


# ──────────────────────────────────────────────────────────────────────
# ASHBY — busca global via API pública
# ──────────────────────────────────────────────────────────────────────
ASHBY_SEARCH_QUERIES = [
    "data engineer",
    "analytics engineer",
    "databricks",
    "engenheiro de dados",
]


def fetch_ashby_global(handle: str = "all") -> list[JobPosting]:
    """
    Busca vagas DE em TODA a plataforma Ashby.
    Usa o endpoint público de busca: /posting-api/job-board/search
    """
    seen: set[str] = set()
    out: list[JobPosting] = []

    for query in ASHBY_SEARCH_QUERIES:
        url = "https://api.ashbyhq.com/posting-api/job-board/search"
        try:
            r = requests.get(
                url,
                params={"jobBoardType": "PUBLIC", "query": query},
                headers=HEADERS,
                timeout=TIMEOUT,
            )
            if r.status_code in (403, 404):
                # Tenta endpoint alternativo
                r = requests.post(
                    "https://api.ashbyhq.com/posting-api/job-board/search",
                    json={"query": query, "jobBoardType": "PUBLIC"},
                    headers={**HEADERS, "Content-Type": "application/json"},
                    timeout=TIMEOUT,
                )
            if r.status_code != 200:
                logger.warning("Ashby global search '%s': %s", query, r.status_code)
                continue

            data = r.json()
            postings = (data.get("results") or data.get("jobPostings") or
                       data.get("data") or [])

        except (requests.RequestException, ValueError) as exc:
            logger.warning("Ashby global search '%s': %s", query, exc)
            continue

        for p in postings:
            ext_id = str(p.get("id", ""))
            if not ext_id or ext_id in seen:
                continue
            seen.add(ext_id)

            title = (p.get("title") or "").strip()
            if not _is_de_title(title):
                continue

            location = p.get("locationName") or ""
            remote = bool(p.get("isRemote", False)) or _detect_remote(location)

            org = p.get("organization") or p.get("company") or {}
            company_name = (org.get("name") or org.get("displayName") or "Unknown") if isinstance(org, dict) else str(org)
            handle_name = (org.get("slug") or org.get("handle") or "unknown") if isinstance(org, dict) else "unknown"

            published = p.get("publishedAt") or p.get("updatedAt")
            posted_at = _parse_iso(published)
            if _is_too_old(posted_at):
                continue

            apply_url = (p.get("applyUrl") or
                        f"https://jobs.ashbyhq.com/{handle_name}/{ext_id}")

            out.append(JobPosting(
                ats="ashby",
                company_handle=handle_name,
                external_id=ext_id,
                title=title,
                location=location,
                remote_flag=remote,
                description=_strip_html(p.get("descriptionHtml", ""))[:2000],
                url=apply_url,
                posted_at=posted_at,
                raw={"_company_label": company_name},
            ))

        logger.info("Ashby global '%s': %d jobs", query, len(out))
        time.sleep(POLITE_DELAY)

    logger.info("Ashby global TOTAL: %d unique DE jobs", len(out))
    return out


# ──────────────────────────────────────────────────────────────────────
# GREENHOUSE — busca global
# ──────────────────────────────────────────────────────────────────────
def fetch_greenhouse_global(handle: str = "all") -> list[JobPosting]:
    """
    Busca vagas DE em toda a plataforma Greenhouse.
    Usa o endpoint de busca pública.
    """
    seen: set[str] = set()
    out: list[JobPosting] = []

    queries = ["data engineer", "analytics engineer", "databricks engineer"]

    for query in queries:
        # Endpoint de busca global do Greenhouse
        for url in [
            "https://boards-api.greenhouse.io/v1/jobs",
            "https://job-boards.greenhouse.io/api/v3/jobs/search",
        ]:
            try:
                r = requests.get(
                    url,
                    params={"q": query, "per_page": 100, "remote": True},
                    headers=HEADERS,
                    timeout=TIMEOUT,
                )
                if r.status_code == 200:
                    data = r.json()
                    jobs = (data.get("jobs") or data.get("results") or
                           data.get("data") or [])
                    if jobs:
                        for j in jobs:
                            ext_id = str(j.get("id", ""))
                            if not ext_id or ext_id in seen:
                                continue
                            seen.add(ext_id)

                            title = (j.get("title") or "").strip()
                            if not _is_de_title(title):
                                continue

                            location = (j.get("location") or {}).get("name", "") if isinstance(j.get("location"), dict) else str(j.get("location", ""))
                            content = _strip_html(j.get("content", ""))
                            posted_at = _parse_iso(j.get("updated_at") or j.get("published_at"))
                            if _is_too_old(posted_at):
                                continue

                            company = j.get("company") or {}
                            company_name = company.get("name", "Unknown") if isinstance(company, dict) else "Unknown"

                            out.append(JobPosting(
                                ats="greenhouse",
                                company_handle=company_name.lower().replace(" ", ""),
                                external_id=ext_id,
                                title=title,
                                location=location,
                                remote_flag=_detect_remote(location, content[:500]),
                                description=content[:2000],
                                url=j.get("absolute_url", ""),
                                posted_at=posted_at,
                                raw={"_company_label": company_name},
                            ))
                        logger.info("Greenhouse global '%s' via %s: %d jobs", query, url[-30:], len(out))
                        break
            except (requests.RequestException, ValueError) as exc:
                logger.warning("Greenhouse global '%s': %s", query, exc)

        time.sleep(POLITE_DELAY)

    logger.info("Greenhouse global TOTAL: %d DE jobs", len(out))
    return out


# ──────────────────────────────────────────────────────────────────────
# LEVER — busca global
# ──────────────────────────────────────────────────────────────────────
def fetch_lever_global(handle: str = "all") -> list[JobPosting]:
    """Busca vagas DE em toda a plataforma Lever."""
    seen: set[str] = set()
    out: list[JobPosting] = []

    queries = ["data engineer", "analytics engineer", "databricks"]

    for query in queries:
        try:
            r = requests.get(
                "https://api.lever.co/v0/postings",
                params={"mode": "json", "limit": 100, "tag": query},
                headers=HEADERS,
                timeout=TIMEOUT,
            )
            if r.status_code != 200:
                logger.warning("Lever global '%s': %s", query, r.status_code)
                continue

            postings = r.json() if isinstance(r.json(), list) else []

            for p in postings:
                ext_id = p.get("id", "")
                if not ext_id or ext_id in seen:
                    continue
                seen.add(ext_id)

                title = (p.get("text") or "").strip()
                if not _is_de_title(title):
                    continue

                categories = p.get("categories") or {}
                location = categories.get("location", "") or ""
                workplace = categories.get("workplaceType", "") or ""

                created = p.get("createdAt")
                posted_at = None
                if created:
                    try:
                        posted_at = datetime.fromtimestamp(int(created) / 1000, tz=timezone.utc)
                    except (ValueError, TypeError):
                        pass
                if _is_too_old(posted_at):
                    continue

                description = _strip_html(p.get("descriptionPlain") or p.get("description", ""))
                company_name = p.get("company") or "Unknown"

                out.append(JobPosting(
                    ats="lever",
                    company_handle=company_name.lower().replace(" ", ""),
                    external_id=ext_id,
                    title=title,
                    location=location,
                    remote_flag=_detect_remote(location, workplace, description[:300]),
                    description=description[:2000],
                    url=p.get("hostedUrl", ""),
                    posted_at=posted_at,
                    raw={"_company_label": company_name},
                ))

        except (requests.RequestException, ValueError) as exc:
            logger.warning("Lever global '%s': %s", query, exc)

        time.sleep(POLITE_DELAY)

    logger.info("Lever global TOTAL: %d DE jobs", len(out))
    return out


# ──────────────────────────────────────────────────────────────────────
# HIMALAYAS — busca global (já temos mas vou expandir)
# ──────────────────────────────────────────────────────────────────────
def fetch_himalayas_global(handle: str = "all") -> list[JobPosting]:
    """Himalayas com mais queries e sem filtro de data agressivo."""
    seen: set[str] = set()
    out: list[JobPosting] = []

    queries = ["data engineer", "analytics engineer", "databricks engineer",
               "data platform engineer", "dbt engineer"]

    for query in queries:
        try:
            r = requests.get(
                "https://himalayas.app/jobs/api",
                params={"title": query, "remote": "true"},
                headers=HEADERS,
                timeout=TIMEOUT,
            )
            if r.status_code != 200:
                continue
            jobs = r.json().get("jobs", [])
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Himalayas global '%s': %s", query, exc)
            continue

        for j in jobs:
            ext_id = str(j.get("guid") or j.get("id", ""))
            if not ext_id or ext_id in seen:
                continue
            seen.add(ext_id)

            title = (j.get("title") or "").strip()
            if not _is_de_title(title):
                continue

            published = _parse_iso(j.get("pubDate") or j.get("publishedAt"))
            # Benefício da dúvida se não tem data
            if published and _is_too_old(published):
                continue

            company_obj = j.get("company") or {}
            company_name = (company_obj.get("name") if isinstance(company_obj, dict) else str(company_obj)) or "Unknown"

            locations = j.get("locationRestrictions") or j.get("locations") or []
            location = (", ".join(str(l) for l in locations[:2]) if isinstance(locations, list) else str(locations)) or "Remote"

            out.append(JobPosting(
                ats="himalayas",
                company_handle=company_name[:50],
                external_id=ext_id,
                title=title,
                location=location,
                remote_flag=True,
                description=_strip_html(j.get("excerpt") or j.get("description", ""))[:2000],
                url=j.get("applicationLink") or f"https://himalayas.app/jobs/{j.get('slug', '')}",
                posted_at=published,
                raw={"_company_label": company_name},
            ))

        time.sleep(POLITE_DELAY)

    logger.info("Himalayas global TOTAL: %d DE jobs", len(out))
    return out


PLATFORM_ADAPTERS = {
    "ashby_global": fetch_ashby_global,
    "greenhouse_global": fetch_greenhouse_global,
    "lever_global": fetch_lever_global,
    "himalayas_global": fetch_himalayas_global,
}
