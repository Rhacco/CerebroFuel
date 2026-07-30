# CF v3.6.0

Package revision: `v3.6.0-lighter-structure-r2`

Read-only Lighter signal monitor for small, manually executed perpetual trades.

- Candidate universe: only the final Lighter shortlist from the July 2026 plan.
- Lighter supplies active perp metadata, closed 1-minute quote-volume candles,
  funding and the executable order book.
- Opportunity (market quality) and direction (Long/Short) are separate.
- Direction combines price and quote volume over 10/20/60 minutes. Stable or
  slightly rising price with rising volume is accumulation; falling price with
  rising volume is selling pressure.
- Spread/slippage, 24h volume, OI, Volume/OI, funding and window quality can
  deliberately produce `NO_TRADE` or `INVALID_DATA`.
- Discord is exactly nine lines: BTC market header plus Top-8. Every line is at
  most 34 Unicode code points, with no blank lines.

Legend: `🟣 BUY`, `🟢 WATCH_LONG`, `🟡 NO_TRADE`, `🟠 WATCH_SHORT`,
`🔴 SELL`, `🟤 INVALID_DATA`; `O` opportunity, `D` direction strength,
`C` roundtrip cost in basis points, `F` funding, `V` 10/20/60 windows.

Run:

```bash
python main.py --no-send
```

The monitor never submits orders. Output details are written to
`output/latest.json`; the compact Discord text is written to `output/latest.txt`.

## Paketstruktur

- `.github/workflows/monitor.yml` — GitHub-Actions-Lauf
- `output/.gitkeep` — erhält den Ausgabeordner im Repository
- `main.py` — Programmeinstieg
- `lighter_monitor.py` — Lighter-Daten, Bewertung und Bericht
- `discord_sender.py` — Discord-Versand
- `config.json` — Kandidaten und Schwellenwerte
- `cloudflare-worker.js` — Drei-Minuten-Auslöser

Tests werden vor der Freigabe ausgeführt, sind aber nicht Bestandteil des
Release-Pakets und keine Voraussetzung für den GitHub-Actions-Lauf.
