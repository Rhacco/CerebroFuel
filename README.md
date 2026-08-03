# Crypto Signal Monitor v4.2.0

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
- `TRY▲2` / `TRY▼2` mutiger Probe-Einstieg, seit 2 Minuten unverändert
- `NEAR3` starkes Vor-Signal, seit 3 Minuten in gleicher Richtung
- `WAIT` keine Freigabe

Der Zähler steigt nur bei aufeinanderfolgenden abgeschlossenen Minuten mit **gleicher Aktion und Richtung**. Wechsel, Lücke, WAIT oder Datenfehler starten beim nächsten Signal wieder mit `1`.

Intern werden alle NEAR/TRY/NOW-Signale nach `3/5/10/20` Minuten ausgewertet: Kursweg, maximaler Gewinn-/Gegenlauf, Beständigkeit, Hochstufung und Richtungswechsel – getrennt nach Coin, E/T/W und Long/Short.

## Kontext

- `E` früher Impuls · `T` Trendfortsetzung · `W` bestätigte Umkehr · `W?` unvollständig
- `a0–a9` Setup-Alter
- `OB` überkauft · `OS` überverkauft · `X` neutral

Überdehnung in der Kopfzeile: `🔴` stark oben · `🟠` erhöht oben · `🟡` neutral · `🔵` erhöht unten · `🟢` stark unten · `⚫` unbekannt.

## Warnungen

`SEC!` Sicherheit · `NET!` Netzwerk · `SHK!` Marktschock · `V!` Volumen/Tape · `L!` Liquidität · `K!` Kosten · `B!` BTC-Kontext · `R!` Regime · `RS!` relative Umkehrstärke · `CH!` zu weit gelaufen · `F!` Funding · `DATA!/STALE!/GAP!/BOOK!/CND!` Datenproblem.

## Ereignisse

`FED` FOMC · `CPI` Verbraucherpreise · `NFP` Arbeitsmarkt · `PPI` Erzeugerpreise · `GDP` BIP · `PCE` Inflation · `EXP` Optionsverfall · `ETF` ETF · `U` Unlock · `UPG` Upgrade · `MNT` Wartung · `GOV` Governance · `SUP` Supply · `N` Nachricht.

**Paper:** bewusst sehr aggressives Experimentprofil; kann bereits NEAR handeln, nutzt größere Margin, mehr Positionen und höhere Hebel. Die sichtbare Signalbewertung bleibt unverändert.
