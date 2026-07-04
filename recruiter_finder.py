"""
Recruiter Finder
================
Pra vagas onde o ATS NÃO expõe recruiter no JSON (~60% dos casos),
gera URLs prontas de busca no LinkedIn pra você abrir manualmente.

Por que semi-manual: o TOS do LinkedIn proíbe scraping autenticado.
Gerando link de busca, você fica do lado seguro — só ajuda a clicar mais rápido.

Output: output/recruiters.html com tabela linkada.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "jobs.db"
OUTPUT = ROOT / "output" / "recruiters.html"


def linkedin_recruiter_search(company: str, title: str) -> str:
    """
    Constrói busca LinkedIn por pessoas com 'recruiter' OR 'talent acquisition' na
    empresa específica, com hint da role.
    """
    role_hint = ""
    if "data engineer" in title.lower():
        role_hint = " data engineer"
    elif "analytics" in title.lower():
        role_hint = " analytics"

    query = f'recruiter OR "talent acquisition"{role_hint} {company}'
    return f"https://www.linkedin.com/search/results/people/?keywords={quote_plus(query)}"


def linkedin_company_search(company: str) -> str:
    return f"https://www.linkedin.com/search/results/companies/?keywords={quote_plus(company)}"


def email_guess(company_domain: str, first_name_hint: str = "") -> list[str]:
    """
    Sugere padrões comuns de email corporativo pra você testar.
    Não envia nada — só sugere.
    """
    if not company_domain:
        return []
    patterns = [
        f"recruiting@{company_domain}",
        f"talent@{company_domain}",
        f"jobs@{company_domain}",
        f"careers@{company_domain}",
        f"hr@{company_domain}",
        f"people@{company_domain}",
    ]
    return patterns


def render() -> None:
    if not DB_PATH.exists():
        print("DB não existe ainda. Rode `python pipeline.py scrape` primeiro.")
        return

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT j.id, j.title, j.score, j.tier, j.url, j.recruiter_name,
               j.recruiter_email, c.name AS company, c.source_url AS company_url
        FROM jobs j JOIN companies c ON c.id = j.company_id
        WHERE j.score >= 12 AND j.tier IN ('T1', 'T2') AND j.status = 'new'
        ORDER BY j.score DESC
        LIMIT 200
        """
    ).fetchall()
    conn.close()

    items = []
    for r in rows:
        company_url = r["company_url"] or ""
        # extrai domínio simples
        domain = ""
        if company_url:
            from urllib.parse import urlparse
            domain = urlparse(company_url).netloc.replace("www.", "")

        items.append({
            "company": r["company"],
            "title": r["title"],
            "score": r["score"],
            "tier": r["tier"],
            "url": r["url"],
            "recruiter_known": r["recruiter_name"] or "—",
            "recruiter_email": r["recruiter_email"] or "",
            "linkedin_recruiter": linkedin_recruiter_search(r["company"], r["title"]),
            "linkedin_company": linkedin_company_search(r["company"]),
            "email_guesses": email_guess(domain),
        })

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    html = build_html(items)
    OUTPUT.write_text(html, encoding="utf-8")
    print(f"✅ {len(items)} entradas em {OUTPUT}")


def build_html(items: list[dict]) -> str:
    rows_html = ""
    for it in items:
        emails = "<br>".join(it["email_guesses"]) if it["email_guesses"] else "—"
        rows_html += f"""
        <tr>
          <td><span class="tier-{it['tier'].lower()}">{it['tier']}</span> <b>{it['score']}</b></td>
          <td><b>{it['company']}</b><br><small>{it['title']}</small></td>
          <td>{it['recruiter_known']}<br><small>{it['recruiter_email']}</small></td>
          <td><a href="{it['linkedin_recruiter']}" target="_blank">🔍 LinkedIn recruiters</a><br>
              <a href="{it['linkedin_company']}" target="_blank">🏢 LinkedIn company</a></td>
          <td><small>{emails}</small></td>
          <td><a href="{it['url']}" target="_blank">apply ↗</a></td>
        </tr>
        """
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Recruiter Finder — Job Radar</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
  body{{font-family:system-ui,sans-serif}}
  .tier-t1{{background:#10b981;color:white;padding:2px 6px;border-radius:4px;font-size:11px}}
  .tier-t2{{background:#3b82f6;color:white;padding:2px 6px;border-radius:4px;font-size:11px}}
  .tier-t3{{background:#6b7280;color:white;padding:2px 6px;border-radius:4px;font-size:11px}}
</style></head>
<body class="bg-gray-50 p-6">
<div class="max-w-7xl mx-auto">
  <h1 class="text-3xl font-bold mb-4">👤 Recruiter Finder</h1>
  <p class="text-gray-600 mb-6">Top T1/T2 jobs com links de busca LinkedIn + sugestões de emails corporativos.
     <strong>Uso manual</strong>: clica nos links, valida no LinkedIn, faz outreach.</p>
  <div class="bg-white rounded-lg shadow overflow-hidden">
    <table class="min-w-full text-sm">
      <thead class="bg-gray-100"><tr>
        <th class="px-3 py-2 text-left">Score</th>
        <th class="px-3 py-2 text-left">Role</th>
        <th class="px-3 py-2 text-left">Recruiter conhecido</th>
        <th class="px-3 py-2 text-left">LinkedIn search</th>
        <th class="px-3 py-2 text-left">Email guesses</th>
        <th class="px-3 py-2 text-left">Apply</th>
      </tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
  </div>
</div></body></html>"""


if __name__ == "__main__":
    render()
