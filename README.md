<!-- r5 -->
# Crypto Signal Monitor v6.1.0

## Anzeige
Oben: drei aktuell aktivste Altcoins, Short → Long. Direkt am Coin bleiben nur hochrelevante Sonderlagen (Unlock, Security/Network/Schock, Supply/ETF). Rechts BTC kompakt als `Pxx` + Farbe + vierstelliger Kursrest bzw. Makro-/ETF-Hinweis.

Unten: BTC fest zuerst, danach die stärksten Aktionskandidaten Short → Long. Einheitlicher Kern:
`Druck · 05/20/60 · ER±xx · AGEyy · COIN±EXT`
Erst danach dürfen Coin-News/Warnungen abweichen.

- `ER-99 … ER-00 … ER+99`: aktuelle Pfadstruktur aus 30m/2h/5h; nur tatsächlich relevante Range zählt. Stark negativ = schnelle beidseitige Sprünge, um 00 = gemischt/ruhiger, stark positiv = effizient laufende Bewegung. Das Vorzeichen ist **keine** Long/Short-Richtung.
- `AGE00 … AGE99`: Beständigkeit derselben Struktur über 2h → 5h → 2d → 3d → 7d → 14d → 30d. Lange Stufen zählen nur, wenn auch rollende Teilfenster zur aktuellen Lage passen.
- `COIN+47/-47`: Extremity; `Pxx`: BTC-Pinning an 1.000-$-Marken. P wird relativ zum 1.000-$-Abstand kontinuierlich aus aktueller/historischer Nähe, Verweildauer und Rückkehr berechnet. `P??` = Pin-Daten nicht belastbar; `P00` = gültig praktisch keine Bindung.

Speed/Activity, Two-Sided, 7/14/30D-Regime, Orderbuch- und Qualitätswerte bleiben intern für Auswahl/Risiko aktiv. Tageskontext verwendet ausschließlich abgeschlossene Tageskerzen mit dem tatsächlichen Bucket-Ende; Minutenfenster werden bei Datenlücken nicht künstlich verlängert.

## Signal und Ausführung
Long/Short entsteht nur aus Preisstruktur der E/T/W-Setups. Quote-Volumen bestätigt Stärke, erzeugt aber keine Richtung. Die Freigabe berücksichtigt zusätzlich Tape-Qualität, OI/Liquidität, BTC-/Regime-Risiko, Funding und die für die Richtung tatsächlich benötigte Orderbuchseite.

- Long-Kosten: Einstieg über Asks, modellierter Ausstieg über Bids.
- Short-Kosten: Einstieg über Bids, modellierter Rückkauf über Asks.
- Fehlende Tiefe oder fehlendes Lighter-Funding blockiert einen neuen Trade.
- `/funding-rates` wird als 8h-äquivalenter Vergleichswert eingelesen und vor Signal/Paper auf einen Stundenwert normalisiert.
- REST-Aufrufe teilen sich ein gemeinsames Rolling-Minute-Budget und einen gemeinsamen 405/429-Cooldown.

## Paper
NEAR eröffnet nur einen kleinen Scout, TRY baut in gleicher Richtung zügig aus und reduziert ein Gegensignal einmalig, NOW übernimmt volle Bestätigung bzw. Schließen/Reverse. ER/AGE bestimmen **nicht** die Handelsrichtung: sprunghafte Phasen werden kleiner/kürzer mit schnelleren Zielen geführt; ein alter, sauber laufender und richtungsgleicher Pfad darf länger laufen. Ein sauber laufender Pfad gegen die Position reduziert Größe/Hebel/Haltedauer.

Fehlende aktuelle Marks werden nicht als Null-P/L behandelt, sondern mit dem letzten bekannten Mark ausdrücklich als stale bewertet. Funding-Lücken werden nur für höchstens ein Stundenintervall mit einer beobachteten bzw. letzten bekannten Stundenrate modelliert; ältere Laufunterbrechungen bleiben ausdrücklich als unbekannte Stunden markiert. Die maximale Setup-Haltedauer gilt auch bei fehlenden Signaldaten und wiederholter identischer Entscheidungskerze.

Alte r2/r3/r4-Paper-States bleiben lesbar. Vor-r5-Fundingbeträge werden einmalig vom früher falsch als stündlich behandelten 8h-Wert korrigiert. Alte abgeleitete Entry-Features werden danach nicht als neue Optimizer-Evidenz verwendet; neue Optimizer-Hinweise bleiben vergleichende/heuristische Hinweise und ändern keine Parameter automatisch.

## Events / Betrieb
Der Cloudflare Worker speichert den frisch erzeugten Event-Feed vor dem GitHub-Dispatch und übergibt denselben Snapshot zusätzlich direkt als Workflow-Input. Kleine Feeds bleiben JSON, große Feeds werden gzip/base64 übertragen. Damit hängt der ausgelöste Lauf nicht von Cache-/KV-Replikationszeit ab. `/refresh` ist nur mit gesetztem `REFRESH_TOKEN` und passendem Bearer-Token aktiv.

Workflow-Concurrency bleibt revisionsunabhängig auf v6.1.0; Runtime-/Paper-State kann kontrolliert aus r2/r3/r4 übernommen werden. Der Daily-Candle-Cache wird wegen der korrigierten Zeitsemantik neu aufgebaut.
