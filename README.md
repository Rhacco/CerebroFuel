# Crypto Signal Monitor v3.2.7

BTC + 8 auffälligste Coins. Flash-Rang reagiert sofort; starke Farben/Zahl benötigen bestätigte 10/20/60-Min.-Muster und rekonstruierte 5–35-Min.-Zustände.

```text
🟡3=7🔵B🔵P🟡V🟠🟠🟠N🟡FR:01
🟣8▲7🟢B🟢P🟣V🟣🟢🟢N🟣SADIWIF
```

- Anfang: Akkumulation `🟣` ↔ Distribution `🔴`
- `X▲/▼/=`: bestätigte Kriterien, keine Wahrscheinlichkeit
- `7`: 7-Tage-Lage zur eigenen Historie
- `B`: Stärke vs. BTC; bei BTC bestätigte eigene Stärke
- `P`: Kauf-/Verkaufsdruck
- `V`: LCW-Rollvolumen 10/20/60 Min.
- `N`: konservatives Gesamtsignal
- `SADI`: max. 2 positive Wochentage; danach Coin bzw. Minute

`🟣` Extrem+ · `🟢` bestätigt+ · `🔵` früh+ · `🟡` neutral · `🟠` Warnung · `🔴` Extrem− · `🟤` fraglich · `⚪` fehlt

Wochentage: einmal täglich, nur abgeschlossene Tage, bis 365 Tage, robuste Lang-/Kurzphasenprüfung, tägliche Hysterese. Normale Läufe verwenden den Tagescache; API-Fehler übernehmen den letzten gültigen Stand statt Langzeitdaten alle 5 Min. neu zu laden.
