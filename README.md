<!-- r1 -->
# Crypto Signal Monitor v5.7.0

## Pool
`BTC SOL HYPE ENA PUMP ADA AVAX APT NEAR JUP ONDO TIA DOGE XRP`

## Discord-Logik
**Zeile 1 = PRE/NEXT-Radar.** Ein Altcoin erscheint dort nur, wenn er bereits entscheidend gestreckt, schnell und auf Lighter aktiv ist, der jüngste Druck aber noch **zum Extrem** läuft. Ein bereits bestätigter Bounce verschwindet aus PRE und wandert nach unten.

PRE-Gate:
- `|EXT| >= 30`
- echte 1m-Speed: Median der letzten 12m mindestens `0,025%/min`, Speed-Score `>=45`
- Live-Aktivität `>=45` aus 5m Quote-Volumen/OI + Volumenpuls + Tape
- jüngster 5m-Druck weiterhin zum Extrem, Extension-Score `>=52`; normalerweise mindestens zwei relevante gleichgerichtete 1m-Moves (ein außergewöhnlich großer Einzelimpuls darf früher reichen)
- Tape `>=45`, Lighter-Maximalhebel `>=5x`

**Zeile 2 = BTC fest.**

**Darunter = bestätigte Bounce-/Comeback-Kandidaten.** Zusätzlich zum schnellen/aktiven Extrem müssen mindestens zwei relevante 1m-Bewegungen je Richtung, Two-Sided `>=20` und Bounce-Score `>=52` vorliegen. `+EXT` bedeutet OB → erwarteter Short-Bounce; `-EXT` bedeutet OS → erwarteter Long-Bounce.

## Einheitliche Detailzeilen
Der Kern ist bei BTC und jedem Altcoin identisch aufgebaut:

`Druck 05/20/60 COIN±EXT Sxx Axx |Spezial`

Beispiel:
`🟡▷ 05🟡20🟡60🟢 BTC+32 S07 A68 |64,989 P84`
`🔴▼ 05🔴20🟠60🟡 ENA+47 S18 A82 |UPG NOW2`

Bis einschließlich `Axx` bleibt Reihenfolge und Breite möglichst konstant. Nur rechts vom `|` dürfen coin-spezifische Informationen abweichen:
- News/Warnungen
- BTC-Preis und `Pxx`
- `NEAR/TRY/NOW + Zähler`, nur bei passender Bounce-Richtung und wenn Platz bleibt

`Sxx` = robuste aktuelle Geschwindigkeit in Basispunkten pro Minute.  
`Axx` = aktuelle Lighter-Aktivität `0–99`.  
`Pxx` = BTC-Pinning `0–99` an einer aktuell noch nahen runden Preiszone.

## News & Warnungen
Coin- und BTC-Ereignislogik bleibt aktiv, inklusive Coin-Upgrades/Governance/Supply/Unlocks, Security/Network/Shock, ETF/Regulierung und relevantem US-Makro-Kontext. Akute wichtige Events bleiben sichtbar, auch wenn der Coin weder PRE noch bestätigter Bounce ist.

Qualitätswarnungen bleiben erhalten: `V! L! K! B! R! RS! CH! F!` sowie `DATA! STALE! GAP! BOOK! CND!`.

## Paper Trading
Paper Trading verwendet einen **eigenen v5.7.0-State (Schema 3)**. PRE allein eröffnet keine Position. Neue Positionen/Nachkäufe benötigen weiterhin das bestätigte Bounce-Gate und passende Richtung.

- Start `$100`
- Paper-Hebel standardmäßig mindestens `10x`, soweit Markt/Risiko es zulassen
- max. Margin je Position `18%`
- max. gesamte Paper-Margin `55%`
- max. gesamtes technisches Risiko `18%`

## Daten
PRE/SPD/ACT/Two-Sided/PIN verwenden die ohnehin geladenen Lighter-1m-Candles, Quote-Volumen, Open Interest sowie Tape-/Orderbook-Kontext. Es entstehen dadurch keine zusätzlichen Lighter-Abfragen pro Coin.
