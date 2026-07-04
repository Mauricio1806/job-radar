"""
CV Tailoring com Claude API
============================
Para cada vaga T1 high-score, gera um CV ajustado pro keyword set específico.

Estratégia:
- Lê config/cv_base.md (CV master do Mauricio em markdown)
- Pra cada vaga selecionada, manda título + descrição + CV base pro Claude
- Recebe CV ajustado em markdown
- Salva em output/cv/{job_id}.md
- (opcional) converte pra .docx com pandoc

Custo: ~$0.01 por vaga usando Claude Haiku, ~$0.05 com Sonnet.
Recomendo Sonnet pra Tier 1 only.

Uso:
    export ANTHROPIC_API_KEY=sk-ant-...
    python cv_tailoring.py --top 10            # top 10 vagas T1 não-tailored
    python cv_tailoring.py --job-id <id>       # vaga específica
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "jobs.db"
CV_BASE_PATH = ROOT / "config" / "cv_base.md"
OUTPUT_DIR = ROOT / "output" / "cv"

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-4-5"   # ajusta pra haiku se quiser barato
MAX_TOKENS = 2500


SYSTEM_PROMPT = """You are an expert technical resume writer specialized in data engineering roles.

Your job: take a master CV and a specific job posting, and produce a tailored 1-page CV in markdown format that:

1. **Keeps every fact 100% truthful** — never invent experience, certifications, or skills the candidate doesn't have. Only rephrase and reorder existing content.
2. **Mirrors the job's keyword set** — when the candidate genuinely has a skill that the job emphasizes, surface it in the summary and in 1-2 bullet points.
3. **Reframes bullets to match the job's language** — if the job says "ELT" and the CV says "ETL", and the candidate did both, prefer "ETL/ELT". If the job emphasizes "data observability" and the CV mentions monitoring, rephrase as "data observability/monitoring".
4. **Cuts irrelevant content** — if a job is pure cloud DE, you can de-emphasize Power BI bullets and keep only the most relevant ones.
5. **Optimizes for ATS parsing** — clear section headers (Summary, Experience, Skills, Education), no fancy formatting, plain markdown.

Output format: pure markdown, ready to paste into a CV. No preamble, no explanation, just the CV."""

USER_PROMPT_TEMPLATE = """## Master CV (truth source — do not invent beyond this)

{cv_base}

---

## Target Job

**Company:** {company}
**Title:** {title}
**Location:** {location}
**ATS:** {ats}
**Matched keywords from filter:** {keywords}

### Job description

{description}

---

## Task

Generate a tailored 1-page markdown CV optimized for this specific role.
Output only the CV in markdown. No commentary."""


def load_cv_base() -> str:
    if not CV_BASE_PATH.exists():
        logger.error("CV base not found: %s", CV_BASE_PATH)
        logger.error("Create it from config/cv_base.md (see template in repo)")
        sys.exit(1)
    return CV_BASE_PATH.read_text(encoding="utf-8")


def fetch_jobs(top_n: int | None = None, job_id: str | None = None) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    if job_id:
        rows = conn.execute(
            """
            SELECT j.*, c.name AS company FROM jobs j JOIN companies c ON c.id = j.company_id
            WHERE j.id = ?
            """,
            (job_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT j.*, c.name AS company
            FROM jobs j JOIN companies c ON c.id = j.company_id
            WHERE j.tier = 'T1' AND j.score >= 15 AND j.status = 'new'
              AND j.id NOT IN (
                SELECT replace(name, '.md', '') FROM (
                  SELECT name FROM sqlite_master WHERE 1=0
                )
              )
            ORDER BY j.score DESC, j.scraped_at DESC
            LIMIT ?
            """,
            (top_n or 10,),
        ).fetchall()

    conn.close()
    return [dict(r) for r in rows]


def call_claude(cv_base: str, job: dict, model: str = DEFAULT_MODEL) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    keywords = ", ".join(json.loads(job.get("matched_keywords") or "[]"))
    user_msg = USER_PROMPT_TEMPLATE.format(
        cv_base=cv_base,
        company=job["company"],
        title=job["title"],
        location=job["location"] or "",
        ats=job.get("ats", "—"),
        keywords=keywords,
        description=(job.get("description") or "")[:4000],
    )

    payload = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_msg}],
    }

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    response = requests.post(API_URL, json=payload, headers=headers, timeout=120)
    response.raise_for_status()
    data = response.json()
    blocks = data.get("content", [])
    text = "\n\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    return text.strip()


def tailor_one(job: dict, cv_base: str, model: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / f"{job['id']}.md"
    if output_path.exists():
        logger.info("skip (already tailored): %s", output_path.name)
        return output_path

    logger.info("tailoring CV for %s | %s (score=%s)",
                job["company"], job["title"], job["score"])
    tailored = call_claude(cv_base, job, model=model)

    header = (
        f"<!-- CV tailored for {job['company']} | {job['title']} -->\n"
        f"<!-- Job ID: {job['id']} | Score: {job['score']} | Tier: {job.get('tier', '?')} -->\n"
        f"<!-- URL: {job.get('url', '')} -->\n\n"
    )
    output_path.write_text(header + tailored, encoding="utf-8")
    logger.info("  -> %s", output_path)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=10,
                        help="top N T1 jobs to tailor (default 10)")
    parser.add_argument("--job-id", default=None,
                        help="tailor a specific job by ID")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Claude model (default {DEFAULT_MODEL})")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s")

    cv_base = load_cv_base()
    jobs = fetch_jobs(top_n=args.top, job_id=args.job_id)
    if not jobs:
        print("Nenhuma vaga T1 high-score nova encontrada.")
        return

    print(f"Tailoring {len(jobs)} CVs com modelo {args.model}...")
    for job in jobs:
        try:
            tailor_one(job, cv_base, model=args.model)
            time.sleep(2.0)   # rate limit safe
        except Exception as exc:
            logger.exception("tailoring failed for job %s: %s", job.get("id"), exc)

    print(f"\n✅ CVs tailored em {OUTPUT_DIR}/")
    print("Próximo passo: revisar, converter pra PDF com pandoc, aplicar.")


if __name__ == "__main__":
    main()
