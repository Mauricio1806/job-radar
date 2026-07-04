"""
Adapters base + Greenhouse + Lever + Ashby + SmartRecruiters

Cada adapter implementa `fetch(handle) -> list[JobPosting]`.
Todos retornam o mesmo schema canônico (JobPosting) pra alimentar o pipeline.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
POLITE_DELAY = 2.0  # segundos entre requests por host


@dataclass
class JobPosting:
    """Schema canônico — qualquer adapter retorna isto."""
    ats: str
    company_handle: str
    external_id: str            # ID original no ATS
    title: str
    location: str               # texto livre
    remote_flag: bool
    description: str            # HTML ou texto
    url: str
    posted_at: Optional[datetime] = None
    department: Optional[str] = None
    recruiter_name: Optional[str] = None
    recruiter_email: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)

    def stable_id(self, company_id: int) -> str:
        """Hash estável para deduplicação."""
        import hashlib
        key = f"{company_id}|{self.title.lower().strip()}|{self.location.lower().strip()}"
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


class AdapterError(Exception):
    pass


def _http_get(url: str, params: dict | None = None) -> dict | list:
    """Wrapper com retry leve e validação JSON."""
    for attempt in (1, 2, 3):
        try:
            response = requests.get(
                url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT
            )
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
        for token in ("remote", "anywhere", "work from home", "distributed", "home-based")
    )


# ──────────────────────────────────────────────────────────────────────
# GREENHOUSE
# ──────────────────────────────────────────────────────────────────────
def fetch_greenhouse(handle: str) -> list[JobPosting]:
    """
    API pública: GET boards-api.greenhouse.io/v1/boards/{handle}/jobs?content=true
    """
    url = f"https://boards-api.greenhouse.io/v1/boards/{handle}/jobs"
    data = _http_get(url, params={"content": "true"})
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    out: list[JobPosting] = []
    for j in jobs:
        location = (j.get("location") or {}).get("name", "") or ""
        content = _strip_html(j.get("content", ""))
        out.append(JobPosting(
            ats="greenhouse",
            company_handle=handle,
            external_id=str(j.get("id")),
            title=j.get("title", "").strip(),
            location=location,
            remote_flag=_detect_remote(location, content[:500]),
            description=content,
            url=j.get("absolute_url", ""),
            posted_at=_parse_iso(j.get("updated_at")),
            department=(j.get("departments", [{}])[0] or {}).get("name") if j.get("departments") else None,
            raw=j,
        ))
    time.sleep(POLITE_DELAY)
    return out


# ──────────────────────────────────────────────────────────────────────
# LEVER
# ──────────────────────────────────────────────────────────────────────
def fetch_lever(handle: str) -> list[JobPosting]:
    """
    API pública: GET api.lever.co/v0/postings/{handle}?mode=json
    Lever às vezes expõe recruiter via `categories.team` + `applicationType`.
    """
    url = f"https://api.lever.co/v0/postings/{handle}"
    data = _http_get(url, params={"mode": "json"})
    postings = data if isinstance(data, list) else []
    out: list[JobPosting] = []
    for p in postings:
        categories = p.get("categories", {}) or {}
        location = categories.get("location", "") or ""
        commitment = categories.get("commitment", "")
        workplace = categories.get("workplaceType", "") or p.get("workplaceType", "")
        description = _strip_html(p.get("descriptionPlain") or p.get("description", ""))
        out.append(JobPosting(
            ats="lever",
            company_handle=handle,
            external_id=p.get("id", ""),
            title=p.get("text", "").strip(),
            location=location,
            remote_flag=_detect_remote(location, workplace, commitment, description[:500]),
            description=description,
            url=p.get("hostedUrl", ""),
            posted_at=_parse_epoch_ms(p.get("createdAt")),
            department=categories.get("team"),
            raw=p,
        ))
    time.sleep(POLITE_DELAY)
    return out


# ──────────────────────────────────────────────────────────────────────
# ASHBY
# ──────────────────────────────────────────────────────────────────────
def fetch_ashby(handle: str) -> list[JobPosting]:
    """
    API pública: GET api.ashbyhq.com/posting-api/job-board/{handle}
    """
    url = f"https://api.ashbyhq.com/posting-api/job-board/{handle}"
    data = _http_get(url)
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    out: list[JobPosting] = []
    for j in jobs:
        location = j.get("locationName", "") or ""
        is_remote = bool(j.get("isRemote", False))
        out.append(JobPosting(
            ats="ashby",
            company_handle=handle,
            external_id=j.get("id", ""),
            title=j.get("title", "").strip(),
            location=location,
            remote_flag=is_remote or _detect_remote(location),
            description=_strip_html(j.get("descriptionHtml", "")),
            url=j.get("jobUrl", ""),
            posted_at=_parse_iso(j.get("publishedAt")),
            department=j.get("department"),
            raw=j,
        ))
    time.sleep(POLITE_DELAY)
    return out


# ──────────────────────────────────────────────────────────────────────
# SMARTRECRUITERS
# ──────────────────────────────────────────────────────────────────────
def fetch_smartrecruiters(handle: str) -> list[JobPosting]:
    """
    API pública: GET api.smartrecruiters.com/v1/companies/{handle}/postings
    Paginação via offset + limit.
    """
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
        is_remote = bool(location_obj.get("remote", False))
        out.append(JobPosting(
            ats="smartrecruiters",
            company_handle=handle,
            external_id=str(p.get("id", "")),
            title=p.get("name", "").strip(),
            location=location,
            remote_flag=is_remote or _detect_remote(location),
            description="",  # detalhes precisam de /postings/{id}, fetched on-demand
            url=p.get("applyUrl") or p.get("ref", ""),
            posted_at=_parse_iso(p.get("releasedDate") or p.get("createdOn")),
            department=(p.get("department") or {}).get("label"),
            raw=p,
        ))
    time.sleep(POLITE_DELAY)
    return out


# ──────────────────────────────────────────────────────────────────────
# RECRUITEE
# ──────────────────────────────────────────────────────────────────────
def fetch_recruitee(handle: str) -> list[JobPosting]:
    """
    API pública: GET {handle}.recruitee.com/api/offers/
    """
    url = f"https://{handle}.recruitee.com/api/offers/"
    data = _http_get(url)
    offers = data.get("offers", []) if isinstance(data, dict) else []
    out: list[JobPosting] = []
    for o in offers:
        location = o.get("location", "") or ", ".join(o.get("city", []) or [])
        out.append(JobPosting(
            ats="recruitee",
            company_handle=handle,
            external_id=str(o.get("id", "")),
            title=o.get("title", "").strip(),
            location=location,
            remote_flag=bool(o.get("remote", False)) or _detect_remote(location),
            description=_strip_html(o.get("description", "")),
            url=o.get("careers_url") or o.get("url", ""),
            posted_at=_parse_iso(o.get("created_at")),
            department=o.get("department"),
            recruiter_email=(o.get("recruiter") or {}).get("email"),
            recruiter_name=(o.get("recruiter") or {}).get("name"),
            raw=o,
        ))
    time.sleep(POLITE_DELAY)
    return out


# ──────────────────────────────────────────────────────────────────────
# WORKABLE
# ──────────────────────────────────────────────────────────────────────
def fetch_workable(handle: str) -> list[JobPosting]:
    """
    API pública (widget interno): GET apply.workable.com/api/v1/widget/accounts/{handle}?details=true
    """
    url = f"https://apply.workable.com/api/v1/widget/accounts/{handle}"
    data = _http_get(url, params={"details": "true"})
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    out: list[JobPosting] = []
    for j in jobs:
        location_parts = [j.get("city"), j.get("country")]
        location = ", ".join(filter(None, location_parts))
        out.append(JobPosting(
            ats="workable",
            company_handle=handle,
            external_id=j.get("shortcode", ""),
            title=j.get("title", "").strip(),
            location=location,
            remote_flag=bool(j.get("telecommuting", False)) or _detect_remote(location),
            description=_strip_html(j.get("description", "")),
            url=f"https://apply.workable.com/{handle}/j/{j.get('shortcode')}/",
            posted_at=_parse_iso(j.get("published")),
            department=j.get("department"),
            raw=j,
        ))
    time.sleep(POLITE_DELAY)
    return out


# ──────────────────────────────────────────────────────────────────────
# TEAMTAILOR
# ──────────────────────────────────────────────────────────────────────
def fetch_teamtailor(handle: str, public_token: str = "") -> list[JobPosting]:
    """
    Teamtailor: GET api.teamtailor.com/v1/jobs com header X-Api-Version.
    O token público é o mesmo embedado no JS do site da empresa.
    Como cada empresa tem token próprio, fazemos fallback pra scrape do .html público.
    Aqui implementamos a versão mínima usando o widget JSON-LD da página pública.
    """
    url = f"https://{handle}.teamtailor.com/jobs.json"
    try:
        data = _http_get(url)
    except AdapterError:
        return []
    jobs = data.get("jobs", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    out: list[JobPosting] = []
    for j in jobs:
        location = j.get("location", {}).get("name", "") if isinstance(j.get("location"), dict) else (j.get("location", "") or "")
        out.append(JobPosting(
            ats="teamtailor",
            company_handle=handle,
            external_id=str(j.get("id", "")),
            title=j.get("title", "").strip(),
            location=location,
            remote_flag=bool(j.get("remote-status", "").lower() == "fully") or _detect_remote(location),
            description=_strip_html(j.get("body") or j.get("pitch", "")),
            url=j.get("url", ""),
            posted_at=_parse_iso(j.get("created-at")),
            department=j.get("department"),
            raw=j,
        ))
    time.sleep(POLITE_DELAY)
    return out


# ──────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────
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
        # Lever usa epoch em ms
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (ValueError, TypeError):
        return None


# Registry de adapters
ADAPTERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "recruitee": fetch_recruitee,
    "workable": fetch_workable,
    "teamtailor": fetch_teamtailor,
}

# Lazy-load aggregators (evita ciclo de import)
def _load_aggregators():
    from adapters.aggregators import AGGREGATOR_ADAPTERS
    ADAPTERS.update(AGGREGATOR_ADAPTERS)


def _load_consultancies():
    from adapters.consultancies import CONSULTANCY_ADAPTERS
    ADAPTERS.update(CONSULTANCY_ADAPTERS)


def fetch_for(ats: str, handle: str) -> list[JobPosting]:
    """Roteia pro adapter certo."""
    if ats in ("remotive", "remoteok", "himalayas", "arbeitnow", "wwr") and ats not in ADAPTERS:
        _load_aggregators()
    if ats in ("dataart", "softserve", "intellias") and ats not in ADAPTERS:
        _load_consultancies()
    fn = ADAPTERS.get(ats)
    if fn is None:
        logger.warning("no adapter for ATS '%s' (handle=%s)", ats, handle)
        return []
    try:
        return fn(handle)
    except AdapterError as exc:
        logger.error("adapter %s failed for %s: %s", ats, handle, exc)
        return []


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Smoke test contra ATS reais
    for ats, handle in [
        ("greenhouse", "airbnb"),
        ("lever", "notion"),
        ("ashby", "ashby"),
    ]:
        try:
            jobs = fetch_for(ats, handle)
            print(f"{ats}/{handle}: {len(jobs)} jobs")
            if jobs:
                print(f"  ex: {jobs[0].title} @ {jobs[0].location}")
        except Exception as exc:
            print(f"{ats}/{handle}: ERROR {exc}")
