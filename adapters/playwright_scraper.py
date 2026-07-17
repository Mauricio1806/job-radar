"""
Playwright Adapter — sites JS-rendered sem API pública
=======================================================
Usa browser headless pra renderizar JS e extrair vagas.

Alvos:
- Koombea (careers.koombea.com)
- Devlane (jobs.devlane.com)
- TECLA (jobs.tecla.io)
- ParallelStaff (parallelstaff.com/jobs)
- Distillery (distillery.com/jobs)
- Scopic (scopicsoftware.com/careers)

Instalação no CI: playwright install --with-deps chromium
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from adapters import JobPosting, _strip_html

logger = logging.getLogger(__name__)

DE_TITLE_KEYWORDS = (
    "data engineer", "analytics engineer", "engenheiro de dados",
    "ingeniero de datos", "data platform", "databricks", "bi engineer",
    "dbt engineer", "data developer", "pipeline engineer", "big data",
    "data ops", "dataops",
)

REMOTE_KEYWORDS = (
    "remote", "remoto", "latam", "latin america", "anywhere",
    "distributed", "work from home", "home office",
)


def _is_de_title(title: str) -> bool:
    t = title.lower()
    return any(k in t for k in DE_TITLE_KEYWORDS)


def _is_remote(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in REMOTE_KEYWORDS)


def _get_playwright():
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError:
        logger.warning("Playwright não instalado — skipping JS scrapers")
        return None


def _scrape_with_playwright(url: str, wait_selector: str = "a",
                             timeout: int = 15000) -> str | None:
    """Retorna HTML renderizado da página ou None se falhar."""
    sync_playwright = _get_playwright()
    if not sync_playwright:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()
            page.goto(url, wait_until="networkidle", timeout=timeout)
            try:
                page.wait_for_selector(wait_selector, timeout=5000)
            except Exception:
                pass
            time.sleep(2)
            html = page.content()
            browser.close()
            return html
    except Exception as exc:
        logger.warning("Playwright failed [%s]: %s", url, exc)
        return None


# ──────────────────────────────────────────────────────────────────────
# KOOMBEA
# ──────────────────────────────────────────────────────────────────────
def fetch_koombea(handle: str = "all") -> list[JobPosting]:
    from bs4 import BeautifulSoup
    import re

    url = "https://www.koombea.com/jobs/"
    html = _scrape_with_playwright(url, wait_selector=".job-listing, .career, h2, h3")
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    out: list[JobPosting] = []
    seen: set[str] = set()

    for link in soup.find_all("a", href=re.compile(r"/jobs?/|/career|/position")):
        title = link.get_text(" ", strip=True)
        href = link.get("href", "")
        if not title or not href or not _is_de_title(title):
            continue
        if href in seen:
            continue
        seen.add(href)
        full_url = href if href.startswith("http") else f"https://www.koombea.com{href}"
        out.append(JobPosting(
            ats="playwright", company_handle="koombea",
            external_id=href.split("/")[-1] or href[-20:],
            title=title, location="Remote LATAM",
            remote_flag=True, description="",
            url=full_url, posted_at=None,
            raw={"_company_label": "Koombea"},
        ))

    logger.info("Koombea: %d DE jobs", len(out))
    return out


# ──────────────────────────────────────────────────────────────────────
# TECLA
# ──────────────────────────────────────────────────────────────────────
def fetch_tecla(handle: str = "all") -> list[JobPosting]:
    from bs4 import BeautifulSoup
    import re

    url = "https://jobs.tecla.io/"
    html = _scrape_with_playwright(url, wait_selector=".job-card, .position, article")
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    out: list[JobPosting] = []
    seen: set[str] = set()

    for link in soup.find_all("a", href=re.compile(r"/job|/position|/opening")):
        title = link.get_text(" ", strip=True)
        href = link.get("href", "")
        if not title or not _is_de_title(title):
            continue
        if href in seen:
            continue
        seen.add(href)
        full_url = href if href.startswith("http") else f"https://jobs.tecla.io{href}"
        out.append(JobPosting(
            ats="playwright", company_handle="tecla",
            external_id=href.split("/")[-1] or href[-20:],
            title=title, location="Remote LATAM",
            remote_flag=True, description="",
            url=full_url, posted_at=None,
            raw={"_company_label": "TECLA"},
        ))

    logger.info("TECLA: %d DE jobs", len(out))
    return out


# ──────────────────────────────────────────────────────────────────────
# DEVLANE
# ──────────────────────────────────────────────────────────────────────
def fetch_devlane(handle: str = "all") -> list[JobPosting]:
    from bs4 import BeautifulSoup
    import re

    url = "https://jobs.devlane.com/"
    html = _scrape_with_playwright(url, wait_selector=".job, .position, h2, h3")
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    out: list[JobPosting] = []
    seen: set[str] = set()

    for link in soup.find_all("a", href=re.compile(r"/job|/position|/opening|/career")):
        title = link.get_text(" ", strip=True)
        href = link.get("href", "")
        if not title or not _is_de_title(title):
            continue
        if href in seen:
            continue
        seen.add(href)
        full_url = href if href.startswith("http") else f"https://jobs.devlane.com{href}"
        out.append(JobPosting(
            ats="playwright", company_handle="devlane",
            external_id=href.split("/")[-1] or href[-20:],
            title=title, location="Remote LATAM",
            remote_flag=True, description="",
            url=full_url, posted_at=None,
            raw={"_company_label": "Devlane"},
        ))

    logger.info("Devlane: %d DE jobs", len(out))
    return out


# ──────────────────────────────────────────────────────────────────────
# PARALLELSTAFF
# ──────────────────────────────────────────────────────────────────────
def fetch_parallelstaff(handle: str = "all") -> list[JobPosting]:
    from bs4 import BeautifulSoup
    import re

    url = "https://parallelstaff.com/jobs/"
    html = _scrape_with_playwright(url, wait_selector=".job, h2, h3, article")
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    out: list[JobPosting] = []
    seen: set[str] = set()

    for link in soup.find_all("a", href=re.compile(r"/job|/position|/opening")):
        title = link.get_text(" ", strip=True)
        href = link.get("href", "")
        if not title or not _is_de_title(title):
            continue
        if href in seen:
            continue
        seen.add(href)
        full_url = href if href.startswith("http") else f"https://parallelstaff.com{href}"
        out.append(JobPosting(
            ats="playwright", company_handle="parallelstaff",
            external_id=href.split("/")[-1] or href[-20:],
            title=title, location="Remote LATAM",
            remote_flag=True, description="",
            url=full_url, posted_at=None,
            raw={"_company_label": "ParallelStaff"},
        ))

    logger.info("ParallelStaff: %d DE jobs", len(out))
    return out


# ──────────────────────────────────────────────────────────────────────
# DISTILLERY
# ──────────────────────────────────────────────────────────────────────
def fetch_distillery(handle: str = "all") -> list[JobPosting]:
    from bs4 import BeautifulSoup
    import re

    url = "https://distillery.com/jobs/"
    html = _scrape_with_playwright(url, wait_selector=".job, h2, h3")
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    out: list[JobPosting] = []
    seen: set[str] = set()

    for link in soup.find_all("a", href=re.compile(r"/job|/position|/career|/opening")):
        title = link.get_text(" ", strip=True)
        href = link.get("href", "")
        if not title or not _is_de_title(title):
            continue
        if href in seen:
            continue
        seen.add(href)
        full_url = href if href.startswith("http") else f"https://distillery.com{href}"
        out.append(JobPosting(
            ats="playwright", company_handle="distillery",
            external_id=href.split("/")[-1] or href[-20:],
            title=title, location="Remote LATAM",
            remote_flag=True, description="",
            url=full_url, posted_at=None,
            raw={"_company_label": "Distillery"},
        ))

    logger.info("Distillery: %d DE jobs", len(out))
    return out


# ──────────────────────────────────────────────────────────────────────
# SCOPIC
# ──────────────────────────────────────────────────────────────────────
def fetch_scopic(handle: str = "all") -> list[JobPosting]:
    from bs4 import BeautifulSoup
    import re

    url = "https://scopicsoftware.com/careers/"
    html = _scrape_with_playwright(url, wait_selector=".job, h2, h3, .position")
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    out: list[JobPosting] = []
    seen: set[str] = set()

    for link in soup.find_all("a", href=re.compile(r"/job|/position|/career|/opening")):
        title = link.get_text(" ", strip=True)
        href = link.get("href", "")
        if not title or not _is_de_title(title):
            continue
        if href in seen:
            continue
        seen.add(href)
        full_url = href if href.startswith("http") else f"https://scopicsoftware.com{href}"
        out.append(JobPosting(
            ats="playwright", company_handle="scopic",
            external_id=href.split("/")[-1] or href[-20:],
            title=title, location="Remote",
            remote_flag=True, description="",
            url=full_url, posted_at=None,
            raw={"_company_label": "Scopic"},
        ))

    logger.info("Scopic: %d DE jobs", len(out))
    return out


PLAYWRIGHT_ADAPTERS = {
    "koombea": fetch_koombea,
    "tecla": fetch_tecla,
    "devlane": fetch_devlane,
    "parallelstaff": fetch_parallelstaff,
    "distillery": fetch_distillery,
    "scopic": fetch_scopic,
}
