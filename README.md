# Crypto Signal Monitor v4.1.0

## Anzeige

**Kopfzeile:** drei auffälligste Altcoins + **BTC immer ganz rechts**. Der Kreis hinter dem Coin zeigt die aktuelle Überdehnung; Warnungen und Ereignisse stehen direkt dahinter. BTC trägt die stündliche Makroanzeige.

**Detailzeilen:** stärkste NOW-, TRY- und NEAR-Signale sowie BTC, nach Signalstärke sortiert. Aufbau:

`Druck  5m 20m 60m  Coin+Readiness  Aktion  Extremität  Setup`

## Druckkreis

- `🟣▲/▼` NOW freigegeben
- `🟢▲` starker Long-Druck
- `🔵▲` leichter Long-Druck
- `🟡►` beruhigt / seitwärts
- `🟠▼` leichter Short-Druck
- `🔴▼` starker Short-Druck
- `⚫?` unzureichende Daten

Die Kreise bei `5/20/60` zeigen den Druck je Zeitfenster: `🟢` long, `🟡` neutral, `🔴` short, `🟤` unvollständig.

## Aktionen und Setups

- `NOW▲/▼` direkte Trading-Freigabe
- `TRY▲/▼` mutiger Probe-Einstieg
- `NEAR` starkes Signal kurz vor TRY/NOW
- `WAIT` keine Freigabe
- `E` früher Impuls
- `T` Trendfortsetzung
- `W` bestätigte Umkehr, `W?` noch unvollständig
- `a0–a9` Alter des Setups in Minuten

## Extremität

- `OB` überkauft / nach oben überdehnt
- `OS` überverkauft / nach unten überdehnt
- `X` neutraler Bereich

Kopfzeile: `🔴` stark überkauft, `🟠` erhöht, `🟡` neutral, `🔵` erhöht überverkauft, `🟢` stark überverkauft, `⚫` nicht verfügbar. Diese Farbe beschreibt Überdehnung, nicht die Handelsrichtung.

## Warnungen

- `SEC!` bestätigtes Sicherheitsereignis
- `NET!` bestätigte Netzwerkstörung
- `SHK!` außergewöhnlicher Marktschock
- `V!` schwache Volumen-/Tape-Bestätigung
- `L!` geringe Liquidität
- `K!` ungünstige Handelskosten
- `B!` schwacher BTC-Kontext
- `R!` gegensätzliches Marktregime
- `RS!` schwache relative Stärke bei Umkehr
- `CH!` Signal bereits zu weit gelaufen
- `F!` ungünstiges oder fehlendes Funding
- `DATA!`, `STALE!`, `GAP!`, `BOOK!`, `CND!` Datenproblem

## Ereignisse

`FED` FOMC · `CPI` Verbraucherpreise · `NFP` US-Arbeitsmarkt · `PPI` Erzeugerpreise · `GDP` BIP · `PCE` PCE-Inflation · `EXP` großer Optionsverfall · `ETF` ETF-Ereignis · `U` Token-Unlock · `UPG` Upgrade · `MNT` Wartung · `GOV` Governance · `SUP` Angebot/Supply · `N` Nachricht
