# Crypto Signal Monitor v3.2.6

Alle 5 Minuten: frische LiveCoinWatch-Daten → BTC + 8 auffälligste Coins → Discord. Keine KI, kein Cloudflare-KV.

```text
🟡3=7🔵B🟡P🟡V🟠🟠🟠N🟡FR:01
🟣8▲7🟢B🟢P🟣V🟣🟢🟢N🟣SADIWIF
```

| Feld | Bedeutung |
|---|---|
| Anfang | bestätigte Nähe: `🟣` Akkumulation ↔ `🔴` Distribution |
| `X▲/▼/=` | 0–8 bestätigte Kriterien; keine Erfolgswahrscheinlichkeit |
| `7` | 7-Tage-Lage relativ zur eigenen Historie |
| `B` | Coin vs. BTC; bei BTC eigene Bewegung mit Volumenbestätigung |
| `P` | geglätteter Kauf-/Verkaufsdruck |
| `V` | rollierender LCW-Volumentrend für 10/20/60 Minuten |
| `N` | konservative Gesamtlage aus Muster, Dauer, `B`, `P`, `7` |
| `SA…FR` | maximal 2 täglich fixierte positive Wochentage |
| Ende | Coin-Kürzel; bei BTC Ausführungsminute |

`🟣` extrem positiv · `🟢` klar positiv · `🔵` leicht positiv · `🟡` neutral · `🟠` Warnung · `🔴` extrem negativ · `🟤` unsicher · `⚪` fehlend

**Reaktion + Sicherheit:** Flash-Rangfolge kann neue 5–15-Minuten-Auffälligkeiten sofort nach oben holen. Farben und Zahl bleiben streng zeitlich bestätigt; einzelne Sprünge erzeugen kein Extrem.

**Wochentage:** einmal täglich für alle Coins aus abgeschlossenen Kalendertagen berechnet; 365/180/90/45 Tage, robuste Mediane, Ausreißertest, Mindesttrefferquote und 2-Tage-Ein-/Austrittsbremse. Innerhalb eines Tages unverändert. Bei API-Fehler bleibt die letzte gültige Auswahl bestehen.

## Installation

1. ZIP-Inhalt ins Repository kopieren und überschreiben.
2. Secrets behalten: `LCW_API_KEY`, `DISCORD_WEBHOOK_URL`.
3. Einmal manuell über **Actions → Crypto Signal Monitor → Run workflow** testen.
4. Cloudflare bleibt unverändert (`ENABLED=1` aktiv, `2` pausiert).

Der erste Lauf eines Tages lädt Langzeithistorien für alle Coins. Danach werden nur Map-Daten und Kurzzeithistorien geladen; GitHub Actions speichert den Tageskontext automatisch.
