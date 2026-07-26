# CF v3.5.0

Alle 3 Minuten werden geschlossene 1‑Minuten‑Kerzen von Binance/Coinbase ausgewertet. LiveCoinWatch liefert Gesamt‑Map, Market Cap sowie Langzeit‑ und Wochenkontext. Die vorhandenen `v350`‑Caches bleiben gültig.

```text
BTC🟢PAY🔵SCP🟢UTL🟣AI🔵MEM🟡:03
🟢6▲7🟢B🟢🔵P🟢V🔵🟢🟢N🟡UWSJUP
```

- `▲` Kauf · `▼` Verkauf · Zahl `1–8` Signalstärke
- 🟣 außergewöhnlich positiv · 🟢 klar positiv · 🔵 frühes Kaufsignal · 🟡 neutral · 🟠 Verkaufswarnung · 🔴 starkes Verkaufssignal · ⚪ Daten fehlen
- Kauf nur nach echtem Preisrückgang: günstige Lage, gehaltenes 3‑Stunden‑Tief, abgeschlossene Stabilisierung und erneut steigende Nachfrage. Aktivität allein reicht nicht.
- `7` 7‑Tage‑Volumen · `B` relativ zu BTC 24h/7d · `P` Druck · `V` 10/30/60 Min · `N` Erholung
- Ende: Kategorie `P/S/U/A/M` + zwei Wochenbereiche: `S` Sa/So · `M` Mo/Di · `W` Mi · `D` Do/Fr · `?` nicht belastbar
- Auswahl: stärkstes Signal immer. Bis zu zwei ähnlich auffällige Käufe und zwei ähnlich auffällige Verkäufe können gleichzeitig erscheinen. Weitere Plätze bis maximal acht benötigen starke, bestätigte oder schnell beschleunigende Signale.
- Falling Knife, Überdehnung, schlechter Spread, geringe Ausführbarkeit sowie Unlock‑ und Ereignisrisiken sperren oder senken Signale.
- Versand bei relevanter Änderung oder spätestens nach 15 Minuten.

## Ungewöhnliche Kürzel

| Kürzel | Coin | Kategorie |
|---|---|---|
| `ARK` | Arkham (`ARKM`) | AI |
| `MND` | Monad (`MON`) | SCP |
| `BOM` | BOOK OF MEME (`BOME`) | MEM, nicht aktiv |
| `GAL` | Gala (`GALA`) | UTL |
| `ORD` | Ordinals (`ORDI`) | UTL |
| `TRB` | Tellor (`TRB`) | UTL, nicht aktiv |
| `PND` | Pendle (`PENDLE`) | UTL |
| `VRT` | Virtuals Protocol (`VIRTUAL`) | AI |
| `1IN` | 1inch (`1INCH`) | UTL |
| `POL` | Polygon Ecosystem Token (`POL`) | SCP |
| `CAK` | PancakeSwap (`CAKE`) | UTL |
| `JUP` | Jupiter (`JUP`) | UTL |

Secrets: `LCW_API_KEY`, `DISCORD_WEBHOOK_URL` · Cloudflare‑Cron: `*/3 * * * *`

<!-- package revision: v3.5.0-dual-discount-r3 -->
