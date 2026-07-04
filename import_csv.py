"""
Import CSV → companies.yaml
============================
Converte um CSV exportado do Notion (Export → CSV) na MASTER DATABASE
em config/companies.yaml.

Uso:
    python import_csv.py path/to/master_database.csv \
        --name-col "Name" --url-col "Site" \
        [--ats-col "ATS"] [--handle-col "Handle"]
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).parent
OUTPUT = ROOT / "config" / "companies.yaml"


def _is_valid_url(url: str) -> bool:
    try:
        parsed = urlparse(url.strip())
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def import_csv(csv_path: Path, name_col: str, url_col: str,
               ats_col: str | None = None, handle_col: str | None = None,
               extra_skipped: list[str] | None = None) -> None:
    extra_skipped = extra_skipped or []
    rows_out: list[dict] = []
    skipped_no_url = 0
    skipped_invalid = 0

    with open(csv_path, "r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if name_col not in reader.fieldnames or url_col not in reader.fieldnames:
            print(f"ERRO: colunas '{name_col}' e/ou '{url_col}' não existem.")
            print(f"Colunas disponíveis: {reader.fieldnames}")
            sys.exit(1)

        for row in reader:
            name = (row.get(name_col) or "").strip()
            url = (row.get(url_col) or "").strip()
            if not url:
                skipped_no_url += 1
                continue
            if not _is_valid_url(url):
                # Tenta adicionar https:// se faltar
                if not url.startswith(("http://", "https://")):
                    candidate = "https://" + url
                    if _is_valid_url(candidate):
                        url = candidate
                    else:
                        skipped_invalid += 1
                        continue
                else:
                    skipped_invalid += 1
                    continue
            if name in extra_skipped:
                continue

            entry: dict = {"name": name or urlparse(url).netloc, "url": url}
            if ats_col and (row.get(ats_col) or "").strip():
                entry["ats"] = row[ats_col].strip().lower()
            if handle_col and (row.get(handle_col) or "").strip():
                entry["handle"] = row[handle_col].strip()
            rows_out.append(entry)

    # Dedupe por URL
    seen_urls: set[str] = set()
    unique = []
    for entry in rows_out:
        if entry["url"] in seen_urls:
            continue
        seen_urls.add(entry["url"])
        unique.append(entry)

    output = {"companies": unique}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as fh:
        yaml.safe_dump(output, fh, allow_unicode=True, sort_keys=False)

    print(f"✅ {len(unique)} empresas escritas em {OUTPUT}")
    print(f"   ({skipped_no_url} skipped sem URL, {skipped_invalid} skipped URL inválida)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--name-col", default="Name")
    parser.add_argument("--url-col", default="URL")
    parser.add_argument("--ats-col", default=None)
    parser.add_argument("--handle-col", default=None)
    args = parser.parse_args()

    if not args.csv_path.exists():
        print(f"ERRO: arquivo não encontrado: {args.csv_path}")
        sys.exit(1)

    import_csv(args.csv_path, args.name_col, args.url_col, args.ats_col, args.handle_col)


if __name__ == "__main__":
    main()
