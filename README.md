# Crypto Signal Monitor v3.9.0

Lighter-Monitor für `UNI, HYPE, SOL, ETH, BTC` mit frühen Swing-Signalen, bestätigten Ereignissen und 7/14/30-Tage-Kontext.

## Zeile 1: Überdehnung

Feste Reihenfolge `UNI HYP SOL ETH BTC`. Die Farbe zeigt **keine Trade-Richtung**, sondern den aktuellen Extremzustand: `🔴` stark überkauft/ungewöhnlich aufwärts überdehnt · `🟠` erhöht · `🟡` neutral · `🔵` abwärts überdehnt · `🟢` stark überverkauft · `⚫` Daten fehlen. Das ist keine Wendewahrscheinlichkeit.

## Detailzeilen

Pfeil = Trade-Richtung. `E` frische Expansion nach Kompression · `T` kurzer Dip/Bounce im fortbestehenden Aktivitätstrend · `W?` Reversal-Versuch ohne vollständige Rückeroberung · `W` strukturell bestätigte Wende · `+/-` Richtung ohne eigenes Setup · `a0/a1/...` Alter in abgeschlossenen Minuten. Danach folgen Coin und Readiness, z. B. `UNI82 W?a1 OS64 RS!`. Readiness ist keine Trefferwahrscheinlichkeit.

`OB` aufwärts überdehnt · `OS` abwärts überdehnt · `X` neutral · `RS!` relative Marktteilnahme widerspricht · `V!` Tape/Volumen · `L!` Liquidität/OI · `K!` Kosten · `B!` BTC-Kontext · `R!` 7/14/30D-Regime · `F!` Funding · `DATA!/CND!/BOOK!/GAP!/STALE!` Datenfehler.

## Ereignisse

`FED` Federal Reserve/FOMC · `CPI` Consumer Price Index · `NFP` Nonfarm Payrolls · `PPI` Producer Price Index · `GDP` Gross Domestic Product · `PCE` Personal Consumption Expenditures · `EXP` Options Expiry · `ETF` ETF-Entscheidung · `U` Token Unlock · `UPG` Protocol Upgrade · `MNT` Scheduled Maintenance · `GOV` Governance · `NET` Network Incident · `SUP` Supply Event · `N` bestätigte News. Beispiele: `U5D`, `CPI@14`, `NET!`. Heutige Termine bleiben bis zum Zeitpunkt sichtbar; spätere Termine erscheinen stündlich, Unlocks bis 14 Tage vorher. Ereignisse erzeugen nie selbst Long/Short.
