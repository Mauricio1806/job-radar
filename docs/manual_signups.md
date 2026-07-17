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
| Crossover | A | ⏳ manual | [apply](https://www.crossover.com/jobs) |  |
| Gun.io | A | ⏳ manual | [apply](https://gun.io/find-work) |  |
| Terminal | A | ⏳ manual | [apply](https://www.terminal.io/engineers) |  |
| Toptal | A | ⏳ manual | [apply](https://www.toptal.com/talent/apply) | Top 3%. Multi-step interview. |
| X-Team | A | ⏳ manual | [apply](https://x-team.com/developers) | Long-term contracts. Alto DE demand. |
| Azumo | B | ⏳ manual | [apply](https://azumo.com/jobs/) |  |
| BEON.tech | B | ⏳ manual | [apply](https://beon.tech/jobs) |  |
| CloudDevs | B | ⏳ manual | [apply](https://clouddevs.com/join-as-developer/) |  |
| Devlane | B | ⏳ manual | [apply](https://devlane.com/careers) | jobs.devlane.com offline — URL nova a verificar. |
| Distillery | B | ⏳ manual | [apply](https://distillery.com/jobs/) | Cloudflare bloqueia Playwright. Manual por enquanto. |
| Koombea | B | ⏳ manual | [apply](https://www.koombea.com/jobs) |  |
| Lemon.io | B | ⏳ manual | [apply](https://lemon.io/apply) |  |
| Near | B | ⏳ manual | [apply](https://jobs.hirewithnear.com/) |  |
| Proxify | B | ⏳ manual | [apply](https://proxify.io/apply) |  |
| Remotebase | B | ⏳ manual | [apply](https://remotebase.com/jobs) |  |
| Revelo | B | ⏳ manual | [apply](https://jobs.revelo.com/) |  |
| Tekton Labs | B | ⏳ manual | [apply](https://tektonlabs.com/jobs/) |  |
| Turing | B | ⏳ manual | [apply](https://developers.turing.com/jobs) |  |
| VanHack | B | ⏳ manual | [apply](https://app.vanhack.com/jobs) |  |

## Como atualizar status

Quando você completar um screening, rode:
```sql
UPDATE companies SET status='completed' WHERE name='Toptal';
```
(ou edite via DB browser)