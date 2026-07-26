"""
Adapters — Registry central v45
================================
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}
REQUEST_TIMEOUT = 20
POLITE_DELAY = 2.0


@dataclass
class JobPosting:
    ats: str
    company_handle: str
    external_id: str
    title: str
    location: str
    remote_flag: bool
    description: str
    url: str
    posted_at: Optional[datetime] = None
    department: Optional[str] = None
    recruiter_name: Optional[str] = None
    recruiter_email: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)

    def stable_id(self, company_id: int) -> str:
        import hashlib
        key = f"{company_id}|{self.title.lower().strip()}|{self.location.lower().strip()}"
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


class AdapterError(Exception):
    pass


def _http_get(url: str, params: dict | None = None) -> dict | list:
    for attempt in (1, 2, 3):
        try:
            response = requests.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if response.status_code == 429:
                logger.warning("rate limited on %s, sleeping", url)
                time.sleep(10 * attempt)
                continue
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            if attempt == 3:
                raise AdapterError(f"fetch {url} failed: {exc}") from exc
            time.sleep(2 ** attempt)
    raise AdapterError(f"unreachable: {url}")


def _strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html or "").replace("&nbsp;", " ").strip()


def _detect_remote(*texts: str) -> bool:
    haystack = " ".join(t.lower() for t in texts if t)
    return any(
        token in haystack
        for token in ("remote", "anywhere", "work from home", "distributed", "home-based",
                      "latam", "latin america", "remoto", "teletrabajo")
    )


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return _parse_epoch_ms(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _parse_epoch_ms(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (ValueError, TypeError):
        return None


# ── GREENHOUSE ─────────────────────────────────────────────────────────

def _parse_greenhouse_jobs(handle: str, jobs: list) -> list[JobPosting]:
    out: list[JobPosting] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    for j in jobs:
        location = (j.get("location") or {}).get("name", "") or ""
        posted = _parse_iso(j.get("updated_at"))
        if posted:
            if posted.tzinfo is None:
                posted = posted.replace(tzinfo=timezone.utc)
            if posted < cutoff:
                continue
        content = _strip_html(j.get("content", ""))
        out.append(JobPosting(
            ats="greenhouse", company_handle=handle,
            external_id=str(j.get("id")),
            title=j.get("title", "").strip(), location=location,
            remote_flag=_detect_remote(location, content[:500]),
            description=content, url=j.get("absolute_url", ""),
            posted_at=posted,
            department=(j.get("departments", [{}])[0] or {}).get("name") if j.get("departments") else None,
            raw=j,
        ))
    time.sleep(POLITE_DELAY)
    return out


def fetch_greenhouse_new(handle: str) -> list[JobPosting]:
    """Greenhouse novo (job-boards.greenhouse.io) com endpoint JSON _data="""
    headers_json = {**HEADERS, "Accept": "application/json"}
    out: list[JobPosting] = []
    page = 1
    while True:
        url = f"https://job-boards.greenhouse.io/{handle}"
        try:
            data = _http_get(url, params={"page": page, "_data": ""})
        except AdapterError as exc:
            logger.warning("greenhouse_new %s p%d: %s", handle, page, exc)
            break
        posts = []
        if isinstance(data, dict):
            job_posts = data.get("jobPosts") or {}
            posts = job_posts.get("data", []) or []
            total_pages = job_posts.get("total_pages", 1) or 1
        elif isinstance(data, list):
            posts = data
            total_pages = 1
        for j in posts:
            title = (j.get("title") or "").strip()
            location_obj = j.get("location") or {}
            location = location_obj.get("name", "") if isinstance(location_obj, dict) else str(location_obj)
            content_html = j.get("content", "") or j.get("description", "") or ""
            description = _strip_html(content_html)
            remote = _detect_remote(location, description[:500])
            posted = _parse_iso(j.get("updated_at") or j.get("published_at"))
            job_id = str(j.get("id", ""))
            apply_url = j.get("absolute_url") or f"https://job-boards.greenhouse.io/{handle}/jobs/{job_id}"
            out.append(JobPosting(
                ats="greenhouse", company_handle=handle,
                external_id=job_id, title=title, location=location,
                remote_flag=remote, description=description[:2000],
                url=apply_url, posted_at=posted, department=None, raw=j,
            ))
        if page >= total_pages:
            break
        page += 1
        time.sleep(POLITE_DELAY / 2)
    time.sleep(POLITE_DELAY)
    return out


def fetch_greenhouse(handle: str) -> list[JobPosting]:
    data = None
    for url in [
        f"https://boards-api.greenhouse.io/v1/boards/{handle}/jobs",
        f"https://job-boards.greenhouse.io/api/v3/boards/{handle}/jobs",
    ]:
        try:
            data = _http_get(url, params={"content": "true"})
            break
        except AdapterError as exc:
            if "404" in str(exc):
                continue
            raise
    if data is None:
        logger.warning("greenhouse %s: 404 em endpoints clássicos, tentando _data=", handle)
        return fetch_greenhouse_new(handle)
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    return _parse_greenhouse_jobs(handle, jobs)


# ── LEVER ──────────────────────────────────────────────────────────────

def fetch_lever(handle: str) -> list[JobPosting]:
    url = f"https://api.lever.co/v0/postings/{handle}"
    data = _http_get(url, params={"mode": "json"})
    postings = data if isinstance(data, list) else []
    out: list[JobPosting] = []
    for p in postings:
        categories = p.get("categories", {}) or {}
        location = categories.get("location", "") or ""
        description = _strip_html(p.get("descriptionPlain") or p.get("description", ""))
        out.append(JobPosting(
            ats="lever", company_handle=handle,
            external_id=p.get("id", ""),
            title=p.get("text", "").strip(), location=location,
            remote_flag=_detect_remote(location, categories.get("workplaceType", ""), description[:500]),
            description=description, url=p.get("hostedUrl", ""),
            posted_at=_parse_epoch_ms(p.get("createdAt")),
            department=categories.get("team"),
            raw=p,
        ))
    time.sleep(POLITE_DELAY)
    return out


# ── ASHBY ──────────────────────────────────────────────────────────────

def fetch_ashby(handle: str) -> list[JobPosting]:
    """Ashby REST public API: GET /posting-api/job-board/{handle}"""
    url = f"https://api.ashbyhq.com/posting-api/job-board/{handle}?includeCompensation=true"
    try:
        response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise AdapterError(f"ashby {handle}: {exc}") from exc
    postings = data.get("jobPostings", []) or []
    out: list[JobPosting] = []
    for p in postings:
        title = (p.get("title") or "").strip()
        location = p.get("locationName") or ""
        remote = bool(p.get("isRemote", False)) or _detect_remote(location)
        dept = p.get("departmentName") or p.get("teamName") or ""
        published = p.get("publishedAt") or p.get("updatedAt")
        job_id = p.get("id", "")
        apply_url = p.get("applyUrl") or f"https://jobs.ashbyhq.com/{handle}/{job_id}"
        out.append(JobPosting(
            ats="ashby", company_handle=handle,
            external_id=str(job_id), title=title, location=location,
            remote_flag=remote,
            description=_strip_html(p.get("descriptionHtml", ""))[:2000],
            url=apply_url, posted_at=_parse_iso(published),
            department=dept, raw=p,
        ))
    time.sleep(POLITE_DELAY)
    return out


# ── SMARTRECRUITERS ────────────────────────────────────────────────────

def fetch_smartrecruiters(handle: str) -> list[JobPosting]:
    url = f"https://api.smartrecruiters.com/v1/companies/{handle}/postings"
    all_postings: list[dict] = []
    offset = 0
    while True:
        data = _http_get(url, params={"limit": 100, "offset": offset})
        content = data.get("content", []) if isinstance(data, dict) else []
        if not content:
            break
        all_postings.extend(content)
        if len(content) < 100:
            break
        offset += 100
        time.sleep(POLITE_DELAY / 2)
    out: list[JobPosting] = []
    for p in all_postings:
        location_obj = p.get("location", {}) or {}
        location = ", ".join(filter(None, [
            location_obj.get("city"), location_obj.get("region"), location_obj.get("country")
        ]))
        out.append(JobPosting(
            ats="smartrecruiters", company_handle=handle,
            external_id=str(p.get("id", "")),
            title=p.get("name", "").strip(), location=location,
            remote_flag=bool(location_obj.get("remote", False)) or _detect_remote(location),
            description="", url=p.get("applyUrl") or p.get("ref", ""),
            posted_at=_parse_iso(p.get("releasedDate") or p.get("createdOn")),
            department=(p.get("department") or {}).get("label"),
            raw=p,
        ))
    time.sleep(POLITE_DELAY)
    return out


# ── WORKABLE ───────────────────────────────────────────────────────────

def fetch_workable(handle: str) -> list[JobPosting]:
    url = f"https://apply.workable.com/api/v1/widget/accounts/{handle}"
    data = _http_get(url, params={"details": "true"})
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    out: list[JobPosting] = []
    for j in jobs:
        location = ", ".join(filter(None, [j.get("city"), j.get("country")]))
        out.append(JobPosting(
            ats="workable", company_handle=handle,
            external_id=j.get("shortcode", ""),
            title=j.get("title", "").strip(), location=location,
            remote_flag=bool(j.get("telecommuting", False)) or _detect_remote(location),
            description=_strip_html(j.get("description", "")),
            url=f"https://apply.workable.com/{handle}/j/{j.get('shortcode')}/",
            posted_at=_parse_iso(j.get("published")),
            department=j.get("department"), raw=j,
        ))
    time.sleep(POLITE_DELAY)
    return out


# ── REGISTRY ───────────────────────────────────────────────────────────

ADAPTERS: dict[str, Any] = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "workable": fetch_workable,
}


def _load_adzuna():
    from adapters.adzuna import ADZUNA_ADAPTERS
    ADAPTERS.update(ADZUNA_ADAPTERS)


def _load_latam():
    from adapters.latam_sources import LATAM_ADAPTERS
    ADAPTERS.update(LATAM_ADAPTERS)


def _load_playwright():
    from adapters.playwright_scraper import PLAYWRIGHT_ADAPTERS
    ADAPTERS.update(PLAYWRIGHT_ADAPTERS)


def _load_platform():
    from adapters.platform_search import PLATFORM_ADAPTERS
    ADAPTERS.update(PLATFORM_ADAPTERS)


def fetch_for(ats: str, handle: str) -> list[JobPosting]:
    if ats == "adzuna" and "adzuna" not in ADAPTERS:
        _load_adzuna()
    if ats in ("getonboard", "jobicy", "remotive", "himalayas", "wwr",
               "wellfound", "getonboard_pw") and ats not in ADAPTERS:
        _load_latam()
    if ats in ("ashby_global", "greenhouse_global", "lever_global",
               "himalayas_global") and ats not in ADAPTERS:
        _load_platform()
    if ats in ("koombea", "tecla", "devlane", "parallelstaff", "distillery",
               "scopic", "nearsure", "rootstrap") and ats not in ADAPTERS:
        _load_playwright()
    fn = ADAPTERS.get(ats)
    if fn is None:
        logger.warning("no adapter for ATS '%s' (handle=%s)", ats, handle)
        return []
    try:
        return fn(handle)
    except AdapterError as exc:
        logger.error("adapter %s failed for %s: %s", ats, handle, exc)
        return []
    except Exception as exc:
        logger.exception("adapter %s crashed for %s: %s", ats, handle, exc)
        return []
