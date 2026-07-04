# 🎯 JOB RADAR v3

Pipeline autônomo de Data Engineering job hunting — **Plano Híbrido USD → EUR** baseado na MASTER DATABASE do Notion.

Gerado pra Mauricio Esquivel (Data Engineer, 5+ anos, Brasil → Espanha Out/2026).

---

## 🏛️ Arquitetura

```
config/companies.yaml   ←  90 entradas da MASTER DATABASE
  │
  ├─ 13 manual_network        → relatório de signups pendentes (Toptal, Andela, A.Team...)
  ├─ 18 usd_contractor        → ATS scrape (Tier B staff aug)
  ├─ 19 global_consulting     → ATS scrape (DataArt, Intellias, N-iX...)
  ├─ 13 job_board_aggregator  → API JSON dedicada (Remotive, RemoteOK, Himalayas, Arbeitnow, WWR)
  └─ 27 eu_sponsor            → ATS scrape + filtro visa sponsorship (Zalando, HelloFresh, N26...)
              │
              ▼
       filter.py (perfil-aware)
       │   ├─ Gate: data engineer / ingeniero de datos
       │   ├─ Tier 1 stack (CV em produção):  Databricks, PySpark, dbt, AWS Glue, ADF, Synapse, Airflow, Power BI
       │   ├─ Tier 2 stack (familiar):        Snowflake, Kafka, K8s, Terraform, GCP, BigQuery
       │   ├─ Domain bonus:                   SAP S/4HANA, star schema, data observability
       │   ├─ Industry boost:                 Oil & Gas, Renewable, Healthcare, Mobility, Manufacturing
       │   ├─ Region tiers:                   T1 USD LATAM > T2 EUR EU > T3 outras
       │   ├─ Currency:                       USD primary, EUR secondary
       │   ├─ Visa sponsorship:               🛂 +5 (Blue Card, relocation package, etc.)
       │   ├─ Language detection:             ES bonus
       │   └─ Blocklist:                      Junior, US-only, Data Scientist, ML Engineer
              │
              ▼
       SQLite (jobs.db)
              │
              ├──► docs/index.html (GitHub Pages dashboard com goal tracker Fase 1)
              ├──► docs/manual_signups.md (lembretes Tier A)
              ├──► Telegram notifier
              └──► output/cv/{job_id}.md (CV tailored por vaga via Claude API)
```

---

## 🧠 Como o pipeline pensa

### Phase routing (Página C do Notion)

| Phase | Foco | Strategies ativos |
|---|---|---|
| **1** (0-6 sem) | Fechar USD remoto | `usd_contractor` + `global_consulting` + `job_board_aggregator` + `manual_network` |
| **2** (6-12 sem) | Pipeline EU sponsor | `eu_sponsor` (com visa filter ativo) |
| **3** (3-9 mes) | Estabilidade ambos | Tudo |

Roda só Fase 1: `python pipeline.py scrape --phase 1`
Roda só Fase 2 (quando ativa): `python pipeline.py scrape --phase 2`
Roda tudo: `python pipeline.py scrape`

### Estratégia por categoria

| Strategy | O que acontece |
|---|---|
| `manual_network` | NÃO scrapeia. Gera linha em `manual_signups.md` com link de apply. Você marca status='completed' quando assinar. |
| `usd_contractor` | Detecta ATS via `ats_detector.py` → roda adapter Greenhouse/Lever/etc. |
| `global_consulting` | Mesma coisa que usd_contractor. |
| `job_board_aggregator` | Usa adapter dedicado (`adapters/aggregators.py`). API JSON pública. |
| `eu_sponsor` | Mesma scrape + bonus pesado se "visa sponsorship" no texto. |

---

## 📦 Estrutura do repo

```
job-radar/
├── README.md
├── requirements.txt
├── ats_detector.py             # detecta ATS por URL/HTML
├── adapters/
│   ├── __init__.py             # Greenhouse, Lever, Ashby, SmartRec, Workable, Recruitee, Teamtailor
│   └── aggregators.py          # Remotive, RemoteOK, Himalayas, Arbeitnow, WWR (RSS)
├── filter.py                   # scoring perfil-aware
├── db.py                       # SQLite schema + CRUD
├── pipeline.py                 # orquestrador (import|detect|scrape|notify|manual|all)
├── notifier.py                 # Telegram HTML
├── dashboard.py                # docs/index.html com goal tracker
├── cv_tailoring.py             # Claude API → CV ajustado por vaga
├── recruiter_finder.py         # links LinkedIn pra recruiters
├── import_csv.py               # parser CSV → companies.yaml
├── config/
│   ├── companies.yaml          # 90 entradas da MASTER DATABASE
│   ├── keywords.yaml           # 300+ keywords/tokens calibrados
│   └── cv_base.md              # seu CV em markdown (source of truth)
└── .github/workflows/
    └── scrape-jobs.yml         # cron 4×/dia
```

---

## 🚀 Setup local

```bash
git clone <seu_repo>
cd job-radar
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1) Importar companies + detectar ATS automaticamente
python pipeline.py detect
# (skip pra manual_networks; ~80 empresas vão pra detecção; demora ~3min na primeira vez)

# 2) Rodar scrape Fase 1
python pipeline.py scrape --phase 1
# (Fase 2 quando ativar): python pipeline.py scrape --phase 2

# 3) Notificar
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
python pipeline.py notify

# 4) Gerar dashboard + manual report
python dashboard.py
python pipeline.py manual

# 5) (Opcional) Tailoring CVs pras T1 high-score
export ANTHROPIC_API_KEY=sk-ant-...
python cv_tailoring.py --top 10
```

---

## 🔐 GitHub secrets

| Nome | Uso |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot pra notificações |
| `TELEGRAM_CHAT_ID` | Seu chat |
| `ANTHROPIC_API_KEY` | (opcional) CV tailoring no workflow |

---

## 🧪 Casos de teste validados

| Cenário | Resultado |
|---|---|
| Senior DE Databricks Lakehouse, Remote LATAM, USD | T1, score 37 ✅ |
| Senior DE Berlin + Visa Sponsorship | T2 🛂, score 32 ✅ |
| Senior DE Madrid (espanhol) | T2, score 30 ✅ |
| Staff DE SAP S/4HANA Munich + Blue Card | T2 🛂, score 38 ✅ (top match) |
| Staff DE LATAM via aggregator | T1, score 26 ✅ |
| Data Engineer Internship | BLOCKED ✅ |
| Data Scientist | BLOCKED ✅ |
| Sr DE US-only | BLOCKED ✅ |

---

## ⚖️ Compliance

- Todos os endpoints são públicos e não autenticados
- Rate limit conservador: 2-3s entre requests por host
- Zero scraping de LinkedIn autenticado (só links de busca)
- Respeita `robots.txt` quando aplicável
