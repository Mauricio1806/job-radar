"""
SQLite layer v6 — delta detection
==================================
Key change: `first_seen_at` = quando a vaga entrou no radar (não posted_at).
Notificação é baseada nisso, não em posted_at do ATS.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from adapters import JobPosting
from filter import FilterResult

logger = logging.getLogger(__name__)


_COUNTRY_SUFFIX_PATTERNS = [
    re.compile(r"\s*\|\s*(brazil|brasil|argentina|mexico|méxico|chile|colombia|peru|"
               r"dr|dominican\s*republic|uruguay|paraguay|venezuela|ecuador|"
               r"latam|latin\s*america|americas|south\s*america|"
               r"spain|españa|portugal|germany|alemania|france|netherlands|holanda|"
               r"ireland|irlanda|uk|united\s*kingdom|eu|europe|europa|"
               r"remote|worldwide|global)\s*$", re.IGNORECASE),
    re.compile(r"\s*\([^)]+\)\s*$"),
    re.compile(r"\s*[-–]\s*(brazil|brasil|latam|remote|argentina|mexico|colombia|"
               r"spain|germany|eu|europe)\s*$", re.IGNORECASE),
]


def normalize_title(title: str) -> str:
    if not title:
        return ""
    t = title.strip().lower()
    for _ in range(3):
        before = t
        for pattern in _COUNTRY_SUFFIX_PATTERNS:
            t = pattern.sub("", t).strip()
        if t == before:
            break
    return t


SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  ats TEXT,
  ats_handle TEXT,
  source_url TEXT,
  strategy TEXT DEFAULT 'usd_contractor',
  tier_priority TEXT DEFAULT 'C',
  phase INTEGER DEFAULT 1,
  notes TEXT,
  last_scraped_at TEXT,
  status TEXT DEFAULT 'pending'
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_companies_url ON companies(source_url);
CREATE INDEX IF NOT EXISTS ix_companies_strategy ON companies(strategy);
CREATE INDEX IF NOT EXISTS ix_companies_phase ON companies(phase);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  company_id INTEGER NOT NULL REFERENCES companies(id),
  ats TEXT,
  external_id TEXT,
  title TEXT NOT NULL,
  location TEXT,
  remote_flag INTEGER DEFAULT 0,
  description TEXT,
  url TEXT,
  posted_at TEXT,            -- data do ATS (não confiável — só pra referência)
  first_seen_at TEXT NOT NULL,   -- quando a vaga entrou no NOSSO radar
  last_seen_at TEXT NOT NULL,    -- última vez que apareceu num scrape (pra detectar removidas)
  score INTEGER DEFAULT 0,
  tier TEXT DEFAULT 'T3',
  industry TEXT,
  language TEXT DEFAULT 'en',
  visa_sponsorship INTEGER DEFAULT 0,
  matched_keywords TEXT,
  recruiter_name TEXT,
  recruiter_email TEXT,
  recruiter_linkedin TEXT,
  status TEXT DEFAULT 'new',
  applied_at TEXT,
  notes TEXT,
  notified_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_tier ON jobs(tier);
CREATE INDEX IF NOT EXISTS ix_jobs_score ON jobs(score DESC);
CREATE INDEX IF NOT EXISTS ix_jobs_first_seen ON jobs(first_seen_at DESC);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS ix_jobs_company ON jobs(company_id);

CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  companies_total INTEGER DEFAULT 0,
  companies_ok INTEGER DEFAULT 0,
  jobs_seen INTEGER DEFAULT 0,
  jobs_new INTEGER DEFAULT 0,
  jobs_matched INTEGER DEFAULT 0,
  notes TEXT
);
"""


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
    logger.info("schema initialized at %s", db_path)


def upsert_company(conn: sqlite3.Connection, name: str, source_url: str,
                   ats: str | None = None, ats_handle: str | None = None,
                   strategy: str = "usd_contractor", tier_priority: str = "C",
                   phase: int = 1, notes: str | None = None) -> int:
    row = conn.execute(
        "SELECT id FROM companies WHERE source_url = ?", (source_url,)
    ).fetchone()
    if row:
        conn.execute(
            """UPDATE companies SET ats=COALESCE(?, ats), ats_handle=COALESCE(?, ats_handle),
               strategy=?, tier_priority=?, phase=?, notes=COALESCE(?, notes)
               WHERE id=?""",
            (ats, ats_handle, strategy, tier_priority, phase, notes, row["id"]),
        )
        return int(row["id"])
    cursor = conn.execute(
        """INSERT INTO companies (name, source_url, ats, ats_handle, strategy, tier_priority, phase, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, source_url, ats, ats_handle, strategy, tier_priority, phase, notes),
    )
    return int(cursor.lastrowid)


def mark_company_scraped(conn: sqlite3.Connection, company_id: int, status: str = "ok") -> None:
    conn.execute(
        "UPDATE companies SET last_scraped_at=?, status=? WHERE id=?",
        (datetime.now(timezone.utc).isoformat(), status, company_id),
    )


def _stable_id(job: JobPosting, company_id: int) -> str:
    import hashlib
    normalized = normalize_title(job.title)
    key = f"{company_id}|{normalized}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def insert_or_touch_job(conn: sqlite3.Connection, company_id: int,
                         result: FilterResult) -> bool:
    """
    Insert vaga nova OU update last_seen_at.
    Retorna True se é NOVA (never seen before) — essa é a chave do delta.
    """
    job = result.job
    stable_id = _stable_id(job, company_id)
    now = datetime.now(timezone.utc).isoformat()

    exists = conn.execute("SELECT 1 FROM jobs WHERE id=?", (stable_id,)).fetchone()
    if exists:
        # Já conhecida — só update last_seen (indica que ainda tá ativa)
        conn.execute(
            """UPDATE jobs SET last_seen_at=?, score=?, tier=?, industry=?,
               language=?, visa_sponsorship=?, matched_keywords=? WHERE id=?""",
            (now, result.score, result.tier, result.industry, result.language,
             int(result.visa_sponsorship),
             json.dumps(result.matched, ensure_ascii=False), stable_id),
        )
        return False

    # NOVA — first_seen_at = agora
    conn.execute(
        """
        INSERT INTO jobs (
            id, company_id, ats, external_id, title, location, remote_flag,
            description, url, posted_at, first_seen_at, last_seen_at, score,
            tier, industry, language, visa_sponsorship, matched_keywords,
            recruiter_name, recruiter_email, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            stable_id, company_id, job.ats, job.external_id, job.title, job.location,
            int(job.remote_flag), job.description[:5000] if job.description else "",
            job.url, job.posted_at.isoformat() if job.posted_at else None,
            now, now,
            result.score, result.tier, result.industry, result.language,
            int(result.visa_sponsorship),
            json.dumps(result.matched, ensure_ascii=False),
            job.recruiter_name, job.recruiter_email, "new",
        ),
    )
    return True


def get_companies_for_scraping(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT * FROM companies WHERE ats IS NOT NULL ORDER BY last_scraped_at ASC NULLS FIRST"
    ).fetchall())


def get_unnotified_high_score_jobs(conn: sqlite3.Connection,
                                    min_score: int = 12) -> list[sqlite3.Row]:
    """
    Retorna vagas nunca notificadas com score alto.
    Ordenadas por first_seen_at DESC (mais recentes no radar primeiro).
    """
    return list(conn.execute(
        """
        SELECT j.*, c.name AS company_name
        FROM jobs j JOIN companies c ON c.id = j.company_id
        WHERE j.notified_at IS NULL AND j.score >= ?
        ORDER BY j.first_seen_at DESC, j.score DESC
        """,
        (min_score,),
    ).fetchall())


def mark_notified(conn: sqlite3.Connection, job_ids: list[str]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany("UPDATE jobs SET notified_at=? WHERE id=?",
                     [(now, jid) for jid in job_ids])


def start_run(conn: sqlite3.Connection) -> str:
    import uuid
    run_id = uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO runs (run_id, started_at) VALUES (?, ?)",
        (run_id, datetime.now(timezone.utc).isoformat()),
    )
    return run_id


def end_run(conn: sqlite3.Connection, run_id: str,
            companies_total: int, companies_ok: int,
            jobs_seen: int, jobs_new: int, jobs_matched: int,
            notes: str = "") -> None:
    conn.execute(
        """
        UPDATE runs SET ended_at=?, companies_total=?, companies_ok=?,
            jobs_seen=?, jobs_new=?, jobs_matched=?, notes=?
        WHERE run_id=?
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            companies_total, companies_ok, jobs_seen, jobs_new, jobs_matched, notes,
            run_id,
        ),
    )
