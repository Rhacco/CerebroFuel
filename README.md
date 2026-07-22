# Krypto-Monitor v3.3.2 – Expanded Two-Tail Flash + Unlock Risk

`Kreis Zahl Richtung 7 B24 B7 P V10/V30/V60 N Tage Coin/Minute`

## Aktiver Pool

BTC bleibt Referenz. Aktiv sind **60 eindeutige Altcoins**:

1. die Top 10 nach 30-Tage-Volumen/Market-Cap aus 3x,
2. die Top 10 aus 5x,
3. die Top 10 aus 10x,
4. alle zusätzlichen Fast-Swing-Screenshot-Coins,
5. 21 wiederaufgenommene liquide, volatile oder für Marktbreite nützliche Coins.

### Top 10 – 3x
`ARKM, MON, BOME, GALA, ORDI, TRB, KAITO, MOODENG, BIO, PNUT`

### Top 10 – 5x
`MEGA, W, XPL, FET, INJ, ENA, AAVE, OP, ARB, CRV`

### Top 10 – 10x
`TRUMP, WIF, PEPE, BONK, SUI, UNI, TAO, LTC, DOGE, ADA`

### Zusätzliche Fast-Swing-Screenshot-Coins
`IO, RAY, S, ATH, SUSHI, APE, FARTCOIN, ZETA, SEI`

`MOODENG` war ebenfalls im Screenshot, steht aber bereits in der 3x-Top-10 und wird nicht doppelt geführt.

### Wiederaufgenommene 21 Coins
`HYPE, AVAX, NEAR, ONDO, DOT, WLD, MORPHO, FIL, PENGU, ETHFI, TIA, LDO, PYTH, JTO, FLOKI, ZKSYNC, KMNO, ETH, SOL, XLM, RENDER`

Diese 21 Coins werden verbindlich unter `coin_selection.required_active` geprüft. Die Top-10-Grenze dient nur noch als Startpriorität, nicht mehr als harte Poolgrenze.

## LCW-Identifier

Mehrdeutige Coins verwenden feste, bereits aus der vorherigen Projektfassung übernommene LCW-Codes:

- `MEGA` → `__________________MEGA`
- `MON` → `_________MON`
- `XPL` → `_XPL`
- `TRUMP` → `_______________________________TRUMP`
- `PEPE` → `____PEPE`
- `BONK` → `__BONK`
- `SUI` → `_SUI`
- `TAO` → `____TAO`
- `IO` → `_IO`
- `ATH` → `____________ATH`
- `BIO` → `__BIO`
- `PNUT` → `_PNUT`
- `ZETA` → `_ZETA`
- `HYPE` → `______HYPE`
- `WLD` → `__WLD`
- `MORPHO` → `_MORPHO`
- `PENGU` → `_____PENGU`
- `ETHFI` → `_ETHFI`
- `TIA` → `_TIA`
- `FLOKI` → `_FLOKI`
- `ZKSYNC` → `ZK`

Alle Discord-Aliase sind feste, eindeutige Drei-Zeichen-Namen.

## Flash-Score mit zwei Ausschlägen

Der erste Discord-Kreis und die Top-8-Auswahl beruhen auf einer **signierten Volumen-/Kursschere**, primär über 30 Minuten und bestätigt durch 10 und 60 Minuten:

- **positiv:** Der Volumentrend ragt zunehmend über den Kurstrend, während der Kurs mindestens halbwegs stabil ist → `🔵`, `🟢`, `🟣`
- **negativ:** Der Volumentrend bleibt zunehmend unter dem Kurstrend → `🟠`, `🔴`
- **fallendes Messer:** Fallender Kurs plus steigendes Volumen gilt als bestätigter Verkaufsdruck und niemals als Akkumulation
- kleine beziehungsweise widersprüchliche Scheren bleiben `🟡`

Schwellen: positiv 24/50/76 Punkte; negativ 24/68 Punkte. Die Farbe beschreibt die Richtung, die Zahl die Stärke. Positive und negative Ausschläge konkurrieren gemeinsam um die acht sichtbaren Plätze.

## Unlock-Abzüge

Unlock-Risiken verändern **nur die Ranglistenpriorität**, niemals die tatsächlich beobachtete Signalfarbe. Ein sehr starker Marktimpuls bleibt also sichtbar, wird bei naher Verwässerung aber vorsichtiger einsortiert.

Der Abzug besteht aus:

- einem datumsabhängigen Anteil bei nahen Cliff-Ereignissen,
- einem begrenzten strukturellen Anteil bei laufender linearer Freigabe oder Inflation,
- einem Nachlauf unmittelbar nach dem Ereignis.

### Nahe oder konkrete Ereignisse

- `W` – 24.07.2026, Core Contributors
- `XPL` – 25.07.2026, Ecosystem and Growth
- `KMNO` – 30.07.2026, Core Contributors
- `SUI` und `ZETA` – 01.08.2026
- `ENA` – 02.08.2026
- `HYPE` – 06.08.2026, Core Contributors
- `AVAX` – 10.08.2026, Foundation
- `ARB` – 16.08.2026
- `ZKSYNC` – 17.08.2026, Investors
- `KAITO` – 20.08.2026
- `OP` – 11.10.2026
- `MON` – 24.11.2026
- `ONDO` – 18.01.2027
- `PYTH` – 19.05.2027

### Strukturelle Abzüge ohne nahen Cliff

`ATH, WLD, PENGU, TIA, MORPHO, RENDER`

JTO, LDO und ETHFI erhalten derzeit keinen relevanten Cliff-Abzug, weil ihre klassischen Vesting-Zeitpläne laut geprüfter Quelle weitgehend beziehungsweise vollständig abgeschlossen sind.

Alle Quellen, Daten, Empfänger und Basisabzüge stehen maschinenlesbar in `config.json` unter `unlock_risk.events`. Datenstand: 22.07.2026.

## Credits und Caches

- Vollpoolprüfung aller 60 Altcoins: eine gemeinsame LCW-Map-Abfrage
- Detailziel: 24 Altcoins
- harter Detaildeckel: 25 Altcoins
- Top-Ausgabe: BTC plus acht Altcoins
- neue Coins können durch den Vollpool-Flash sofort in die Detailauswahl steigen
- Flash-Cache bleibt mit der bisherigen v3.3.2 sowie v3.3.1/v3.3.0/v3.2.9 kompatibel
- Tagescache übernimmt vorhandene Rohhistorien; nur neu hinzugefügte Coins benötigen einen einmaligen Langzeitaufbau

Der größere Pool führt nicht automatisch zu 60 teuren Kurzzeithistorien. Der gemeinsame Map-Snapshot bewertet alle Coins günstig; nur die auffälligsten Kandidaten erhalten Detailanfragen.

## Wichtige Grenze

Syntax, Konfiguration, Formeln, Cache-Migration und simulierte Signale können lokal geprüft werden. Die endgültige Auflösung aller LCW-Identifier und die reale Datenverfügbarkeit lassen sich ohne privaten LCW-Key erst im ersten GitHub-Produktionslauf bestätigen.
