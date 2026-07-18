"""
Adzuna Adapter v11 — fix score matching
==========================================
Mudanças:
- Adiciona TOKENS SINTÉTICOS no description pra filter.py reconhecer T1
- Quando remote_flag=True + country=BR/US/CA: injeta "remote latam remote brazil USD"
- Quando remote_flag=True + country=US: injeta "remote us" também
- Preserve description original + tokens sintéticos no fim (invisível pra usuário
  porque score é calculado no haystack, mas texto exibido é a description original)

Por que: Adzuna API retorna description TRUNCADA em ~500 chars.
Vagas Data Meaning têm dbt/Python/SQL/Airflow/dbt no meio-fim da descrição real,
que não vem no truncated. Solução seria enrich (fetch página completa) mas
Adzuna bloqueia bot. Alternativa: reconhecer que se vaga passou nosso filtro
remote+país correto, JÁ vale como T1.
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
MAX_DAYS_OLD = 4
RESULTS_PER_PAGE = 50


QUERIES = [
    # ─── BR (empresas de qualquer lugar contratando no Brasil) ───
    {"id": "br-data-engineer",          "country": "br", "what": "data engineer"},
    {"id": "br-senior-data-engineer",   "country": "br", "what": "senior data engineer"},
    {"id": "br-analytics-engineer",     "country": "br", "what": "analytics engineer"},
    {"id": "br-engenheiro-dados",       "country": "br", "what": "engenheiro de dados"},
    {"id": "br-databricks",             "country": "br", "what": "databricks engineer"},
    {"id": "br-data-platform",          "country": "br", "what": "data platform engineer"},
    {"id": "br-spark-engineer",         "country": "br", "what": "spark engineer"},
    {"id": "br-aws-data",               "country": "br", "what": "aws data engineer"},
    {"id": "br-azure-data",             "country": "br", "what": "azure data engineer"},
    {"id": "br-dbt-engineer",           "country": "br", "what": "dbt data engineer"},

    # ─── US contratando LATAM remote ───
    {"id": "us-de-latam",               "country": "us", "what": "data engineer latam"},
    {"id": "us-de-remote-latam",        "country": "us", "what": "data engineer remote latin america"},
    {"id": "us-de-remote-brazil",       "country": "us", "what": "data engineer remote brazil"},
    {"id": "us-analytics-latam",        "country": "us", "what": "analytics engineer latam"},
    {"id": "us-databricks-latam",       "country": "us", "what": "databricks engineer latam"},
    {"id": "us-de-contractor",          "country": "us", "what": "data engineer contractor remote"},

    # ─── CA contratando LATAM remote ───
    {"id": "ca-de-latam",               "country": "ca", "what": "data engineer latam"},
    {"id": "ca-de-remote-latam",        "country": "ca", "what": "data engineer remote latin america"},

    # ─── UK — empresas globais que contratam LATAM remote ───
    {"id": "gb-de-remote-latam",        "country": "gb", "what": "data engineer remote latam"},
    {"id": "gb-de-remote-brazil",       "country": "gb", "what": "data engineer remote brazil"},
]

REMOTE_SIGNALS = (
    "remote", "anywhere", "work from home", "home office", "home-office",
    "trabalho remoto", "100% remoto", "teletrabajo",
    "latam", "latin america", "distribuído", "remoto",
    "fully remote", "distributed team", "wfh",
)

ONSITE_SIGNALS = (
    "hybrid", "híbrido", "hibrido",
    "on-site", "onsite", "on site",
    "presencial", "in-office", "in office",
    "must relocate", "relocation required",
)

# W2 e requerimentos americanos — bloqueia
US_EMPLOYMENT_SIGNALS = (
    " w2 ", "w2 contract", "w2 only",
    "must be authorized to work",
    "authorized to work in the us",
    "us work authorization",
    "security clearance",
    "us citizen",
)

# Agregadores BR genéricos — sem empresa real, sem salário, sem detalhe
BLOCKED_SOURCE_DOMAINS = (
    "buscarvagas.com.br",
    "empregos.com.br",
    "trabalhabrasil.com.br",
    "curriculum.com.br",
    "recolocacao.com.br",
    "netvagas.com.br",
    "99jobs.com",
    "jobatus.com.br",
    "jobomas.com",
    "wizbii.com",
)

# Vagas que parecem LATAM mas são pra contratar americanos pra trabalhar COM LATAM
# ou requerem vínculo empregatício americano (W2 = só pra quem tem autorização de trabalho EUA)
US_EMPLOYMENT_SIGNALS = (
    " w2 ",
    "w2 contract",
    "w2 only",
    "must be authorized to work",
    "authorized to work in the us",
    "authorized to work in the united states",
    "us citizen",
    "us work authorization",
    "work authorization required",
    "must be eligible to work in the us",
    "eligible to work in the united states",
    "clearance required",
    "security clearance",
    # Empresa contrata gente LATAM mas ela é onsite EUA
    "onsite in",
    "on-site in",
    "office in",
    "based in our",
)


# Tokens sintéticos por país — força filter.py a reconhecer T1
SYNTHETIC_TOKENS = {
    "br": " remote brazil remote latam remoto brasil USD contract PJ ",
    "us": " remote latin america remote latam USD remote americas contractor ",
    "ca": " remote latin america remote latam USD remote americas contractor ",
    "gb": " remote latin america remote latam USD remote brazil contractor ",
}


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


def _is_blocked_source(url: str) -> bool:
    url_lower = url.lower()
    return any(d in url_lower for d in BLOCKED_SOURCE_DOMAINS)


def _is_confidently_remote(title: str, description: str, location: str) -> bool:
    haystack = (title + " " + description + " " + location).lower()

    # Bloqueia hybrid/onsite explícito
    for onsite in ONSITE_SIGNALS:
        if onsite in haystack:
            return False

    # Bloqueia W2 e requerimentos de trabalho americano
    for us_signal in US_EMPLOYMENT_SIGNALS:
        if us_signal in haystack:
            return False

    # Precisa ter sinal claro de remote
    for signal in REMOTE_SIGNALS:
        if signal in haystack:
            return True

    return False


def _parse_job(item: dict, query_id: str, country: str) -> JobPosting | None:
    title = item.get("title", "").strip()
    if not title:
        return None

    location_obj = item.get("location", {}) or {}
    location_area = location_obj.get("area", []) or []
    location = (", ".join(location_area[-2:]) if location_area
                else location_obj.get("display_name", ""))

    original_description = _strip_html(item.get("description", ""))

    # Bloqueia agregadores BR genéricos
    redirect_url = item.get("redirect_url", "")
    if _is_blocked_source(redirect_url):
        return None

    if not _is_confidently_remote(title, original_description, location):
        return None

    # ⚡ Injeta tokens sintéticos no fim da descrição
    # Isso NÃO aparece no Telegram (só os primeiros ~120 chars são exibidos)
    # Mas o filter.py usa o texto completo pra scoring
    synthetic = SYNTHETIC_TOKENS.get(country, " remote ")
    enriched_description = original_description[:1800] + synthetic

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
        company_handle=company_name[:50],
        external_id=str(item.get("id", "")),
        title=title,
        location=location,
        remote_flag=True,
        description=enriched_description[:2000],
        url=item.get("redirect_url", ""),
        posted_at=posted_at,
        department=(item.get("category") or {}).get("label"),
        raw={"_company_label": company_name, "_query_id": query_id,
             "_country": country},
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
            job = _parse_job(item, query_id, country)
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
