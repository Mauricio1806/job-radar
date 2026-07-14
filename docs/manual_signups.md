# 🤝 Manual Network Signups

Talent networks que **não têm job board público** — exigem signup + screening.
Pipeline não consegue scrapear; você precisa aplicar manualmente.

**Meta Fase 1 (Página C):** completar screening em pelo menos 4 talent networks.

| Network | Tier | Status | URL | Notes |
| --- | --- | --- | --- | --- |
| A.Team | A | ⏳ manual | [apply](https://www.a.team/join) | Senior/staff focus. |
| Andela | A | ⏳ manual | [apply](https://andela.com/talent) | Global network. |
| Arc.dev | A | ⏳ manual | [apply](https://arc.dev/remote-jobs) | Remote-first. Job board próprio. |
| Athyna | A | ⏳ manual | [apply](https://www.athyna.com/talent) | LATAM + USD. Signup + screening. |
| Braintrust | A | ⏳ manual | [apply](https://www.usebraintrust.com/join) | Marketplace token-based. |
| Toptal | A | ⏳ manual | [apply](https://www.toptal.com/talent/apply) | Top 3%. Multi-step interview. |
| X-Team | A | ⏳ manual | [apply](https://x-team.com/developers) | Long-term contracts. Alto DE demand. |

## Como atualizar status

Quando você completar um screening, rode:
```sql
UPDATE companies SET status='completed' WHERE name='Toptal';
```
(ou edite via DB browser)