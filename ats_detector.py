"""
ATS Detector
============
Recebe uma URL de página de carreiras (ou homepage da empresa) e
retorna qual ATS ela usa + o handle/slug específico.

Estratégia em 3 camadas:
1. Regex em URL conhecida (rápido, 60% dos casos)
2. HEAD/GET no /careers e olhar redirects
3. GET na home e regex em HTML/script src

Resultado: ATSDetection(ats, handle, evidence_url, confidence)
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# Ordem importa: detectores mais específicos primeiro
URL_PATTERNS = [
    # ats_name, regex contra URL, group_idx do handle
    ("greenhouse", re.compile(r"boards\.greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9_-]+)", re.I), 1),
    ("greenhouse", re.compile(r"job-boards\.greenhouse\.io/([a-z0-9_-]+)", re.I), 1),
    ("lever", re.compile(r"jobs\.lever\.co/([a-z0-9_-]+)", re.I), 1),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-z0-9_-]+)", re.I), 1),
    ("smartrecruiters", re.compile(r"careers\.smartrecruiters\.com/([a-z0-9_-]+)", re.I), 1),
    ("smartrecruiters", re.compile(r"jobs\.smartrecruiters\.com/([a-z0-9_-]+)", re.I), 1),
    ("workable", re.compile(r"apply\.workable\.com/([a-z0-9_-]+)", re.I), 1),
    ("workable", re.compile(r"([a-z0-9_-]+)\.workable\.com", re.I), 1),
    ("recruitee", re.compile(r"([a-z0-9_-]+)\.recruitee\.com", re.I), 1),
    ("workday", re.compile(r"([a-z0-9_-]+\.(?:wd[0-9]+|myworkdayjobs)\.com)/(?:[a-z]+/)?([a-z0-9_-]+)", re.I), 0),
    ("bullhorn", re.compile(r"careers-([a-z0-9]+)\.bullhornstaffing\.com", re.I), 1),
    ("teamtailor", re.compile(r"([a-z0-9_-]+)\.teamtailor\.com", re.I), 1),
    ("personio", re.compile(r"([a-z0-9_-]+)\.jobs\.personio\.(?:com|de)", re.I), 1),
    ("jobvite", re.compile(r"jobs\.jobvite\.com/([a-z0-9_-]+)", re.I), 1),
    ("breezy", re.compile(r"([a-z0-9_-]+)\.breezy\.hr", re.I), 1),
    ("icims", re.compile(r"careers-([a-z0-9_-]+)\.icims\.com", re.I), 1),
    ("taleo", re.compile(r"([a-z0-9_-]+)\.taleo\.net", re.I), 1),
]

# Padrões em HTML/JS quando o site é custom mas embeda widget
HTML_FINGERPRINTS = [
    ("greenhouse", re.compile(r'(?:src|href)=["\']https?://boards\.greenhouse\.io/([a-z0-9_-]+)', re.I)),
    ("greenhouse", re.compile(r'Grnhse\.Iframe\.load\(["\']([a-z0-9_-]+)["\']', re.I)),
    ("lever", re.compile(r'(?:src|href)=["\']https?://jobs\.lever\.co/([a-z0-9_-]+)', re.I)),
    ("ashby", re.compile(r'jobs\.ashbyhq\.com/([a-z0-9_-]+)', re.I)),
    ("smartrecruiters", re.compile(r'careers\.smartrecruiters\.com/([a-z0-9_-]+)', re.I)),
    ("workable", re.compile(r'apply\.workable\.com/([a-z0-9_-]+)', re.I)),
    ("recruitee", re.compile(r'([a-z0-9_-]+)\.recruitee\.com', re.I)),
    ("teamtailor", re.compile(r'([a-z0-9_-]+)\.teamtailor\.com', re.I)),
    ("personio", re.compile(r'([a-z0-9_-]+)\.jobs\.personio\.(?:com|de)', re.I)),
]

CAREERS_PATHS = [
    "/careers", "/career", "/jobs", "/job-openings", "/openings",
    "/vagas", "/trabalhe-conosco", "/work-with-us", "/join-us",
    "/empleo", "/trabaja-con-nosotros",
]


@dataclass
class ATSDetection:
    ats: Optional[str]
    handle: Optional[str]
    evidence_url: Optional[str]
    confidence: float       # 0.0 - 1.0
    raw_url: str

    @property
    def is_supported(self) -> bool:
        return self.ats is not None


def _match_url(url: str) -> Optional[ATSDetection]:
    for ats, pattern, group_idx in URL_PATTERNS:
        m = pattern.search(url)
        if m:
            handle = m.group(group_idx) if group_idx > 0 else m.group(1)
            return ATSDetection(
                ats=ats, handle=handle, evidence_url=url,
                confidence=0.95, raw_url=url,
            )
    return None


def _match_html(html: str, source_url: str) -> Optional[ATSDetection]:
    for ats, pattern in HTML_FINGERPRINTS:
        m = pattern.search(html)
        if m:
            return ATSDetection(
                ats=ats, handle=m.group(1), evidence_url=source_url,
                confidence=0.85, raw_url=source_url,
            )
    return None


def _fetch(url: str, timeout: int = 10) -> Optional[requests.Response]:
    try:
        return requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    except requests.RequestException as exc:
        logger.debug("fetch failed for %s: %s", url, exc)
        return None


def detect(url: str, polite_delay: float = 1.5) -> ATSDetection:
    """
    Detecta ATS de uma empresa a partir de uma URL.

    A URL pode ser:
    - já a página de careers (ex: https://boards.greenhouse.io/acme)
    - homepage da empresa (ex: https://acme.com)
    """
    # 1) Match direto na URL
    direct = _match_url(url)
    if direct:
        return direct

    # 2) Fetch da URL e tenta match em redirect chain + HTML
    response = _fetch(url)
    if response is not None:
        # Redirect pode ter levado pra URL do ATS
        final = _match_url(response.url)
        if final:
            return final
        # HTML fingerprint
        html_match = _match_html(response.text, response.url)
        if html_match:
            return html_match

    # 3) Tenta paths comuns de careers
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    for path in CAREERS_PATHS:
        time.sleep(polite_delay)
        candidate = base + path
        response = _fetch(candidate)
        if response is None:
            continue
        final = _match_url(response.url)
        if final:
            return final
        html_match = _match_html(response.text, response.url)
        if html_match:
            return html_match

    return ATSDetection(
        ats=None, handle=None, evidence_url=None,
        confidence=0.0, raw_url=url,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_urls = [
        "https://boards.greenhouse.io/airbnb",
        "https://jobs.lever.co/notion",
        "https://stripe.com/jobs",
        "https://www.roberthalf.com",
        "https://acme.recruitee.com",
    ]
    for u in test_urls:
        result = detect(u)
        print(f"{u}\n  -> ats={result.ats} handle={result.handle} conf={result.confidence:.2f}\n")
