# Krypto-Monitor v3.2.9

`Kreis Zahl Richtung 7 B P V10/20/60 N Tage Coin/Minute`

- Vollpool-Flash: **jeder konfigurierte Coin** wird bei jedem Lauf aus derselben frischen LCW-Map geprüft; gespeicherte Map-Snapshots liefern P/V 10/20/60 ohne Extra-Credits.
- Die 22 auffälligsten Flash-Kandidaten werden per LCW-Historie bestätigt; maximal 24 Detailabrufe bei Datenlücken.
- `🟣` ruhiger Kurs + stark vorauslaufendes Volumen · `🟢` nahe am Einstieg · `🔵` frühe positive Auffälligkeit
- `🟠` Divergenz/Warnung · `🔴` abrupter Volumen-Supportverlust/SELL · `🟡` ruhig/unklar · `🟤/⚪` unsichere/fehlende Daten
- Zahl `0–8` = bestätigte Kriterien · `7` = 7-Tage-Kontext · `B` = Stärke zu BTC · `P` = Kurs-/Volumendruck · `V` = rollierender LCW-Volumentrend · `N` = Gesamtbild
- Tage: täglich eingefrorene, BTC- und Pool-bereinigte Top-Tage aus vollständigen Wochen.
- Normal: `1 Map + BTC + 22 Historien ≈ 24 Requests`; harter Detaildeckel inklusive Reserve: höchstens etwa 26 Requests pro Monitorlauf.

Nach einem frischen Update füllt sich der Vollpool-Snapshotverlauf über 10/20/60 Minuten; Map-Daten halten die Rangfolge während des Warm-ups aktiv.
