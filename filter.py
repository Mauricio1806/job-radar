"""
Filter & Scoring v3.2
=====================
Fixes críticos pós-feedback:
- Gate must_have_any agora bate no TÍTULO, não no haystack inteiro
- Bloqueia vagas onsite em país EU/US/etc SEM menção de visa sponsorship
- max_age_days padrão 30 (não 45)
- blocked_titles mais agressivo
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from adapters import JobPosting

logger = logging.getLogger(__name__)


@dataclass
class FilterResult:
    job: JobPosting
    score: int
    tier: str
    matched: list[str]
    industry: str | None
    language: str
    visa_sponsorship: bool
    rejected_reason: str | None
    passed: bool


# Onsite-only nessas regiões = inacessível.
# Cidades EU foram REMOVIDAS — Mauricio mira relocação EU, vagas EU
# em Berlin/Madrid/Dublin/Amsterdam/etc passam mesmo sem visa explícito
# (essas empresas geralmente sponsoram, só não escrevem em cada vaga).
ONSITE_INACCESSIBLE_PATTERNS = re.compile(
    r"\b(toronto|vancouver|montreal|ottawa|calgary|"
    r"seattle|new york|nyc|san francisco|austin|boston|chicago|"
    r"los angeles|denver|atlanta|miami|portland|"
    r"bengaluru|bangalore|mumbai|delhi|new delhi|hyderabad|chennai|pune|"
    r"singapore|tokyo|sydney|melbourne|brisbane|"
    r"shanghai|beijing|shenzhen|hong kong|seoul)\b",
    re.IGNORECASE,
)


class JobFilter:
    def __init__(self, config_path: Path):
        with open(config_path, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)

        self.threshold = int(cfg.get("threshold", 16))
        self.max_age_days = int(cfg.get("max_age_days", 30))
        self.weights = cfg.get("weights", {})

        self.must_have_any = self._lc(cfg.get("must_have_any", []))
        self.tier_1_stack = self._lc(cfg.get("tier_1_stack", []))
        self.tier_2_stack = self._lc(cfg.get("tier_2_stack", []))
        self.domain_bonus = self._lc(cfg.get("domain_bonus", []))
        self.industry_boost = self._lc(cfg.get("industry_boost", []))
        self.region_t1 = self._lc(cfg.get("region_tier_1", []))
        self.region_t2 = self._lc(cfg.get("region_tier_2", []))
        self.region_t3 = self._lc(cfg.get("region_tier_3", []))
        self.currency_usd = self._lc(cfg.get("currency_usd", []))
        self.currency_eur = self._lc(cfg.get("currency_eur", []))
        self.seniority_signals = self._lc(cfg.get("seniority_signals", []))
        self.language_es_signals = self._lc(cfg.get("language_es_signals", []))
        self.visa_sponsorship_signals = self._lc(cfg.get("visa_sponsorship_signals", []))
        self.blocked = self._lc(cfg.get("blocked", []))
        self.blocked_titles = self._lc(cfg.get("blocked_titles", []))

    @staticmethod
    def _lc(items: list[str]) -> list[str]:
        return [s.lower().strip() for s in items if s and isinstance(s, str)]

    def _haystack(self, job: JobPosting) -> str:
        return " ".join(filter(None, [
            job.title or "",
            job.location or "",
            (job.description or "")[:3000],
            job.department or "",
        ])).lower()

    def _title_lower(self, job: JobPosting) -> str:
        return (job.title or "").lower().strip()

    def _detect_language(self, text: str) -> str:
        if any(token in text for token in self.language_es_signals):
            return "es"
        if any(token in text for token in [" e ", " é ", " ou ", " com ", "engenheiro", "vagas", "empresa de"]):
            return "pt"
        return "en"

    def _is_too_old(self, job: JobPosting) -> bool:
        if not job.posted_at:
            return False
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.max_age_days)
        try:
            posted = job.posted_at
            if posted.tzinfo is None:
                posted = posted.replace(tzinfo=timezone.utc)
            return posted < cutoff
        except (AttributeError, TypeError):
            return False

    def _has_visa_sponsorship(self, text: str) -> bool:
        return any(token in text for token in self.visa_sponsorship_signals)

    def _is_onsite_inaccessible(self, job: JobPosting, text: str) -> bool:
        """
        Vaga é onsite em país que o Mauricio não pode acessar SEM visa.
        Critério:
        - remote_flag = False (não é remoto)
        - location contém cidade conhecida
        - texto NÃO menciona visa sponsorship / relocation
        """
        if job.remote_flag:
            return False
        if self._has_visa_sponsorship(text):
            return False
        location = (job.location or "").lower()
        if not location:
            return False
        if ONSITE_INACCESSIBLE_PATTERNS.search(location):
            return True
        return False

    def evaluate(self, job: JobPosting) -> FilterResult:
        text = self._haystack(job)
        title_lower = self._title_lower(job)

        # 0a) Title blocklist
        for token in self.blocked_titles:
            if token in title_lower:
                return FilterResult(
                    job=job, score=0, tier="BLOCKED", matched=[],
                    industry=None, language="en", visa_sponsorship=False,
                    rejected_reason=f"blocked_title: {token}", passed=False,
                )

        # 0b) Age filter
        if self._is_too_old(job):
            return FilterResult(
                job=job, score=0, tier="BLOCKED", matched=[],
                industry=None, language="en", visa_sponsorship=False,
                rejected_reason=f"too old (> {self.max_age_days}d)", passed=False,
            )

        # 0c) Onsite inacessível sem visa
        if self._is_onsite_inaccessible(job, text):
            return FilterResult(
                job=job, score=0, tier="BLOCKED", matched=[],
                industry=None, language="en", visa_sponsorship=False,
                rejected_reason=f"onsite inacessível sem visa: {job.location}",
                passed=False,
            )

        # 1) Blocked tokens (curto-circuito)
        for token in self.blocked:
            if token in text:
                return FilterResult(
                    job=job, score=0, tier="BLOCKED", matched=[],
                    industry=None, language="en", visa_sponsorship=False,
                    rejected_reason=f"blocked: {token}", passed=False,
                )

        # 2) Gate — must_have_any precisa estar no TÍTULO (não na descrição inteira)
        gate_hit = next((t for t in self.must_have_any if t in title_lower), None)
        if gate_hit is None:
            return FilterResult(
                job=job, score=0, tier="BLOCKED", matched=[],
                industry=None, language="en", visa_sponsorship=False,
                rejected_reason="no must_have_any in title", passed=False,
            )

        score = 0
        matched: list[str] = [gate_hit]
        score += self.weights.get("must_have", 5)
        language = self._detect_language(text)

        # 3) Tier 1 stack
        for token in self.tier_1_stack:
            if self._matches(token, text):
                score += self.weights.get("tier_1_stack", 3)
                matched.append(f"t1:{token}")

        # 4) Tier 2 stack
        for token in self.tier_2_stack:
            if self._matches(token, text):
                score += self.weights.get("tier_2_stack", 1)
                matched.append(f"t2:{token}")

        # 5) Domain bonus
        for token in self.domain_bonus:
            if self._matches(token, text):
                score += self.weights.get("domain_bonus", 4)
                matched.append(f"dom:{token}")

        # 6) Industry boost
        industry_hit = None
        for token in self.industry_boost:
            if self._matches(token, text):
                score += self.weights.get("industry_boost", 2)
                industry_hit = token
                matched.append(f"ind:{token}")
                break

        # 7) Região
        tier_label = None
        if self._any(self.region_t1, text):
            score += self.weights.get("region_t1", 5)
            matched.append("region_t1")
            tier_label = "T1"
        elif self._any(self.region_t2, text):
            score += self.weights.get("region_t2", 3)
            matched.append("region_t2")
            tier_label = "T2"
        elif job.remote_flag or self._any(self.region_t3, text):
            score += self.weights.get("region_t3", 1)
            matched.append("region_t3")
            tier_label = "T3"
        else:
            return FilterResult(
                job=job, score=score, tier="BLOCKED", matched=matched,
                industry=industry_hit, language=language, visa_sponsorship=False,
                rejected_reason="no region match", passed=False,
            )

        # 8) Currency
        if any(t in text for t in self.currency_usd):
            score += self.weights.get("currency_usd", 3)
            matched.append("usd")
        elif any(t in text for t in self.currency_eur):
            score += self.weights.get("currency_eur", 2)
            matched.append("eur")

        # 9) Seniority
        for token in self.seniority_signals:
            if self._matches(token, text):
                score += self.weights.get("seniority_match", 3)
                matched.append(f"sr:{token.strip()}")
                break

        # 10) Language bonus
        if language == "es":
            score += self.weights.get("language_es", 1)
            matched.append("lang:es")

        # 11) Visa sponsorship
        visa_hit = False
        for token in self.visa_sponsorship_signals:
            if token in text:
                score += self.weights.get("visa_sponsorship", 5)
                matched.append(f"visa:{token}")
                visa_hit = True
                if tier_label == "T3":
                    tier_label = "T2"
                break

        passed = score >= self.threshold
        return FilterResult(
            job=job, score=score, tier=tier_label or "T3", matched=matched,
            industry=industry_hit, language=language, visa_sponsorship=visa_hit,
            rejected_reason=None if passed else f"score {score} < {self.threshold}",
            passed=passed,
        )

    @staticmethod
    def _matches(token: str, text: str) -> bool:
        if len(token) <= 3 or any(c in token for c in (" ", ".", "/", "-")):
            return token in text
        return re.search(rf"\b{re.escape(token)}\b", text) is not None

    @staticmethod
    def _any(tokens: list[str], text: str) -> bool:
        return any(t in text for t in tokens)


def matched_to_json(matched: list[str]) -> str:
    return json.dumps(matched, ensure_ascii=False)
