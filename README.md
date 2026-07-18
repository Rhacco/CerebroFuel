# Krypto-Monitor v3.3.1 – Ranked Volume Priority

`Kreis Zahl Richtung 7 B24 B7 P V10/V30/V60 N Tage Coin/Minute`

## Verbindliche Bestandteile

`MEGA`, `PUMP`, `JTO` und `ZKSYNC` bleiben zwingend im aktiven Pool. Der Programmstart prüft zusätzlich alle direkt ausgewählten Ranglisten-Coins; fehlt einer davon in `config.json`, wird der Lauf vor API-Abfragen und vor einem Discord-Versand abgebrochen.

## Neue 5x-/10x-Coins – Rangliste

| Rang | Coin | Hebel | Entscheidung |
|---:|---|---:|---|
| 1 | Litecoin (`LTC`) | 10x | direkt aktiv |
| 2 | Chainlink (`LINK`) | 10x | direkt aktiv |
| 3 | Avalanche (`AVAX`) | 5x | direkt aktiv |
| 4 | Pudgy Penguins (`PENGU`) | 5x | direkt aktiv, Ereignisabzug |
| 5 | Ethena (`ENA`) | 5x | direkt aktiv, starker Unlock-Abzug |
| 6 | Arbitrum (`ARB`) | 5x | direkt aktiv, Unlock-Filter |
| 7 | Aptos (`APT`) | 5x | direkt aktiv, Unlock-Filter |
| 8 | Filecoin (`FIL`) | 5x | direkt aktiv |
| 9 | FLOKI (`FLOKI`) | 5x | direkt aktiv, volatiler Kandidat |
| 10 | Algorand (`ALGO`) | 5x | direkt aktiv, testweise |
| 11 | Tron (`TRX`) | 5x | direkt aktiv, ruhiger 24/7-Vergleich |
| 12 | Shiba Inu (`SHIB`) | 5x | Reserve, nicht im aktiven Pool |
| 13 | Ethereum Classic (`ETC`) | 5x | Reserve, nicht im aktiven Pool |
| 14 | Conflux (`CFX`) | 5x | nicht aufnehmen |
| 15 | XDC Network (`XDC`) | 5x | nicht aufnehmen |

Die elf direkt empfohlenen 5x-/10x-Coins sind vollständig in `groups/ranked_5x_10x` eingetragen. Die gesamte Reihenfolge bleibt zusätzlich maschinenlesbar unter `coin_selection.new_5x_10x_ranking` erhalten.

## Strenge 3x-Auswahl

Direkt aktiv sind `VIRTUAL`, `ORDI`, `PENDLE`, `RAY` und `SUSHI`. Die schwächeren bisherigen Zwischenkandidaten `JASMY`, `ZEC` und `BOME` wurden nicht als Detailkandidaten übernommen. Beobachtungskandidaten bleiben in `coin_selection.observe_only_3x` dokumentiert, verbrauchen aber keine zusätzlichen regulären Detailplätze.

## LCW-Code-Sicherheit

Mehrdeutige Symbole verwenden feste geprüfte Primärcodes:

- `MEGA` → `__________________MEGA`
- `PENGU` → `_____PENGU`
- `FLOKI` → `_FLOKI`
- `ZKSYNC` → `ZK`
- bestehende Sondercodes für `SUI`, `TAO`, `PEPE`, `BONK`, `ETHFI`, `TIA`, `MORPHO`, `TRUMP`, `WLD`, `HYPE` und `XPL` bleiben erhalten.

Die Discord-Aliase bleiben eindeutig und lesbar mit drei Zeichen; unter anderem `W` → `WRM`, `OP` → `OPT`, `ZKSYNC` → `ZKS`, `JTO` → `JTO`.

Es werden für diese mehrdeutigen Coins keine unsicheren Standard-Fallbacks verwendet. Ein nicht aufgelöster Coin wird protokolliert, statt versehentlich ein gleichnamiges Asset auszuwerten.

## Signalprioritäten

1. Wichtigster Faktor bleibt die richtungsbewusste 30-Minuten-Volumen-/Kursschere. Stabiler Kurs plus steigendes Volumen ist positiv; fallender Kurs plus steigendes Volumen ist negative Verkaufsbestätigung.
2. `V10/V30/V60` zeigen den Trend des rollierenden LCW-24h-Volumens.
3. `7` ergänzt den Sieben-Tage-Volumentrend.
4. Kleine Market Cap und hohe Volatilität bleiben begrenzte Bonusfaktoren.
5. `N` zeigt Crash-Stabilisierung beziehungsweise einen weiterlaufenden Crash.
6. `B24 B7` vergleichen die Kursperformance mit BTC über 24 Stunden und sieben Tage.

## Vollpool, Details und Credits

Alle aktiven Altcoins werden mit einer gemeinsamen LCW-Map-Abfrage und den gespeicherten Fünf-Minuten-Snapshots geprüft. Die Erweiterung erhöht daher die reguläre Zahl teurer Kurzzeithistorien nicht:

- Detailziel: 24 Altcoins
- harter Detaildeckel: 25 Altcoins
- regulär: `1 Map + BTC + 24 Coin-Historien ≈ 26 Requests`
- maximal ohne zusätzlichen BTC-Retry: `1 Map + BTC + 25 Coin-Historien ≈ 27 Requests`

Nur die nach Vollpool-Snapshot, 30-Minuten-Schere und begrenzten Zusatzfaktoren stärksten Kandidaten erhalten eine Detailhistorie. Neue Coins benötigen einmalig Tageshistorie und anschließend 10/30/60 Minuten Flash-Aufwärmung.

## Cache-Migration und Versand-Sicherheit

Die Workflow-Restore-Reihenfolge übernimmt weiterhin `seasonality-v330-`, `flash-v330-` und `flash-v329-`. Vorhandene Rohhistorien und Snapshot-Punkte werden wiederverwendet; fehlende neue Coins werden gezielt ergänzt. Der Tageskontext wird mit der Revision `complete-weeks-pool-neutral-r2-ranked` vollständig berechnet und gespeichert, bevor Discord ausgeführt wird.

Cloudflare-Zeitplan, Secrets und Discord-Format bleiben unverändert.
