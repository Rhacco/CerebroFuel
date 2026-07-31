# Crypto Signal Monitor v3.8.2

Lighter-Analyse für `HYPE, SOL, ETH, BTC` mit frühen E/T/W-Signalen, verifizierten Ereignissen und internem Paper-Trading.

## Signale

- `E` – frische Expansion nach Kompression; verschwindet, sobald der Weg weit verbraucht ist
- `T` – kurzer echter Dip/Bounce mit bestätigter Wiederaufnahme eines klaren Preis- und Aktivitätstrends; schwaches Probe-Setup, nie lila
- `W` – bestätigte Wende nach außergewöhnlichem Schock
- `+` / `-` – Richtung ohne qualifiziertes E/T/W-Setup

## Discord

Zeile 1 bleibt fest: `HYP SOL ETH BTC`. Pro Coin erscheint höchstens das wichtigste bestätigte Ereignis. Zukünftige Termine werden einmal je voller Stunde eingeblendet; heutige Termine bleiben bis zum Ereignis sichtbar. BTC bleibt auch in den Detailzeilen immer enthalten.

Warnungen bedeuten immer ein Problem: `V!` Tape/Volumen · `L!` Liquidität/OI · `K!` Spread/Kosten · `B!` BTC-Kontext widerspricht · `F!` Funding ungünstig/unsicher.

## Ereigniskürzel

`FED` Fed/FOMC · `CPI` Inflation · `NFP` US-Arbeitsmarkt · `PPI` Erzeugerpreise · `GDP` BIP · `PCE` Konsum/Inflation · `EXP` großer Optionsverfall · `ETF` ETF-Entscheidung · `U` Unlock · `UPG` Upgrade/Wartung · `GOV` Governance · `NET` Störung · `SUP` Angebot · `N` sonstige kritische News.

Suffixe: `U5D` in fünf Tagen · `CPI@14:30` heute · `FED@20` volle Stunde · `U0D` heute ohne bestätigte Uhrzeit · `NET!` aktiv. Unlocks erscheinen bis zu 14 Tage vorher.

Automatisch geprüft werden offizielle BLS-, BEA- und Fed-Termine, große BTC-/ETH-Optionsverfälle über die öffentliche Deribit-API sowie Solana-/Hyperliquid-Statusmeldungen. Unlocks, ETF-, Upgrade-, Governance-, Supply- und projektspezifische News kommen nur aus `events.json`, `CRYPTO_EVENTS_JSON` oder `CRYPTO_EVENTS_URL` mit `verified=true`, HTTPS-Quelle und erlaubter Domain. Ereignisse erzeugen nie selbst Long oder Short.

## Paper-Trading

Paper-Aktionen stehen nur in Logs/JSON. Bis zu drei Positionen dürfen parallel laufen; starke Signale starten früher als kleine Probe und werden erst nach Bestätigung ausgebaut. Risiko-, Kosten-, Liquiditäts- und Ereignisgrenzen bleiben aktiv.

Ab mindestens 16 abgeschlossenen Trades prüft die App aufgezeichnete Eintrittsparameter gegen die übrige Stichprobe. Ein Hinweis braucht mindestens sechs betroffene Trades aus zwei Coins, negative Ergebnisse in beiden zeitlichen Stichprobenhälften und einen klaren R-Abstand zur Vergleichsgruppe. Die App ändert keine Parameter selbst. Ein neuer belastbarer Befund ergänzt Discord einmalig um:

`Parameter-Fehler/Optimierung gefunden!`

## Start

```bash
python main.py --no-send
```

Für Discord `DISCORD_WEBHOOK_URL` setzen. Laufzeitdateien liegen in `output/`.
