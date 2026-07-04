"""
Pipeline v3 — strategy-aware
=============================
Lê companies.yaml com strategy/tier/phase e roda o adapter certo pra cada um.

- manual_network → não scrapeia. Gera lembrete de signup pendente.
- usd_contractor → detecta ATS e scrapeia
- global_consulting → detecta ATS e scrapeia
- job_board_aggregator → usa adapter dedicado (ats já fixado no yaml)
- eu_sponsor → detecta ATS e scrapeia (com bonus se visa-sponsorship hits)
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import yaml

import ats_detector
import db
from adapters import fetch_for
from filter import JobFilter
from notifier import notify_jobs

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "jobs.db"
COMPANIES_YAML = ROOT / "config" / "companies.yaml"
KEYWORDS_YAML = ROOT / "config" / "keywords.yaml"
MANUAL_REPORT_PATH = ROOT / "docs" / "manual_signups.md"


def load_companies() -> list[dict]:
    if not COMPANIES_YAML.exists():
        logger.error("missing %s", COMPANIES_YAML)
        return []
    with open(COMPANIES_YAML, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data.get("companies", [])


def step_import_and_detect(force: bool = False) -> None:
    db.init_schema(DB_PATH)
    entries = load_companies()
    logger.info("loaded %d companies from YAML", len(entries))

    with db.connect(DB_PATH) as conn:
        for entry in entries:
            name = entry["name"]
            url = entry["url"]
            ats_hint = entry.get("ats")
            handle_hint = entry.get("handle")
            strategy = entry.get("strategy", "usd_contractor")
            tier = entry.get("tier_priority", "C")
            phase = int(entry.get("phase", 1))
            notes = entry.get("notes")

            company_id = db.upsert_company(
                conn, name, url, ats_hint, handle_hint,
                strategy=strategy, tier_priority=tier, phase=phase, notes=notes,
            )

            # Manual networks: marcar como N/A, não tentar detect
            if strategy == "manual_network":
                conn.execute(
                    "UPDATE companies SET status='manual', ats=NULL WHERE id=?",
                    (company_id,),
                )
                continue

            # Se já tem ATS explícito, skip detection
            if ats_hint and handle_hint and not force:
                continue

            row = conn.execute("SELECT ats FROM companies WHERE id=?", (company_id,)).fetchone()
            if row and row["ats"] and not force:
                continue

            logger.info("detecting ATS for %s (%s)", name, url)
            result = ats_detector.detect(url)
            if result.is_supported:
                conn.execute(
                    "UPDATE companies SET ats=?, ats_handle=? WHERE id=?",
                    (result.ats, result.handle, company_id),
                )
                logger.info("  -> %s/%s", result.ats, result.handle)
            else:
                conn.execute(
                    "UPDATE companies SET status='unsupported' WHERE id=?",
                    (company_id,),
                )
                logger.info("  -> unsupported (no ATS detected)")
            time.sleep(2.0)


def step_scrape(phase_filter: int | None = None) -> None:
    """Loop sobre empresas suportadas (não manuais)."""
    db.init_schema(DB_PATH)
    jf = JobFilter(KEYWORDS_YAML)

    with db.connect(DB_PATH) as conn:
        run_id = db.start_run(conn)
        sql = """
            SELECT * FROM companies
            WHERE ats IS NOT NULL AND strategy != 'manual_network'
        """
        params: list = []
        if phase_filter:
            sql += " AND phase = ?"
            params.append(phase_filter)
        sql += " ORDER BY tier_priority ASC, last_scraped_at ASC NULLS FIRST"
        companies = list(conn.execute(sql, params).fetchall())
        logger.info("[run %s] scraping %d companies%s",
                    run_id, len(companies),
                    f" (phase {phase_filter})" if phase_filter else "")

        total_seen = total_new = total_matched = ok = 0

        for company in companies:
            try:
                jobs = fetch_for(company["ats"], company["ats_handle"])
                logger.info("[%s] %s/%s -> %d jobs",
                            company["tier_priority"], company["ats"],
                            company["ats_handle"], len(jobs))
                ok += 1
                total_seen += len(jobs)
                for job in jobs:
                    result = jf.evaluate(job)
                    if result.tier == "BLOCKED":
                        continue
                    is_new = db.insert_or_touch_job(conn, company["id"], result)
                    if is_new:
                        total_new += 1
                    if result.passed:
                        total_matched += 1
                db.mark_company_scraped(conn, company["id"], "ok")
            except Exception as exc:
                logger.exception("scrape failed for %s: %s", company["name"], exc)
                db.mark_company_scraped(conn, company["id"], f"error: {str(exc)[:80]}")
            conn.commit()

        db.end_run(conn, run_id, len(companies), ok, total_seen, total_new, total_matched)
        logger.info("[run %s] done. ok=%d/%d seen=%d new=%d matched=%d",
                    run_id, ok, len(companies), total_seen, total_new, total_matched)


def step_notify() -> None:
    with db.connect(DB_PATH) as conn:
        rows = db.get_unnotified_high_score_jobs(conn, min_score=10)
        if not rows:
            logger.info("nothing to notify")
            return
        logger.info("notifying %d jobs", len(rows))
        ids = notify_jobs(rows)
        if ids:
            db.mark_notified(conn, ids)
            logger.info("marked %d jobs as notified", len(ids))


def step_manual_report() -> None:
    """
    Gera docs/manual_signups.md com lista de Tier A networks que exigem signup.
    Marca quais já estão completos (manual tracking via notes).
    """
    db.init_schema(DB_PATH)
    with db.connect(DB_PATH) as conn:
        rows = list(conn.execute(
            """
            SELECT name, source_url, tier_priority, phase, notes, status, last_scraped_at
            FROM companies WHERE strategy = 'manual_network'
            ORDER BY tier_priority ASC, name ASC
            """
        ).fetchall())

    MANUAL_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 🤝 Manual Network Signups",
        "",
        "Talent networks que **não têm job board público** — exigem signup + screening.",
        "Pipeline não consegue scrapear; você precisa aplicar manualmente.",
        "",
        "**Meta Fase 1 (Página C):** completar screening em pelo menos 4 talent networks.",
        "",
        "| Network | Tier | Status | URL | Notes |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        status_icon = "✅" if r["status"] == "completed" else "⏳"
        lines.append(
            f"| {r['name']} | {r['tier_priority']} | {status_icon} {r['status']} | "
            f"[apply]({r['source_url']}) | {r['notes'] or ''} |"
        )
    lines.extend([
        "",
        "## Como atualizar status",
        "",
        "Quando você completar um screening, rode:",
        "```sql",
        "UPDATE companies SET status='completed' WHERE name='Toptal';",
        "```",
        "(ou edite via DB browser)",
    ])
    MANUAL_REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    logger.info("manual signups report -> %s", MANUAL_REPORT_PATH)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "step",
        choices=["import", "detect", "scrape", "notify", "manual", "all"],
        help="which pipeline step to run",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--phase", type=int, default=None,
                        help="filter by phase (1, 2, or 3)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    if args.step in ("import", "detect", "all"):
        step_import_and_detect(force=args.force)
    if args.step in ("scrape", "all"):
        step_scrape(phase_filter=args.phase)
    if args.step in ("notify", "all"):
        step_notify()
    if args.step in ("manual", "all"):
        step_manual_report()


if __name__ == "__main__":
    main()
