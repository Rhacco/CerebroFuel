<!-- r4 -->
# Crypto Signal Monitor v6.1.0

## Anzeige
Oben: drei aktuell aktivste Altcoins, Short → Long. Direkt am Coin bleiben nur hochrelevante Sonderlagen (Unlock, Security/Network/Schock, Supply/ETF). Rechts BTC kompakt als `Pxx` + Farbe + vierstelliger Kursrest bzw. Makro-/ETF-Hinweis.

Unten: BTC fest zuerst, danach die stärksten Aktionskandidaten Short → Long. Einheitlicher Kern:
`Druck · 05/20/60 · ER±xx · AGEyy · COIN±EXT`
Erst danach dürfen Coin-News/Warnungen abweichen.

- `ER-99 … ER-00 … ER+99`: aktuelle Pfadstruktur aus 30m/2h/5h; nur tatsächlich relevante Range zählt. Stark negativ = schnelle beidseitige Sprünge, um 00 = gemischt/ruhiger, stark positiv = effizient laufende Bewegung. Das Vorzeichen ist **keine** Long/Short-Richtung.
- `AGE00 … AGE99`: Beständigkeit derselben Struktur über 2h → 5h → 2d → 3d → 7d → 14d → 30d. Lange Stufen zählen nur, wenn auch rollende Teilfenster zur aktuellen Lage passen.
- `COIN+47/-47`: Extremity wie bisher; `Pxx`: BTC-Pinning an 1.000-$-Marken. P wird relativ zum 1.000-$-Abstand kontinuierlich aus aktueller/historischer Nähe, Verweildauer und Rückkehr berechnet; kein preisabhängiger Hartfilter. `P??` = Pin-Daten nicht belastbar; `P00` = gültig praktisch keine Bindung.

Speed/Activity, Two-Sided, 7/14/30D-Regime, Orderbuch- und Qualitätswerte bleiben intern für Auswahl/Risiko aktiv.

## Paper
NEAR eröffnet nur einen kleinen Scout, TRY baut in gleicher Richtung zügig aus und reduziert ein Gegensignal einmalig, NOW übernimmt volle Bestätigung bzw. Schließen/Reverse. ER/AGE bestimmen **nicht** die Handelsrichtung: sprunghafte Phasen werden kleiner/kürzer mit schnelleren Zielen geführt; ein alter, sauber laufender und richtungsgleicher Pfad darf länger laufen. Ein sauber laufender Pfad gegen die Position reduziert Größe/Hebel/Haltedauer.
