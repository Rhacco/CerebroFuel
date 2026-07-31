# Crypto Signal Monitor v3.8.1

Lighter-native Analyse für `BTC, ETH, SOL, HYPE` mit kompakter Discord-Ausgabe, verifizierten Ereignissen und Paper-Trading.

## Signale

- `E` – frische Expansion nach Kompression; nicht mehr anzeigen, sobald der Weg weit verbraucht ist
- `T` – schneller Dip beziehungsweise Bounce im klar laufenden Preis-Trend mit anhaltend erhöhtem Volumen; bewusst schwächer und nie Sofortsignal
- `W` – bestätigte Wende nach außergewöhnlichem Schock
- `+` / `-` – Richtung ohne qualifiziertes E/T/W-Setup

## Ereignisse

Kopfzeile: alle vier Coins, BTC fest enthalten, ohne Schluss-Uhrzeit. Pro Coin erscheint höchstens das wichtigste bestätigte Ereignis; Unlocks werden bis zu 14 Tage voraus gezeigt.

`FED` Fed · `CPI` Inflation · `NFP` Arbeitsmarkt · `PPI` Erzeugerpreise · `GDP` BIP · `PCE` Konsum/Inflation · `EXP` Verfall · `ETF` ETF-Entscheidung · `U` Unlock · `UPG` Upgrade · `GOV` Governance · `NET` Störung · `SUP` Angebot · `N` sonstige kritische News

Suffixe: `U5D` in fünf Tagen · `CPI@14:30` heute · `FED@20` volle Stunde · `U0D` heute ohne bestätigte Uhrzeit · `NET!` aktiv.

BLS, BEA, Fed sowie Solana-/Hyperliquid-Status werden offiziell abgerufen. Coin-spezifische Termine wie Unlocks werden nur aus `events.json`, `CRYPTO_EVENTS_JSON` oder `CRYPTO_EVENTS_URL` übernommen, wenn `verified=true`, eine HTTPS-Quelle und eine freigegebene Quelldomain vorhanden sind. Diese Freigabe ist kuratiert; die App prüft Format und Domain, nicht den Inhalt der verlinkten Seite. Ereignisse erzeugen nie selbst Long oder Short; kurz davor werden neue Paper-Trades blockiert oder der Hebel begrenzt.

## Discord

- Zeile 1: `BTC`, `ETH`, `SOL`, `HYP` mit Farbe und optionalem Ereigniskürzel
- Detailzeilen: nur Kandidaten mit hoher Aufmerksamkeit; Score direkt hinter `E`, `T`, `W`, `+` oder `-`
- nur Abweichungen erscheinen als `V!`, `L!`, `K!`, `B!`, `F!`
- unveränderte Berichte werden unterdrückt; Heartbeat nach 15 Minuten

## Paper-Trading

Bis zu drei parallele Positionen. Starke Signale dürfen als kleine Probe starten; bestätigte E-/W-Signale können ausgebaut werden. T bleibt eine kleine Probe. Margin-, Stop-Risiko-, Richtungs-, Liquiditäts- und Ereignislimits bleiben aktiv.

## Start

```bash
python main.py --no-send
```

Für Discord `DISCORD_WEBHOOK_URL` setzen. Optionaler verifizierter Ereignisfeed: `CRYPTO_EVENTS_URL` oder `CRYPTO_EVENTS_JSON`. Laufzeitdateien liegen in `output/`.
