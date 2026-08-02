# Crypto Signal Monitor v3.9.3

Lighter-Monitor ausschließlich für BTC und den gemeinsam ausgewählten Top-Pool.

## Märkte

Kernpool: `HYPE, ENA, AAVE, PUMP, ZEC, JUP, SUI, NEAR`.

Strenger bedingter Pool: `ONDO, 1000PEPE` (`kPEPE`). Diese beiden dürfen erst ab Readiness `80` und Confidence `76` in die Paper-Ausführung; der aktuelle Orderbuch-, OI-, Volumen- und Kostencheck bleibt immer Pflicht.

Feste Kürzel: `HYP ENA AAV PMP ZEC JUP SUI NER OND PEP BTC`.

## Erste Discord-Zeile

Alle zehn Altcoins werden bei jedem Lauf dynamisch von links nach rechts sortiert: geringe Gesamtüberdehnung zuerst, stärkste Überkauft-/Überverkauft-Ausprägung zuletzt. Maßgeblich ist der Betrag des Extremity-Scores; Readiness und Attention lösen nur Gleichstände. BTC ist von der Sortierung ausgenommen und bleibt immer ganz rechts.

Farbe = Gesamtdehnung aus 5/20/60 Minuten, 60m-VWAP, 180m-Spanne, 1/3/7 Tagen und Funding: `🔴` stark überkauft · `🟠` erhöht · `🟡` neutral · `🔵` überverkauft · `🟢` stark überverkauft · `⚫` Daten fehlen. Die Farbe allein ist keine Trade-Richtung.

BTC trägt dort die verifizierte Makro-/Heute-Anzeige. Coinspezifische Codes erscheinen am jeweiligen Coin, sobald ein verifizierter Feed oder eine eingebaute offizielle Statusquelle einen passenden Eintrag liefert; es werden keine Termine geschätzt.

## Detailzeilen

Pfeil = Trade-Richtung. `NEAR` sauberer Aufbau, noch warten · `TRY` erste belastbare Bestätigung, nur kleine Probe · `NOW` frisches bestätigtes Sofortfenster · `WAIT` kein Einstieg. `CH!` erzwingt immer `WAIT`.

`E` Expansion nach Kompression · `T` kurzer Dip/Bounce im Aktivitätstrend · `W?` Wendeversuch ohne vollständigen Reclaim · `W` bestätigte Wende · `a0/a1/...` Alter in abgeschlossenen Minuten.

`OB/OS` Gesamtüberdehnung · `X` neutral · `RS!` relative Marktteilnahme widerspricht · `V!` Tape/Volumen · `L!` Liquidität/OI · `K!` Kosten · `B!` BTC-Kontext · `R!` 7/14/30D-Regime · `F!` Funding · `DATA!/CND!/BOOK!/GAP!/STALE!` Datenfehler. Readiness ist keine Trefferwahrscheinlichkeit.

Für eine Freigabe werden bei der realen Prüfgröße von 50 USDC unter anderem mindestens 150.000 USDC Tagesvolumen, 200.000 USDC OI und höchstens 0,10 % modellierte Roundtrip-Orderbuchkosten verlangt. Die tatsächliche Paper-Positionsgröße wird vor jedem Einstieg nochmals vollständig durchs aktuelle Buch gerechnet.

## Ereignisse

`FED` Federal Reserve/FOMC · `CPI` Consumer Price Index · `NFP` Nonfarm Payrolls · `PPI` Producer Price Index · `GDP` Gross Domestic Product · `PCE` Personal Consumption Expenditures · `EXP` Options Expiry · `ETF` ETF-Entscheidung · `U` Token Unlock · `UPG` Protocol Upgrade · `MNT` Scheduled Maintenance · `GOV` Governance · `NET` Network Incident · `SUP` Supply Event · `N` bestätigte News. Ereignisse erzeugen nie selbst Long oder Short.

## Discord-Aufräumung

Ab dem ersten erfolgreichen v3.9.3-Versand wird die gespeicherte Webhook-Nachricht bei Änderungen aktualisiert, nicht neu angehäuft. Falls ein Bericht mehrere Discord-Nachrichten benötigt, werden überzählige alte Teile nach erfolgreicher Aktualisierung gelöscht. Frühere v3.9.2-Nachrichten besitzen keine gespeicherten IDs und können deshalb nicht sicher automatisch gefunden werden; sie müssen einmalig manuell entfernt werden.
