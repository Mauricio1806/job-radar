"""
LATAM Remote Sources v17
========================
Fixes:
- Get on Board: remove expand[] do search endpoint (causava 422)
- Remotive: filtro de título mais permissivo
- WWR: inclui categorias de data
- NOVO: RemoteRocketship RSS — 300+ vagas DE remote LATAM
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
MAX_AGE_DAYS = 7

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/xml, */*",
}

BLOCKED_COUNTRY_ONLY = (
    "chile", "mexico", "colombia", "argentina", "peru",
    "uruguay", "paraguay", "venezuela", "ecuador",
)

DE_TITLE_KEYWORDS = (
    "data engineer", "analytics engineer", "engenheiro de dados",
    "data platform", "databricks", "bi engineer", "dbt engineer",
    "data developer", "big data engineer", "ingeniero de datos",
    "data ops", "dataops", "pipeline engineer",
)


def _is_too_old(published_at: datetime | None) -> bool:
    if not published_at:
        return False
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    return published_at < datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)


def _parse_iso(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _parse_pubdate(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(value)
    except (ValueError, TypeError):
        return None


def _is_brazil_accessible(location: str, description: str) -> bool:
    loc_lower = (location or "").lower()
    desc_lower = (description or "")[:500].lower()
    combined = loc_lower + " " + desc_lower
    if any(k in combined for k in ("brazil", "brasil", "br ", "anywhere", "worldwide",
                                    "latam", "latin america", "all latam")):
        return True
    for country in BLOCKED_COUNTRY_ONLY:
        if country in loc_lower:
            if not any(k in combined for k in ("latam", "latin america", "anywhere", "worldwide")):
                return False
    return True


def _is_de_title(title: str) -> bool:
    t = title.lower()
    return any(k in t for k in DE_TITLE_KEYWORDS)


# ──────────────────────────────────────────────────────────────────────
# GET ON BOARD (API pública)
# ──────────────────────────────────────────────────────────────────────
GOB_BASE = "https://www.getonbrd.com/api/v0"


def fetch_getonboard(handle: str = "all") -> list[JobPosting]:
    seen_ids: set[str] = set()
    out: list[JobPosting] = []

    # Endpoint 1: categoria (suporta expand[])
    category_url = f"{GOB_BASE}/categories/data-science-analytics/jobs?per_page=100&expand[]=company"
    try:
        resp = requests.get(category_url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        items = resp.json().get("data", [])
        for item in items:
            job = _parse_gob_job(item, seen_ids)
            if job:
                out.append(job)
        logger.info("GetOnBoard category: %d jobs", len(out))
    except (requests.RequestException, ValueError) as exc:
        logger.error("GetOnBoard category: %s", exc)

    time.sleep(POLITE_DELAY)

    # Endpoint 2: search SEM expand[] (causava 422)
    search_terms = ["data engineer", "analytics engineer", "databricks"]
    for term in search_terms:
        search_url = f"{GOB_BASE}/search/jobs?q={term.replace(' ', '+')}&per_page=50"
        try:
            resp = requests.get(search_url, headers=HEADERS, timeout=TIMEOUT)
            if resp.status_code == 422:
                logger.warning("GetOnBoard search 422 for '%s' — skipping", term)
                continue
            resp.raise_for_status()
            data = resp.json()
            items = data.get("data", []) if isinstance(data, dict) else data
            before = len(out)
            for item in items:
                job = _parse_gob_job(item, seen_ids)
                if job:
                    out.append(job)
            logger.info("GetOnBoard search '%s': %d new jobs", term, len(out) - before)
        except (requests.RequestException, ValueError) as exc:
            logger.error("GetOnBoard search '%s': %s", term, exc)
        time.sleep(POLITE_DELAY)

    logger.info("GetOnBoard TOTAL: %d remote jobs", len(out))
    return out


def _parse_gob_job(item: dict, seen_ids: set) -> JobPosting | None:
    attrs = item.get("attributes", {}) or item
    title = (attrs.get("title") or "").strip()
    if not title or not _is_de_title(title):
        return None

    remote = attrs.get("remote", False)
    if not remote:
        return None

    locations = attrs.get("locations") or []
    location = (", ".join(str(l) for l in locations[:2])
                if isinstance(locations, list) else str(locations)) or "Remote LATAM"

    company_obj = attrs.get("company") or {}
    if isinstance(company_obj, dict):
        company_name = ((company_obj.get("data", {}) or {})
                        .get("attributes", {}).get("name", "")
                        or company_obj.get("name", "") or "Unknown")
    else:
        company_name = "Unknown"

    description = _strip_html(attrs.get("description", "") or attrs.get("functions", "") or "")

    if not _is_brazil_accessible(location, description):
        return None

    published_at = _parse_iso(attrs.get("published_at") or attrs.get("updated_at"))
    if _is_too_old(published_at):
        return None

    url = attrs.get("url") or attrs.get("applyUrl") or ""
    if not url:
        url = f"https://www.getonbrd.com/jobs/{item.get('id', '')}"

    ext_id = str(item.get("id", "")) or url.split("/")[-1]
    if ext_id in seen_ids:
        return None
    seen_ids.add(ext_id)

    return JobPosting(
        ats="getonboard", company_handle=company_name[:50],
        external_id=ext_id, title=title, location=location,
        remote_flag=True, description=description[:2000],
        url=url, posted_at=published_at,
        raw={"_company_label": company_name},
    )


# ──────────────────────────────────────────────────────────────────────
# REMOTIVE — API JSON
# ──────────────────────────────────────────────────────────────────────
def fetch_remotive(handle: str = "data") -> list[JobPosting]:
    try:
        resp = requests.get("https://remotive.com/api/remote-jobs",
                            params={"category": "data"}, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        jobs = resp.json().get("jobs", [])
    except (requests.RequestException, ValueError) as exc:
        logger.error("Remotive: %s", exc)
        return []

    out: list[JobPosting] = []
    for j in jobs:
        title = (j.get("title") or "").strip()
        if not _is_de_title(title):
            continue
        published_at = _parse_iso(j.get("publication_date"))
        # Remotive não reporta datas recentes com precisão — sem filtro de data
        company_name = j.get("company_name", "Unknown")
        out.append(JobPosting(
            ats="remotive", company_handle=company_name[:50],
            external_id=str(j.get("id", "")), title=title,
            location=j.get("candidate_required_location", "") or "Worldwide",
            remote_flag=True,
            description=_strip_html(j.get("description", ""))[:2000],
            url=j.get("url", ""), posted_at=published_at,
            raw={"_company_label": company_name},
        ))

    logger.info("Remotive: %d DE jobs", len(out))
    time.sleep(POLITE_DELAY)
    return out


# ──────────────────────────────────────────────────────────────────────
# HIMALAYAS — API JSON
# ──────────────────────────────────────────────────────────────────────
def fetch_himalayas(handle: str = "data-engineer") -> list[JobPosting]:
    out: list[JobPosting] = []
    seen: set[str] = set()
    for query in ("data engineer", "analytics engineer", "databricks engineer"):
        try:
            resp = requests.get("https://himalayas.app/jobs/api",
                                params={"title": query}, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            jobs = resp.json().get("jobs", [])
        except (requests.RequestException, ValueError) as exc:
            logger.error("Himalayas '%s': %s", query, exc)
            continue
        for j in jobs:
            ext_id = str(j.get("guid") or j.get("id", ""))
            if ext_id in seen:
                continue
            seen.add(ext_id)
            title = (j.get("title") or "").strip()
            published_at = _parse_iso(j.get("pubDate") or j.get("publishedAt"))
            # Se não tem data, dá benefício da dúvida (pode ser nova)
            if published_at and _is_too_old(published_at):
                continue
            company_obj = j.get("company") or {}
            company_name = (company_obj.get("name") if isinstance(company_obj, dict) else str(company_obj)) or "Unknown"
            locations = j.get("locationRestrictions") or j.get("locations") or []
            location = (", ".join(str(l) for l in locations[:2]) if isinstance(locations, list) else str(locations)) or "Remote"
            out.append(JobPosting(
                ats="himalayas", company_handle=company_name[:50],
                external_id=ext_id, title=title, location=location,
                remote_flag=True,
                description=_strip_html(j.get("excerpt") or j.get("description", ""))[:2000],
                url=j.get("applicationLink") or f"https://himalayas.app/jobs/{j.get('slug','')}",
                posted_at=published_at,
                raw={"_company_label": company_name},
            ))
        time.sleep(POLITE_DELAY)
    logger.info("Himalayas TOTAL: %d jobs", len(out))
    return out


# ──────────────────────────────────────────────────────────────────────
# WE WORK REMOTELY — RSS (múltiplas categorias)
# ──────────────────────────────────────────────────────────────────────
WWR_FEEDS = [
    "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
    "https://weworkremotely.com/remote-jobs.rss",
]


def fetch_wwr(handle: str = "data") -> list[JobPosting]:
    out: list[JobPosting] = []
    seen: set[str] = set()
    for feed_url in WWR_FEEDS:
        try:
            resp = requests.get(feed_url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            if not resp.content.strip():
                continue
            root = ET.fromstring(resp.content)
        except (requests.RequestException, ET.ParseError) as exc:
            logger.warning("WWR '%s': %s", feed_url[-40:], exc)
            continue
        for item in root.iter("item"):
            title_full = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if not _is_de_title(title_full):
                continue
            ext_id = link.split("/")[-1] if link else title_full[:40]
            if ext_id in seen:
                continue
            seen.add(ext_id)
            published_at = _parse_pubdate(item.findtext("pubDate"))
            if _is_too_old(published_at):
                continue
            if ":" in title_full:
                company_name, _, title = title_full.partition(":")
                company_name, title = company_name.strip(), title.strip()
            else:
                company_name, title = "WWR", title_full
            description = _strip_html(item.findtext("description") or "")
            out.append(JobPosting(
                ats="wwr", company_handle=company_name[:50],
                external_id=ext_id, title=title, location="Worldwide",
                remote_flag=True, description=description[:2000],
                url=link, posted_at=published_at,
                raw={"_company_label": company_name},
            ))
        time.sleep(POLITE_DELAY)
    logger.info("WWR TOTAL: %d DE jobs", len(out))
    return out


# ──────────────────────────────────────────────────────────────────────
# REMOTE ROCKETSHIP — HTML scraping (RSS tem XML inválido)
# ──────────────────────────────────────────────────────────────────────
import re as _re


def fetch_remoterocketship(handle: str = "all") -> list[JobPosting]:
    from bs4 import BeautifulSoup
    RRS_URLS = [
        "https://www.remoterocketship.com/country/latin-america/jobs/data-engineer/",
        "https://www.remoterocketship.com/country/brazil/jobs/data-engineer/",
        "https://www.remoterocketship.com/jobs/analytics-engineer/",
    ]
    out: list[JobPosting] = []
    seen: set[str] = set()
    for url in RRS_URLS:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as exc:
            logger.warning("RemoteRocketship '%s': %s", url[-40:], exc)
            continue
        for link_el in soup.find_all("a", href=_re.compile(r"^/jobs/[^/]+/[^/]+/")):
            href = link_el.get("href", "")
            ext_id = href.rstrip("/").split("/")[-1]
            if not ext_id or ext_id in seen:
                continue
            seen.add(ext_id)
            raw_text = link_el.get_text(" ", strip=True)
            if " at " in raw_text:
                parts = raw_text.rsplit(" at ", 1)
                title, company_name = parts[0].strip(), parts[1].strip()
            else:
                title, company_name = raw_text, "Unknown"
            if not _is_de_title(title):
                continue
            out.append(JobPosting(
                ats="remoterocketship", company_handle=company_name[:50],
                external_id=ext_id, title=title, location="Remote LATAM",
                remote_flag=True, description="",
                url=f"https://www.remoterocketship.com{href}",
                posted_at=None, raw={"_company_label": company_name},
            ))
        logger.info("RemoteRocketship '%s': %d total so far", url[-40:], len(out))
        time.sleep(POLITE_DELAY)
    logger.info("RemoteRocketship TOTAL: %d jobs", len(out))
    return out


# ──────────────────────────────────────────────────────────────────────
# JOBICY RSS
# ──────────────────────────────────────────────────────────────────────
JOBICY_FEEDS = [
    "https://jobicy.com/?feed=job_feed&job_categories=data-science&job_types=full-time&search_region=latam",
    "https://jobicy.com/?feed=job_feed&job_categories=data-science&job_types=full-time&search_region=brazil",
    "https://jobicy.com/?feed=job_feed&job_categories=data-science&job_types=contractor",
]


def fetch_jobicy(handle: str = "all") -> list[JobPosting]:
    seen: set[str] = set()
    out: list[JobPosting] = []
    for feed_url in JOBICY_FEEDS:
        try:
            resp = requests.get(feed_url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except (requests.RequestException, ET.ParseError) as exc:
            logger.warning("Jobicy: %s", exc)
            continue
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if not title or not link or not _is_de_title(title):
                continue
            ext_id = link.split("?")[0].rstrip("/").split("/")[-1]
            if ext_id in seen:
                continue
            seen.add(ext_id)
            published_at = _parse_pubdate(item.findtext("pubDate"))
            if _is_too_old(published_at):
                continue
            company_tag = item.findtext("{https://jobicy.com/}company") or "Unknown"
            location_tag = item.findtext("{https://jobicy.com/}location") or "Remote LATAM"
            out.append(JobPosting(
                ats="jobicy", company_handle=company_tag[:50],
                external_id=ext_id, title=title, location=location_tag,
                remote_flag=True,
                description=_strip_html(item.findtext("description") or "")[:2000],
                url=link, posted_at=published_at,
                raw={"_company_label": company_tag},
            ))
    logger.info("Jobicy TOTAL: %d jobs", len(out))
    return out




# ──────────────────────────────────────────────────────────────────────
# WELLFOUND (AngelList) — RSS feed startups US contratando LATAM
# ──────────────────────────────────────────────────────────────────────
WELLFOUND_FEEDS = [
    "https://wellfound.com/jobs.rss?role=data-engineer&remote=true",
    "https://wellfound.com/jobs.rss?role=analytics-engineer&remote=true",
]

def fetch_wellfound(handle: str = "all") -> list[JobPosting]:
    seen: set[str] = set()
    out: list[JobPosting] = []

    for feed_url in WELLFOUND_FEEDS:
        try:
            resp = requests.get(feed_url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            if not resp.content.strip():
                continue
            root = ET.fromstring(resp.content)
        except (requests.RequestException, ET.ParseError) as exc:
            logger.warning("Wellfound [%s]: %s", feed_url[-40:], exc)
            continue

        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            description = _strip_html(item.findtext("description") or "")
            pub_date = item.findtext("pubDate")

            if not title or not link or not _is_de_title(title):
                continue

            ext_id = link.rstrip("/").split("/")[-1]
            if ext_id in seen:
                continue
            seen.add(ext_id)

            published_at = _parse_pubdate(pub_date)
            if _is_too_old(published_at):
                continue

            # Empresa geralmente está no título "Role at Company"
            company_name = "Unknown"
            if " at " in title:
                parts = title.rsplit(" at ", 1)
                title = parts[0].strip()
                company_name = parts[1].strip()

            out.append(JobPosting(
                ats="wellfound",
                company_handle=company_name[:50],
                external_id=ext_id,
                title=title,
                location="Remote",
                remote_flag=True,
                description=description[:2000],
                url=link,
                posted_at=published_at,
                raw={"_company_label": company_name},
            ))
        time.sleep(POLITE_DELAY)

    logger.info("Wellfound TOTAL: %d jobs", len(out))
    return out


# ──────────────────────────────────────────────────────────────────────
# GET ON BOARD — Playwright pra search (422 na API REST)
# ──────────────────────────────────────────────────────────────────────
def fetch_getonboard_playwright(handle: str = "all") -> list[JobPosting]:
    """
    Usa Playwright pra acessar o search do Get on Board
    que dá 422 via API REST mas funciona no browser.
    """
    from bs4 import BeautifulSoup
    import re

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright não instalado — skip GOB search")
        return []

    search_terms = ["data engineer", "analytics engineer", "databricks engineer"]
    seen: set[str] = set()
    out: list[JobPosting] = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
            )
            for term in search_terms:
                try:
                    page = context.new_page()
                    url = f"https://www.getonbrd.com/search/jobs?q={term.replace(' ', '+')}&remote=true"
                    page.goto(url, wait_until="networkidle", timeout=20000)
                    import time as _time
                    _time.sleep(3)
                    html = page.content()
                    page.close()

                    soup = BeautifulSoup(html, "html.parser")
                    for link in soup.find_all("a", href=re.compile(r"/jobs/[a-z0-9-]+")):
                        href = link.get("href", "")
                        title = link.get_text(" ", strip=True)
                        if not title or not _is_de_title(title):
                            continue
                        ext_id = href.rstrip("/").split("/")[-1]
                        if ext_id in seen:
                            continue
                        seen.add(ext_id)
                        full_url = f"https://www.getonbrd.com{href}" if not href.startswith("http") else href
                        out.append(JobPosting(
                            ats="getonboard",
                            company_handle="getonboard-search",
                            external_id=ext_id,
                            title=title,
                            location="Remote LATAM",
                            remote_flag=True,
                            description="",
                            url=full_url,
                            posted_at=None,
                            raw={"_company_label": "Get on Board"},
                        ))
                    logger.info("GOB Playwright search '%s': %d jobs total", term, len(out))
                except Exception as exc:
                    logger.warning("GOB Playwright search '%s': %s", term, exc)
            browser.close()
    except Exception as exc:
        logger.warning("GOB Playwright failed: %s", exc)

    logger.info("GetOnBoard Playwright TOTAL: %d jobs", len(out))
    return out


LATAM_ADAPTERS = {
    "getonboard": fetch_getonboard,
    "remotive": fetch_remotive,
    "himalayas": fetch_himalayas,
    "wwr": fetch_wwr,
    "jobicy": fetch_jobicy,
    "wellfound": fetch_wellfound,
    "getonboard_pw": fetch_getonboard_playwright,
}
