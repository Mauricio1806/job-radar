"""
Consultancy HTML Scrapers
==========================
Scrapers HTML específicos pras consultorias globais que NÃO usam ATS padrão.

Cada uma tem padrão HTML próprio. Implementados via BeautifulSoup
(sem Playwright — HTML é server-rendered na maioria desses sites).

Total de vagas potenciais nesses adapters:
- DataArt:    ~158
- SoftServe:  ~232 (5 páginas)
- Intellias:  ~149
- Ciklum:     ~50 (estimado)
- Avenga:     ~100 (Teamtailor, mas handle errado — fix manual aqui)
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from adapters import JobPosting, _strip_html

logger = logging.getLogger(__name__)

POLITE_DELAY = 2.0
TIMEOUT = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


def _fetch_html(url: str) -> BeautifulSoup | None:
    try:
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
        return BeautifulSoup(response.text, "html.parser")
    except (requests.RequestException, ValueError) as exc:
        logger.error("fetch failed for %s: %s", url, exc)
        return None


# ──────────────────────────────────────────────────────────────────────
# DATAART — www.dataart.team
# ──────────────────────────────────────────────────────────────────────
def fetch_dataart(handle: str = "all") -> list[JobPosting]:
    """
    GET https://www.dataart.team/vacancies
    HTML estático, todos os 158 jobs em página única.
    Estrutura: <a href="/vacancies/CODE"><h3>Title</h3><locations><desc></a>
    """
    base = "https://www.dataart.team"
    url = f"{base}/vacancies"
    soup = _fetch_html(url)
    if not soup:
        return []

    out: list[JobPosting] = []
    # Cada vaga é um link com h3 dentro
    for link in soup.find_all("a", href=re.compile(r"^/vacancies/[A-Z]+\d+")):
        href = link.get("href", "")
        h3 = link.find("h3")
        if not h3:
            continue
        title = h3.get_text(strip=True)
        # Próximo elemento depois do h3 geralmente tem locations
        link_text = link.get_text(" ", strip=True)
        # Remove título do texto para sobrar locations + desc
        rest = link_text.replace(title, "", 1).strip()
        # As primeiras palavras geralmente são locations concatenadas
        # Heurística: separar location de desc por primeira frase
        location, description = _split_location_desc(rest)

        # Heurística remote: locations com "Remote." ou tem várias regiões
        remote = ("remote" in location.lower() or
                  location.count(",") >= 2 or
                  "latam" in location.lower())

        out.append(JobPosting(
            ats="dataart",
            company_handle=handle,
            external_id=href.rsplit("/", 1)[-1],
            title=title,
            location=location,
            remote_flag=remote,
            description=description,
            url=urljoin(base, href),
            posted_at=None,
            raw={"_company_label": "DataArt"},
        ))
    time.sleep(POLITE_DELAY)
    logger.info("DataArt: %d jobs parsed", len(out))
    return out


# ──────────────────────────────────────────────────────────────────────
# SOFTSERVE — career.softserveinc.com
# ──────────────────────────────────────────────────────────────────────
def fetch_softserve(handle: str = "data") -> list[JobPosting]:
    """
    GET https://career.softserveinc.com/en-us/vacancies
    HTML estático paginado. Vamos pegar primeiras 5 páginas (~60 jobs).
    Estrutura: <a href="/en-us/vacancies/{slug}-{id}">[CategoryTitleLevelLocation]</a>
    """
    base = "https://career.softserveinc.com"
    out: list[JobPosting] = []
    max_pages = 5

    for page in range(1, max_pages + 1):
        url = f"{base}/en-us/vacancies"
        if page > 1:
            url = f"{url}?page={page}"
        soup = _fetch_html(url)
        if not soup:
            break

        links = soup.find_all("a", href=re.compile(r"/en-us/vacancies/.+-\d+$"))
        if not links:
            break

        page_count = 0
        for link in links:
            href = link.get("href", "")
            text = link.get_text(" ", strip=True)
            if not text:
                continue

            # Padrão "{Category}{Title}{Level}{Locations}"
            # Sem separadores claros — usar regex pros tokens de level
            level_match = re.search(
                r"\b(Junior|Middle|Senior|Lead|Director|Executive|Manager|Principal)\b",
                text,
            )
            if level_match:
                title_part = text[:level_match.start()].strip()
                level = level_match.group(1)
                location = text[level_match.end():].strip()
            else:
                title_part = text
                level = ""
                location = ""

            # Title_part é category+title concatenado. Heurística: tirar primeiro "Engineering & Technology" etc.
            for cat in ["Engineering & Technology", "Business Development & Marketing",
                        "Software Development", "Software Testing", "Business Analysis",
                        "Product Management", "Project Management", "Data Science",
                        "Data & Analytics", "Cybersecurity", "Robotics & Advanced Automation",
                        "Test Automation", "Advanced Technologies", "Software Engineering in Test"]:
                if title_part.startswith(cat):
                    title_part = title_part[len(cat):].strip()
                    break

            title = f"{title_part} ({level})" if level else title_part

            external_id = re.search(r"-(\d+)$", href)
            external_id = external_id.group(1) if external_id else href

            remote = ("remote" in text.lower() or
                      "ukraine" in location.lower() or
                      "remote.latam" in location.lower())

            out.append(JobPosting(
                ats="softserve",
                company_handle=handle,
                external_id=external_id,
                title=title,
                location=location,
                remote_flag=remote,
                description="",  # detalhes na página individual — fetch on demand
                url=urljoin(base, href),
                posted_at=None,
                raw={"_company_label": "SoftServe"},
            ))
            page_count += 1

        logger.info("SoftServe page %d: %d jobs", page, page_count)
        if page_count == 0:
            break
        time.sleep(POLITE_DELAY)

    return out


# ──────────────────────────────────────────────────────────────────────
# INTELLIAS — career.intellias.com
# ──────────────────────────────────────────────────────────────────────
def fetch_intellias(handle: str = "all") -> list[JobPosting]:
    """
    GET https://career.intellias.com/vacancies/
    Primeira página tem ~12 vagas no HTML. Resto via "See more" (AJAX).
    Pra ser rápido: usar filtro de Data Engineering family.
    """
    base = "https://career.intellias.com"
    url = f"{base}/vacancies/?vacancy_filter%5Bjob_family%5D%5B%5D=Data+Engineering"
    soup = _fetch_html(url)
    if not soup:
        # Fallback pra URL geral
        soup = _fetch_html(f"{base}/vacancies/")
        if not soup:
            return []

    out: list[JobPosting] = []
    # Links de vaga seguem padrão /vacancy/{slug-id}/
    for link in soup.find_all("a", href=re.compile(r"/vacancy/.+-\d+/?$")):
        href = link.get("href", "")
        text = link.get_text(" ", strip=True)
        if not text:
            continue

        # Padrão: "{Category}{Title}{Level}{Locations}{Hybrid|Remote}"
        # Achar level no texto
        level_match = re.search(
            r"\b(Junior|Middle|Senior|Lead|Director|Executive|Manager|Principal|Solution Architect)\b",
            text,
        )
        if level_match:
            title_part = text[:level_match.start()].strip()
            after = text[level_match.end():].strip()
        else:
            title_part = text
            after = ""

        # Categoria conhecida no começo
        for cat in ["Software Engineering", "Test Engineering", "Business Analysis",
                    "Architecture", "Management", "Data Engineering", "DevOps Engineering",
                    "AI/ML Engineering", "Information Security", "ITSM",
                    "Quality Management", "OTHER"]:
            if title_part.startswith(cat):
                title_part = title_part[len(cat):].strip()
                break

        # Tipo de trabalho no fim
        remote = False
        for kw in (" Remote", " Hybrid", " Office"):
            if after.endswith(kw):
                remote = (kw.strip() == "Remote")
                after = after[: -len(kw)].strip()
                break

        location = after
        external_id = re.search(r"-(\d+)/?$", href)
        external_id = external_id.group(1) if external_id else href

        out.append(JobPosting(
            ats="intellias",
            company_handle=handle,
            external_id=external_id,
            title=title_part,
            location=location,
            remote_flag=remote,
            description="",
            url=urljoin(base, href) if not href.startswith("http") else href,
            posted_at=None,
            raw={"_company_label": "Intellias"},
        ))
    time.sleep(POLITE_DELAY)
    logger.info("Intellias: %d jobs parsed", len(out))
    return out


# ──────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────
def _split_location_desc(text: str) -> tuple[str, str]:
    """
    DataArt concatena locations e desc num texto. Locations são lista
    de palavras concatenadas tipo 'ArmeniaBulgariaCyprus...'
    Heurística: locations terminam quando aparece "We are" ou "Looking for"
    ou maiúsculas seguidas de minúsculas começando uma frase.
    """
    # Procurar início de frase em inglês (We are / We're / Looking)
    m = re.search(r"\b(We are|We're|We have|We |Looking for|We seek)\b", text)
    if m:
        location = text[:m.start()].strip()
        description = text[m.start():].strip()
    else:
        # Fallback: primeira metade
        mid = len(text) // 3
        location = text[:mid].strip()
        description = text[mid:].strip()
    return location, description


# Registry
CONSULTANCY_ADAPTERS = {
    "dataart": fetch_dataart,
    "softserve": fetch_softserve,
    "intellias": fetch_intellias,
}
