# Crypto Signal Monitor v5.0.0

## Anzeige

**Kopfzeile:** drei auffälligste Altcoins + **BTC immer ganz rechts**. Coin-Kreis = Überdehnung; Warnungen/Ereignisse stehen direkt dahinter. BTC trägt die stündliche Makroanzeige.

**Detail:** `Druck  5m 20m 60m  Coin+Readiness  Aktion  Extremität  Setup`

## Trenddruck

- `🟣▲/▼` NOW freigegeben
- `🟢▲` starker Long-Druck · `🔵▲` leichter Long-Druck
- `🟡▷` beruhigt / seitwärts
- `🟠▼` leichter Short-Druck · `🔴▼` starker Short-Druck
- `⚫?` unzureichende Daten

Fensterkreise `5/20/60`: `🟢` long · `🟡` neutral · `🔴` short · `🟤` unvollständig.

## Aktionen

- `NOW▲3` / `NOW▼3` direkte Freigabe, seit 3 Minuten unverändert
- `TRY▲2` / `TRY▼2` Probe-Einstieg, seit 2 Minuten unverändert
- `NEAR3` starkes Vor-Signal, seit 3 Minuten in gleicher Richtung
- `WAIT` keine Freigabe

Der Zähler steigt nur bei aufeinanderfolgenden abgeschlossenen Minuten mit gleicher Aktion und Richtung. Wechsel, Lücke, WAIT oder Datenfehler starten wieder mit `1`.

## Richtungswechsel

- `W?` bleibt immer höchstens `NEAR`; TRY/NOW erst nach bestätigtem strukturellem `W`.
- Nach einem entgegengesetzten TRY/NOW braucht die neue Richtung zwei bestätigte Minuten für TRY und drei für NOW.
- Das Paper-Trading übernimmt dieselbe Sperre und handelt keine unbestätigte Gegenrichtung; sein übriges aggressives Profil bleibt bestehen.

## Kontext

- `E` früher Impuls · `T` Trendfortsetzung · `W` bestätigte Umkehr · `W?` unvollständig
- `a0–a9` Setup-Alter
- `OB` überkauft · `OS` überverkauft · `X` neutral

Überdehnung in der Kopfzeile: `🔴` stark oben · `🟠` erhöht oben · `🟡` neutral · `🔵` erhöht unten · `🟢` stark unten · `⚫` unbekannt.

## Warnungen

`SEC!` Sicherheit · `NET!` Netzwerk · `SHK!` Marktschock · `V!` Volumen/Tape · `L!` Liquidität · `K!` Kosten · `B!` BTC-Kontext · `R!` Regime · `RS!` relative Umkehrstärke · `CH!` zu weit gelaufen · `F!` Funding · `DATA!/STALE!/GAP!/BOOK!/CND!` Datenproblem.

## Ereignisse

`FED` FOMC · `CPI` Verbraucherpreise · `NFP` Arbeitsmarkt · `PPI` Erzeugerpreise · `GDP` BIP · `PCE` Inflation · `EXP` Optionsverfall · `ETF` ETF · `U` Unlock · `UPG` Upgrade · `MNT` Wartung · `GOV` Governance · `SUP` Supply · `N` Nachricht.
