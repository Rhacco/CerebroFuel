# CF v3.5.0

Alle 3 Minuten werden geschlossene 1‑Minuten‑Kerzen von Binance/Coinbase ausgewertet. LiveCoinWatch liefert Gesamt‑Map, Market Cap sowie Langzeit‑ und Wochenkontext.

```text
BTC🟢PAY🔵SCP🟢UTL🟣AI🔵MEM🟡:03
🔵5▲7🟢B🟢🔵P🟢V🔵🟢🟢N🟡UWSJUP
```

- `▲` Kauf · `▼` Verkauf · Zahl `1–8` Signalstärke
- 🟣 außergewöhnlich bestätigt · 🟢 bestätigt · 🔵 früher günstiger/stabiler Nachzügler · 🟡 neutral · 🟠 Verkaufswarnung · 🔴 bestätigt negativ · ⚪ Daten fehlen
- 🔵: echter Preisrückgang, beginnende Stabilisierung und erneut steigende Nachfrage
- 🟢/🟣: strengere, über mehrere Läufe bestätigte Erholung
- Falling Knife, Überdehnung, schlechter Spread sowie Unlock‑ und Ereignisrisiken sperren oder senken Signale
- `7` 7‑Tage‑Volumen · `B` relativ zu BTC 24h/7d · `P` Druck · `V` 10/30/60 Min · `N` Erholung
- Ende: Kategorie `P/S/U/A/M` + zwei Wochenbereiche: `S` Sa/So · `M` Mo/Di · `W` Mi · `D` Do/Fr · `?` nicht belastbar
- Kauf und Verkauf werden unabhängig ausgewählt. Bis zu zwei ähnlich starke Signale je Richtung können gemeinsam erscheinen; weitere Plätze benötigen hohe oder bestätigte Qualität.
- Versand bei relevanter Änderung oder spätestens nach 15 Minuten.

## Ungewöhnliche Kürzel

| Kürzel | Vollständiger Name | Kategorie |
|---|---|---|
| `ARK` | Arkham | AI |
| `MND` | Monad | SCP |
| `GAL` | Gala | UTL |
| `ORD` | Ordinals | UTL |
| `PND` | Pendle | UTL |
| `VRT` | Virtuals Protocol | AI |
| `1IN` | 1inch | UTL |
| `POL` | Polygon Ecosystem Token | SCP |
| `CAK` | PancakeSwap | UTL |
| `JUP` | Jupiter | UTL |

Secrets: `LCW_API_KEY`, `DISCORD_WEBHOOK_URL` · Cloudflare‑Cron: `*/3 * * * *`

<!-- Package revision: v3.5.0-buy-gate-fix-r5 -->
