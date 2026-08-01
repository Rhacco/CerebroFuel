# Crypto Signal Monitor v3.9.1

Lighter-Monitor für `UNI, HYPE, SOL, ETH, BTC` mit frühen Swing-Signalen, bestätigten Ereignissen und mehrstufigem Extremzustand.

## Zeile 1: Marktdehnung

Feste Reihenfolge `UNI HYP SOL ETH BTC`. Die Farbe kombiniert 5/20/60-Minuten-Überdehnung, 60m-VWAP, 180m-Spanne, 1/3/7-Tage-Bewegung und Funding: `🔴` stark aufwärts überdehnt · `🟠` erhöht · `🟡` neutral · `🔵` abwärts überdehnt · `🟢` stark abwärts überdehnt · `⚫` Daten fehlen. Sie zeigt keine Trade-Richtung und keine Wendewahrscheinlichkeit.

## Detailzeilen

Pfeil = Trade-Richtung. `NOW` exakt bestätigtes Sofortfenster (lila) · `TRY` kleiner Probe-Einsatz vertretbar, weitere Bestätigung abwarten · `NEAR` Chance nähert sich, noch warten · `WAIT` kein Einstieg. `E` Expansion nach Kompression · `T` kurzer Dip/Bounce im fortbestehenden Aktivitätstrend · `W?` Wendeversuch ohne vollständigen Reclaim · `W` strukturell bestätigte Wende · `a0/a1/...` Alter in abgeschlossenen Minuten.

`OB/OS` Gesamtüberdehnung · `X` neutral · `CH!` Bewegung bereits weit gelaufen · `RS!` relative Marktteilnahme widerspricht · `V!` Tape/Volumen · `L!` Liquidität/OI · `K!` Kosten · `B!` BTC-Kontext · `R!` 7/14/30D-Regime · `F!` Funding · `DATA!/CND!/BOOK!/GAP!/STALE!` Datenfehler. Readiness ist keine Trefferwahrscheinlichkeit.

## Ereignisse

`FED` Federal Reserve/FOMC · `CPI` Consumer Price Index · `NFP` Nonfarm Payrolls · `PPI` Producer Price Index · `GDP` Gross Domestic Product · `PCE` Personal Consumption Expenditures · `EXP` Options Expiry · `ETF` ETF-Entscheidung · `U` Token Unlock · `UPG` Protocol Upgrade · `MNT` Scheduled Maintenance · `GOV` Governance · `NET` Network Incident · `SUP` Supply Event · `N` bestätigte News. Heutige Termine bleiben bis zum Zeitpunkt sichtbar; spätere Termine erscheinen stündlich, Unlocks bis 14 Tage vorher. Ereignisse erzeugen nie selbst Long/Short.
