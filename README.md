# Crypto Signal Monitor v3.2.6

BTC + 8 aktuell auffälligste Coins aus frischen LCW-Daten. Flash-Rangfolge reagiert schnell; Farben und Zahl brauchen zeitliche Bestätigung. Unvollständige 10/20/60-Min.-Daten werden nicht angezeigt.

```text
🟡3=7🔵B🟡P🟡V🟠🟠🟠N🟡FR:01
🟣8▲7🟢B🟢P🟣V🟣🟢🟢N🟣SADIWIF
```

- Anfang: bestätigte Akkumulation `🟣` ↔ Distribution `🔴`
- `X▲/▼/=`: erfüllte Kriterien, keine Erfolgswahrscheinlichkeit
- `7`: 7-Tage-Lage zur eigenen Historie
- `B`: Stärke vs. BTC; bei BTC bestätigte eigene Kurzfriststärke
- `P`: Kauf-/Verkaufsdruck
- `V`: LCW-Rollvolumen 10/20/60 Min.
- `N`: konservatives Gesamtsignal
- `SADI`: max. 2 positive Wochentage; Ende = Coin bzw. Ausführungsminute

`🟣` Extrem+ · `🟢` bestätigt+ · `🔵` früh+ · `🟡` neutral · `🟠` Warnung · `🔴` Extrem− · `🟤` fraglich · `⚪` fehlt

Qualität: rekonstruierte 5–35-Min.-Zustände, Pfad-/Sprungprüfung, Gegensignal-Hysterese und Flash-Rangfolge. Wochentage: abgeschlossene Tage, 365/180/90/45-Tage-Prüfung, robuste Mediane, Ausreißer-/Trefferquotenprüfung, tägliche Hysterese. Fehlgeschlagene Tagesdaten werden erneut geladen; gültige Ergebnisse ohne positiven Tag bleiben bewusst leer.
