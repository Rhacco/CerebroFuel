<!-- r2 -->
# Crypto Signal Monitor v5.6.0

## Pool
`BTC SOL HYPE ENA PUMP ADA AVAX APT NEAR JUP ONDO TIA DOGE XRP`

## Kernlogik
Ein Altcoin wird als aktueller Bounce-/Comeback-Kandidat nur angezeigt, wenn **alles gleichzeitig** passt:

- `|EXT| >= 30`
- echte aktuelle 1m-Geschwindigkeit: Median der letzten 12 Minuten mindestens `0,025%/min` und Speed-Score `>=45`
- Live-Aktivität `>=45` aus 5m Quote-Volumen/OI + Volumenpuls + Tape-Qualität
- nennenswerte 1m-Bewegung in **beide Richtungen**, mindestens 2 Bewegungen je Seite und Two-Sided `>=20`
- kombinierter Bounce-Score `>=52`
- Tape `>=45` und Lighter-Maximalhebel `>=5x`; Spread/Liquidität bleiben Warnungen statt zusätzlicher Anzeige-Blocker

Die erwartete Bounce-Richtung ist bewusst **gegen die Extremity**: `+EXT` = oben gestreckt → Short-Bounce-Kandidat; `-EXT` = unten gestreckt → Long-Bounce-Kandidat. Extremity allein reicht nie.

## Discord
**Zeile 1:** bis zu vier streng qualifizierte Altcoins, Farbe = Extremity-Zone. Normale News umgehen den Speed-/Aktivitätsfilter nicht. Eine akute/hochprioritäre Coin-Warnung kann separat als kompakte `⚠COIN CODE`-Zeile erscheinen.

**Zeile 2:** BTC ist fest verankert.

**Darunter:** alle weiteren qualifizierten Altcoins.

Format:
`Druck 5/20/60 COIN±EXT [News/Warnung] Sxx Axx [Pxx] [NEARn|TRYn|NOWn]`

Beispiel:
`🟡▷ 5🟡20🟡60🟢 BTC+32 64,989 S07 A68 P84`

- `Sxx`: robuste aktuelle Geschwindigkeit in Basispunkten pro Minute
- `Axx`: aktuelle Lighter-Aktivität `0–99`
- `Pxx`: nur BTC, Pinning `0–99` an einer **aktuell noch nahen** runden Preiszone
- `NEAR/TRY/NOW + Zähler`: nur bei passender Bounce-Richtung und nur solange die Zeilenbreite reicht

Readiness-Zahl sowie `E/T/W/W?` werden nicht mehr angezeigt; die bisherigen Setup-, Regime-, BTC-Kontext- und Transition-Sicherungen bleiben intern erhalten.

## News & Warnungen
Die vorhandene Coin- und BTC-Ereignislogik bleibt aktiv, inklusive Coin-Upgrades/Governance/Supply/Unlocks, Security/Network/Shock, ETF/Regulierung sowie vollständigem BTC-US-Makro-Kontext (u. a. Fed/FOMC, CPI/PPI/PCE, NFP/ADP/JOLTS/Claims/ECI, GDP, Produktivität, Handel, Konsum, Industrie, Immobilien, ISM und Vertrauen).

Bestehende Qualitätswarnungen bleiben erhalten: `V! L! K! B! R! RS! CH! F!` sowie `DATA! STALE! GAP! BOOK! CND!`.

## BTC Pin
`Pxx` misst nicht bloß geringe Volatilität. Gewertet werden Verweildauer an der runden Zone, schnelle Rückkehr nach Ausbrüchen, historische Nähe und **aktuelle Nähe**. Ein alter 65k-Pin kann deshalb nicht weiter hoch ranken, wenn BTC inzwischen deutlich davon weggeflogen ist.

## Paper Trading
Paper Trading verwendet einen eigenen v5.6.0-State. Neue Positionen und Nachkäufe benötigen zusätzlich das Swing-Gate und müssen zur erwarteten Bounce-Richtung passen.

- Start: `$100`
- mindestens `10x`, sofern Markt/Risiko es zulassen
- max. Margin je Position: `18%`
- max. gesamte Paper-Margin: `55%`
- max. gesamtes technisches Risiko: `18%`
- starke gegengerichtete Bounce-Lage darf bestehende Paper-Positionen früh reduzieren/schließen

## Daten
SPD/ACT/PIN verwenden die bereits geladenen Lighter-1m-Candles, Quote-Volumen, Open Interest, Tape-/Orderbook-Qualität und Extremity; dafür entstehen keine zusätzlichen Lighter-Abfragen pro Coin.
