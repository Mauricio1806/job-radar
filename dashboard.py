"""Dashboard v6 — usa first_seen_at (delta detection real)"""

from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "jobs.db"
OUT_PATH = ROOT / "docs" / "index.html"


def load_jobs() -> list[dict]:
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT j.id, j.title, j.location, j.remote_flag, j.score, j.tier,
               j.industry, j.language, j.visa_sponsorship, j.matched_keywords,
               j.url, j.posted_at, j.first_seen_at, j.last_seen_at,
               j.recruiter_name, j.status, j.ats,
               c.name AS company, c.strategy, c.tier_priority, c.phase
        FROM jobs j JOIN companies c ON c.id = j.company_id
        WHERE j.score >= 10 AND j.tier != 'BLOCKED'
        ORDER BY j.first_seen_at DESC, j.score DESC
        LIMIT 1000
        """
    ).fetchall()
    conn.close()
    return [
        {
            "id": r["id"], "title": r["title"], "company": r["company"],
            "location": r["location"] or "", "remote": bool(r["remote_flag"]),
            "score": r["score"], "tier": r["tier"] or "T3",
            "industry": r["industry"] or "", "language": r["language"] or "en",
            "visa": bool(r["visa_sponsorship"]),
            "keywords": json.loads(r["matched_keywords"] or "[]"),
            "url": r["url"], "posted_at": r["posted_at"] or "",
            "first_seen_at": r["first_seen_at"] or "",
            "recruiter": r["recruiter_name"] or "", "ats": r["ats"] or "",
            "status": r["status"] or "new", "strategy": r["strategy"] or "",
            "company_tier": r["tier_priority"] or "C", "phase": r["phase"] or 1,
        }
        for r in rows
    ]


def load_stats() -> dict:
    if not DB_PATH.exists():
        return {}
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    day_ago = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    companies = conn.execute("SELECT COUNT(*) AS n FROM companies").fetchone()["n"]
    jobs_total = conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE tier != 'BLOCKED'").fetchone()["n"]
    fresh_1h = conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE first_seen_at >= ? AND score >= 10", (hour_ago,)).fetchone()["n"]
    fresh_24h = conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE first_seen_at >= ? AND score >= 10", (day_ago,)).fetchone()["n"]
    t1 = conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE tier='T1' AND score >= 10 AND status='new'").fetchone()["n"]
    t2 = conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE tier='T2' AND score >= 10 AND status='new'").fetchone()["n"]
    conn.close()
    return {"companies": companies, "jobs_total": jobs_total, "fresh_1h": fresh_1h,
            "fresh_24h": fresh_24h, "t1": t1, "t2": t2}


HTML = """<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8">
<title>Job Radar v6</title><script src="https://cdn.tailwindcss.com"></script>
<style>body{font-family:system-ui,sans-serif}
.tier-t1{background:#10b981;color:white}.tier-t2{background:#3b82f6;color:white}.tier-t3{background:#6b7280;color:white}
.score-high{background:#10b981;color:white}.score-mid{background:#f59e0b;color:white}.score-low{background:#9ca3af;color:white}
.radar-super{background:#dc2626;color:white;font-weight:bold}.radar-fresh{background:#f59e0b;color:white}
.radar-recent{background:#3b82f6;color:white}.radar-normal{background:#e5e7eb}.radar-old{background:#9ca3af;color:white}
tr:hover{background:#f9fafb}</style></head><body class="bg-gray-50 p-6">
<div class="max-w-7xl mx-auto">
<h1 class="text-3xl font-bold mb-2">📡 Job Radar v6</h1>
<p class="text-gray-600 mb-6">Delta detection real · {{updated}}</p>
<div class="grid grid-cols-2 sm:grid-cols-5 gap-3 mb-6">
<div class="bg-white rounded shadow p-4"><div class="text-xs uppercase text-gray-500">🔥 &lt; 1h</div><div class="text-2xl font-bold text-red-600">{{fresh_1h}}</div></div>
<div class="bg-white rounded shadow p-4"><div class="text-xs uppercase text-gray-500">⚡ &lt; 24h</div><div class="text-2xl font-bold text-amber-600">{{fresh_24h}}</div></div>
<div class="bg-white rounded shadow p-4"><div class="text-xs uppercase text-gray-500">🟢 T1 USD</div><div class="text-2xl font-bold text-emerald-600">{{t1}}</div></div>
<div class="bg-white rounded shadow p-4"><div class="text-xs uppercase text-gray-500">🔵 T2 EUR</div><div class="text-2xl font-bold text-blue-600">{{t2}}</div></div>
<div class="bg-white rounded shadow p-4"><div class="text-xs uppercase text-gray-500">Total</div><div class="text-2xl font-bold">{{jobs_total}}</div></div>
</div>
<div class="bg-white rounded shadow p-4 mb-4 flex gap-3 flex-wrap">
<input id="q" placeholder="Filtrar..." class="flex-1 min-w-[200px] border rounded px-3 py-2 text-sm">
<select id="tf" class="border rounded px-2 py-2 text-sm"><option value="">All tiers</option><option value="T1">T1</option><option value="T2">T2</option><option value="T3">T3</option></select>
<select id="ff" class="border rounded px-2 py-2 text-sm"><option value="">Qualquer época</option><option value="1">🔥 &lt; 1h</option><option value="24">⚡ &lt; 24h</option><option value="72">3 dias</option></select>
<span id="cnt" class="text-sm text-gray-600 ml-auto self-center"></span>
</div>
<div class="bg-white rounded shadow overflow-x-auto"><table class="min-w-full text-sm">
<thead class="bg-gray-100"><tr>
<th class="px-3 py-2 text-left">📡 No radar</th><th class="px-3 py-2 text-left">Tier</th><th class="px-3 py-2 text-left">Score</th>
<th class="px-3 py-2 text-left">Título</th><th class="px-3 py-2 text-left">Empresa</th><th class="px-3 py-2 text-left">Local</th>
<th class="px-3 py-2 text-left">🛂</th><th class="px-3 py-2 text-left">Link</th></tr></thead>
<tbody id="rows"></tbody></table></div></div>
<script>
const D={{data_json}};const N=new Date();
function h(iso){if(!iso)return 999999;return (N-new Date(iso))/3600000;}
function r(iso){const x=h(iso);if(x<1)return{l:'🔥 '+Math.round(x*60)+'min',c:'radar-super'};
if(x<6)return{l:'🔥 '+Math.round(x)+'h',c:'radar-fresh'};if(x<24)return{l:'⚡ '+Math.round(x)+'h',c:'radar-recent'};
if(x<72)return{l:Math.round(x/24)+'d',c:'radar-normal'};return{l:'⚠️ '+Math.round(x/24)+'d',c:'radar-old'};}
function sb(s){let c="score-low";if(s>=18)c="score-high";else if(s>=10)c="score-mid";return `<span class="${c} text-xs font-bold px-2 py-1 rounded">${s}</span>`;}
function tb(t){return `<span class="tier-${t.toLowerCase()} text-xs font-bold px-2 py-1 rounded">${t}</span>`;}
function render(){const q=document.getElementById("q").value.toLowerCase();const tf=document.getElementById("tf").value;const ff=document.getElementById("ff").value;
const f=D.filter(j=>{if(tf&&j.tier!==tf)return false;if(ff&&h(j.first_seen_at)>parseFloat(ff))return false;
if(!q)return true;const b=(j.title+" "+j.company+" "+j.location+" "+(j.keywords||[]).join(" ")).toLowerCase();return b.includes(q);});
document.getElementById("cnt").textContent=`${f.length} de ${D.length} vagas`;
document.getElementById("rows").innerHTML=f.map(j=>{const rd=r(j.first_seen_at);
return `<tr class="border-t"><td class="px-3 py-2"><span class="${rd.c} text-xs px-2 py-1 rounded">${rd.l}</span></td>
<td class="px-3 py-2">${tb(j.tier)}</td><td class="px-3 py-2">${sb(j.score)}</td>
<td class="px-3 py-2 font-medium">${j.title}</td><td class="px-3 py-2">${j.company}</td>
<td class="px-3 py-2">${j.remote?"🌎 ":""}${j.location}</td><td class="px-3 py-2 text-center">${j.visa?"🛂":""}</td>
<td class="px-3 py-2"><a href="${j.url}" target="_blank" class="text-blue-600 hover:underline">apply ↗</a></td></tr>`;}).join("");}
["q","tf","ff"].forEach(id=>{document.getElementById(id).addEventListener("input",render);document.getElementById(id).addEventListener("change",render);});render();
</script></body></html>"""


def render():
    jobs = load_jobs()
    stats = load_stats()
    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = (HTML.replace("{{fresh_1h}}", str(stats.get("fresh_1h", 0)))
                .replace("{{fresh_24h}}", str(stats.get("fresh_24h", 0)))
                .replace("{{t1}}", str(stats.get("t1", 0)))
                .replace("{{t2}}", str(stats.get("t2", 0)))
                .replace("{{jobs_total}}", str(stats.get("jobs_total", 0)))
                .replace("{{updated}}", updated)
                .replace("{{data_json}}", json.dumps(jobs, ensure_ascii=False)))
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(html, encoding="utf-8")
    print(f"dashboard -> {OUT_PATH} ({len(jobs)} jobs)")


if __name__ == "__main__":
    render()