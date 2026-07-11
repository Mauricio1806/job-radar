# 🤝 Manual Network Signups

Talent networks que **não têm job board público** — exigem signup + screening.
Pipeline não consegue scrapear; você precisa aplicar manualmente.

**Meta Fase 1 (Página C):** completar screening em pelo menos 4 talent networks.

| Network | Tier | Status | URL | Notes |
| --- | --- | --- | --- | --- |
| A.Team | A | ⏳ manual | [apply](https://www.a.team/join) |  |
| Andela | A | ⏳ manual | [apply](https://andela.com/talent) |  |
| Arc.dev | A | ⏳ manual | [apply](https://arc.dev/remote-jobs) |  |
| Athyna | A | ⏳ manual | [apply](https://www.athyna.com/talent) | LATAM + USD. Signup + screening. |
| Braintrust | A | ⏳ manual | [apply](https://www.usebraintrust.com/join) |  |
| Gun.io | A | ⏳ manual | [apply](https://gun.io/developers) |  |
| Terminal | A | ⏳ manual | [apply](https://www.terminal.io/engineers) |  |
| Toptal | A | ⏳ manual | [apply](https://www.toptal.com/talent/apply) | Top 3% screening. Multi-step interview. |
| X-Team | A | ⏳ manual | [apply](https://x-team.com/developers) |  |
| CloudDevs | B | ⏳ manual | [apply](https://clouddevs.com/join-as-developer) |  |
| Lemon.io | B | ⏳ manual | [apply](https://lemon.io/developers) |  |
| Proxify | B | ⏳ manual | [apply](https://proxify.io/developers) |  |
| Remotebase | B | ⏳ manual | [apply](https://remotebase.com/talent) |  |

## Como atualizar status

Quando você completar um screening, rode:
```sql
UPDATE companies SET status='completed' WHERE name='Toptal';
```
(ou edite via DB browser)