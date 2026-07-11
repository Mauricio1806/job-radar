"""
Adzuna Adapter v9 — Enriched
=============================
Melhorias:
1. Segue redirect_url e captura URL FINAL (empresa real, não Adzuna)
2. Extrai descrição COMPLETA da página via JSON-LD JobPosting schema
3. Identifica domínios de ATS confiáveis (JazzHR, Greenhouse, Lever, etc.)
4. Retorna URL final direto no Telegram (você aplica DIRETO no ATS da empresa)
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from adapters import JobPosting, _strip_html

logger = logging.getLogger(__name__)

BASE_URL = "https://api.adzuna.com/v1/api/jobs"
POLITE_DELAY = 1.5
ENRICH_DELAY = 1.0        # delay entre requests de enrich
TIMEOUT = 15
MAX_DAYS_OLD = 3
RESULTS_PER_PAGE = 50
ENRICH_MAX_JOBS = 30      # limita enrich pra não estourar timeout

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9,pt;q=0.8",
}

# ATS/portais confiáveis — significa que o link vai direto pra empresa real
CONFIDENT_DOMAINS = {
    "jazzhr.com", "applytojob.com", "boards.greenhouse.io", "greenhouse.io",
    "jobs.lever.co", "lever.co", "kenoby.com", "gupy.io",
    "jobs.ashbyhq.com", "ashbyhq.com", "linkedin.com", "workable.com",
    "smartrecruiters.com", "workday.com", "wd1.myworkdayjobs.com",
    "myworkdayjobs.com", "solides.jobs", "solides.com",
    "personio.com", "personio.de", "jobvite.com", "icims.com",
    "bamboohr.com", "recruitee.com", "teamtailor.com", "breezy.hr",
    "polymer.co", "notion.site", "ycombinator.com", "vagas.com.br",
    "catho.com.br", "infojobs.com.br", "trampos.co", "trabalhabrasil.com.br"
}


QUERIES = [
    {"id": "br-data-engineer", "country": "br", "what": "data engineer"},
    {"id": "br-analytics-engineer", "country": "br", "what": "analytics engineer"},
    {"id": "br-senior-data-engineer", "country": "br", "what": "senior data engineer"},
    {"id": "us-de-remote-latam", "country": "us", "what": "data engineer remote latin america"},
    {"id": "us-de-latam", "country": "us", "what": "data engineer latam"},
    {"id": "us-analytics-latam", "country": "us", "what": "analytics engineer latin america"},
    {"id": "ca-de-remote-latam", "country": "ca", "what": "data engineer remote latin america"},
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
        "results_per_page": RESULTS_PER_PAGE,
        "what": params.get("what", ""),
        "max_days_old": MAX_DAYS_OLD,
        "sort_by": "date",
        "content-type": "application/json",
    }

    try:
        response = requests.get(url, params=query_params, timeout=30)
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


def _extract_from_jsonld(soup: BeautifulSoup) -> str | None:
    """Procura schema.org JobPosting no HTML — mais confiável."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            raw = script.string or script.get_text()
            if not raw:
                continue
            data = json.loads(raw)
            # Pode vir como lista
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict) and (
                    item.get("@type") == "JobPosting" or
                    "JobPosting" in str(item.get("@type", ""))
                ):
                    desc = item.get("description", "")
                    if desc:
                        return _strip_html(desc)
        except (json.JSONDecodeError, TypeError, AttributeError):
            continue
    return None


def _extract_description(html: str) -> str:
    """Multi-fallback pra extrair descrição da vaga."""
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return ""

    # 1) JSON-LD schema.org JobPosting (padrão google jobs)
    desc = _extract_from_jsonld(soup)
    if desc and len(desc) > 200:
        return desc[:6000]

    # 2) Meta og:description
    og = soup.find("meta", {"property": "og:description"})
    if og:
        content = og.get("content", "")
        if content and len(content) > 100:
            return content[:6000]

    # 3) Common containers de descrição
    for selector in [
        {"name": "div", "class_": "job-description"},
        {"name": "div", "class_": "description"},
        {"name": "div", "id": "job-details"},
        {"name": "section", "class_": "job-post"},
        {"name": "article"},
        {"name": "main"},
    ]:
        el = soup.find(**selector)
        if el:
            text = _strip_html(el.get_text(" ", strip=True))
            if len(text) > 300:
                return text[:6000]

    # 4) Fallback: body
    body = soup.find("body")
    if body:
        return _strip_html(body.get_text(" ", strip=True))[:6000]

    return ""


def _follow_redirect(url: str) -> tuple[str, str, str]:
    """
    Segue o redirect e retorna (final_url, final_domain, description).
    """
    try:
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT,
                                allow_redirects=True)
        if response.status_code >= 400:
            return url, "", ""
        final_url = str(response.url)
        final_domain = urlparse(final_url).netloc.lower().lstrip("www.")
        description = _extract_description(response.text)
        return final_url, final_domain, description
    except requests.RequestException as exc:
        logger.warning("redirect follow failed [%s]: %s", url[:80], exc)
        return url, "", ""


def _domain_confidence(domain: str) -> str:
    """Retorna badge de confiança do domínio."""
    d = domain.lower().lstrip("www.")
    for known in CONFIDENT_DOMAINS:
        if known in d:
            return "✓"
    return "?"


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
                                "100% remoto", "remoto", "latam")
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


def _looks_like_data_role(title: str) -> bool:
    """Filtro leve pra decidir se vale a pena enriquecer."""
    t = title.lower()
    return any(k in t for k in (
        "data engineer", "data platform", "analytics engineer", "bi engineer",
        "engenheiro de dados", "engenheiro dados", "ingeniero de datos",
        "data ops", "dataops", "data developer", "big data", "lakehouse",
    ))


def fetch_adzuna(handle: str = "all") -> list[JobPosting]:
    seen_ids: set[str] = set()
    out: list[JobPosting] = []

    # Fase 1: pegar todos jobs base da API
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

        logger.info("Adzuna [%s]: %d jobs (%d após dedup)",
                    query_id, len(results), new_this_page)
        time.sleep(POLITE_DELAY)

    logger.info("Adzuna phase 1: %d unique jobs", len(out))

    # Fase 2: enriquecer os que parecem DE (segue redirect, pega descrição real)
    to_enrich = [j for j in out if _looks_like_data_role(j.title)]
    to_enrich = to_enrich[:ENRICH_MAX_JOBS]  # limita pra não estourar
    logger.info("Adzuna phase 2: enriching %d/%d jobs", len(to_enrich), len(out))

    for i, job in enumerate(to_enrich):
        final_url, final_domain, rich_desc = _follow_redirect(job.url)

        if rich_desc and len(rich_desc) > len(job.description):
            job.description = rich_desc
        if final_url and final_url != job.url:
            job.url = final_url
        if final_domain:
            job.raw["_final_domain"] = final_domain
            job.raw["_domain_confidence"] = _domain_confidence(final_domain)

        if (i + 1) % 5 == 0:
            logger.info("Adzuna enrich: %d/%d done", i + 1, len(to_enrich))
        time.sleep(ENRICH_DELAY)

    logger.info("Adzuna DONE: %d jobs (max_days_old=%d, %d enriched)",
                len(out), MAX_DAYS_OLD, len(to_enrich))
    return out


ADZUNA_ADAPTERS = {
    "adzuna": fetch_adzuna,
}
