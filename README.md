<!-- r2 -->
# Crypto Signal Monitor v7.0.0

## Kernanzeige
Oben stehen BTC plus die aktuell wichtigsten Radar-Coins. Beobachtungswürdige akute News/Warnungen werden möglichst direkt hinter der Coin-Farbe gezeigt. Ein bestätigter einseitiger 15m-Ausreißer wird unabhängig vom normalen Ranking mit `SHK!` nach oben gezogen und bleibt aus den unteren Trade-Zeilen, bis die Bewegung ausreichend beruhigt ist oder ein belastbarer Richtungswechsel beginnt.

Unten bleibt der gemeinsame Kern kompakt und einheitlich:
`Druck · 05/20/60 · Jxx · Exx · COIN±EXT`
Erst danach folgen die wichtigsten akuten Warnungen, die mehr als reine Beobachtung erfordern.

- `J00…J99`: richtungsfreie Springer-Stärke aus wiederkehrenden 15m-Impulsen der jüngsten zusammenhängenden Minutenhistorie plus robuster 10–30d-Range/Verlässlichkeit. Ein einzelner Ausreißer wird ausdrücklich abgewertet; Events gehören nicht in J.
- `E00…E99`: akutes Event-/Störungsrisiko aus verifizierten Ereignissen. `E??` bedeutet unzureichende aktuelle Quellenabdeckung, nicht „kein Risiko“.
- `COIN+xx/-xx`: Extremity. Überdehnung ist ein starker Anti-Chase-Faktor; E/T werden in Laufrichtung früh gebremst bzw. blockiert, während ein bestätigtes Reversal die gegengerichtete Extremity nutzen darf.
- `P00…P99`: BTC-Pinning an 1.000-$-Marken. `P??` = Daten nicht belastbar; `P00` = gültige Daten, praktisch keine aktuelle Bindung.

Poolklassen: A = natürliche Springer (`KAITO PENGU FARTCOIN LDO ZEC WLD PUMP SUI ENA`), B = Hebel-Springer (`ETH SOL HYPE XRP`), C = Event/Regime-Springer (`XPL ONDO ADA TAO JUP NEAR AVAX UNI APT`). Die Klasse erzeugt keine Richtung und ersetzt keine Live-Daten.

## Signal
Long/Short entsteht ausschließlich aus der Preisstruktur der E/T/W-Setups. Volumen, J, Events und historische Klassen dürfen bestätigen, priorisieren oder Risiko reduzieren, aber keine Richtung erfinden. Die numerischen Basisschwellen bleiben `NEAR 51 / TRY 58 / NOW 69`; NOW benötigt zusätzlich die jeweiligen präzisen Setup-, Frische-, Edge-, Daten-, Funding-, Tape-, Liquiditäts- und Richtungs-Gates. Positive Kontextfaktoren dürfen ein nicht bestätigtes Setup nicht künstlich hochstufen.

Ausführung wird richtungsspezifisch geprüft: Long steigt über Asks ein und modelliert den Ausstieg über Bids; Short umgekehrt. Fehlende Tiefe oder fehlendes Funding blockiert neue Trades. Lighter-`/funding-rates` wird als 8h-äquivalenter Vergleichswert eingelesen und vor Verwendung auf den Stundenkontext normalisiert. Tageskontext verwendet nur abgeschlossene Daily-Buckets; Minutenfenster werden bei Lücken nicht künstlich verlängert.

`SHK!` ist ein Sicherheitszustand aus echten Lighter-Preisdaten: ein ungewöhnlich großer, effizient einseitiger 15m-Move relativ zu robuster eigener Historie. Er blockiert neue Paper-/Detail-Trades, bis Retracement, Gegenbewegung oder ausreichende Beruhigung vorliegt. Ein Schock wird nicht automatisch einem Exploit oder einer News-Ursache zugeschrieben.

## Lernen / Paper
Paper bleibt bewusst explorativ: NEAR kann kleine Scouts eröffnen, TRY ausbauen, NOW stärker gewichten; mehrere Positionen und kontrollierte Scale-ins sind möglich. J beeinflusst Größe/Haltedauer nur richtungsfrei: hohe wiederkehrende Aktivität darf etwas aktiver getestet werden, extreme J-Lagen werden wegen Slippage-/Whipsaw-Risiko wieder begrenzt. E/`SHK!`, Extremity, Kosten, Datenqualität und Gegenkontext bleiben harte Sicherheitsfaktoren.

Funding wird nur an tatsächlich überschrittenen UTC-Stundengrenzen verbucht. Nicht belegbare Settlements werden als unbekannt markiert und nicht als Optimizer-Evidenz verwendet. Scale-ins erhöhen den lebenszeitlichen Risikonenner für R-Auswertungen. Der Evaluator speichert echte Signal-Episoden statt Minuten-Duplikate sowie die kontinuierlichen Startmerkmale und Promotions, damit `51/58/69` später auf denselben Rohsignalen sauber verglichen werden können.

Der Optimizer ändert **keine** Live-Parameter automatisch. Er liefert wiederholbare, datenbasierte Hinweise mit Stichprobe, Trefferquote/R-Vergleich und konkreter Prüfempfehlung; dieselbe Problemklasse kann nach neuer Evidenz erneut gemeldet werden.

## Events / Betrieb
Jeder Pool-Coin besitzt dieselbe Basissuche auf verifizierten offiziellen Domains; zusätzliche strukturierte Status-/Release-/Unlock-/Feed-Quellen werden nur verwendet, wenn sie verifiziert sind. Quellen-Gesundheit wird pro Coin intern mitgeführt; ohne bereits verifiziertes aktuelles Ereignis erscheint unzureichende Abdeckung kompakt als `E??` statt als zusätzlicher Warncode. BTC erhält zusätzlich die relevanten US-Makrotermine sowie verifizierte BTC-spezifische Risikoquellen.

Der Cloudflare Worker speichert den neuen Feed vor dem GitHub-Dispatch und übergibt exakt denselben Snapshot samt `source_health` direkt als Workflow-Input. `/refresh` ist nur mit gesetztem `REFRESH_TOKEN` und passendem Bearer-Token aktiv. Lighter-REST-Aufrufe teilen sich ein gemeinsames Rolling-Minute-Budget und 405/429-Cooldown.

v7 verwendet bewusst frische, revisionsgebundene Runtime-/Paper-Zustände; alte v6-Lernwerte werden wegen geänderter J/E-/Funding-/Episode-Semantik nicht ungeprüft weitergeführt. Reale Lighter-/GitHub-/Cloudflare-Läufe bleiben die abschließende Umgebungsvalidierung.
