# Crypto Signal Monitor v3.9.2

Lighter-Monitor für `UNI, HYPE, SOL, ETH, BTC` mit frühen Swing-Signalen, bestätigten Ereignissen und mehrstufiger Marktdehnung.

## Zeile 1

Feste Reihenfolge `UNI HYP SOL ETH BTC`. Farbe = Gesamtdehnung aus 5/20/60 Minuten, 60m-VWAP, 180m-Spanne, 1/3/7 Tagen und Funding: `🔴` stark aufwärts überdehnt · `🟠` erhöht · `🟡` neutral · `🔵` abwärts überdehnt · `🟢` stark abwärts überdehnt · `⚫` Daten fehlen. Keine Trade-Richtung.

## Detailzeilen

Pfeil = Trade-Richtung. `NEAR` sauberer Aufbau vor dem Auslöser, noch warten · `TRY` erste belastbare Bestätigung, nur kleine Probe; bei `E` meist der erste saubere Grenztest · `NOW` frisches bestätigtes Sofortfenster, lila · `WAIT` kein Einstieg. `CH!` erzwingt immer `WAIT`.

`E` Expansion nach Kompression · `T` kurzer Dip/Bounce im fortbestehenden Aktivitätstrend · `W?` Wendeversuch ohne vollständigen Reclaim · `W` bestätigte Wende · `a0/a1/...` Alter in abgeschlossenen Minuten.

`OB/OS` Gesamtüberdehnung · `X` neutral · `RS!` relative Marktteilnahme widerspricht · `V!` Tape/Volumen · `L!` Liquidität/OI · `K!` Kosten · `B!` BTC-Kontext · `R!` 7/14/30D-Regime · `F!` Funding · `DATA!/CND!/BOOK!/GAP!/STALE!` Datenfehler. Readiness ist keine Trefferwahrscheinlichkeit.

## Ereignisse

`FED` Federal Reserve/FOMC · `CPI` Consumer Price Index · `NFP` Nonfarm Payrolls · `PPI` Producer Price Index · `GDP` Gross Domestic Product · `PCE` Personal Consumption Expenditures · `EXP` Options Expiry · `ETF` ETF-Entscheidung · `U` Token Unlock · `UPG` Protocol Upgrade · `MNT` Scheduled Maintenance · `GOV` Governance · `NET` Network Incident · `SUP` Supply Event · `N` bestätigte News. Ereignisse erzeugen nie selbst Long/Short.
