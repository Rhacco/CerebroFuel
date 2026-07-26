# CF v3.5.0

Alle 3 Minuten: geschlossene 1-Minuten-Börsenkerzen für schnelle Signale; LCW für Gesamt-Map und Langzeitkontext. Der vorhandene `v350`-Cache wird weiterverwendet.

```text
BTC🟢PAY🔵SCP🟢UTL🟣AI🔵MEM🟡:03
🟢6▲7🟢B🟢🔵P🟢V🔵🟢🟢N🟡UWSJUP
```

- `▲` Kaufchance · `▼` Verkaufswarnung · Zahl `1–8` Stärke
- 🟣 außergewöhnlich · 🟢 klar · 🔵 früh · 🟡 neutral · 🟠 Warnung · 🔴 stark negativ · ⚪ fehlt
- `7` 7-Tage-Volumen · `B` zu BTC 24h/7d · `P` Druck · `V` 10/30/60 Min · `N` Erholung
- Ende: Kategorie `P/S/U/A/M` + zwei Wochenbereiche: `S` Sa/So · `M` Mo/Di · `W` Mi · `D` Do/Fr · `?` nicht belastbar
- Liste: Platz 1 immer; Platz 2/3 nur bei engem Abstand; weitere Plätze nur bei starker, bestätigter oder deutlich beschleunigender Lage.

## Seltene Discord-Kürzel

| Kürzel | Vollständiger Coin | Kategorie |
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

Secrets: `LCW_API_KEY`, `DISCORD_WEBHOOK_URL` · Cron: `*/3 * * * *`
