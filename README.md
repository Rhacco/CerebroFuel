# Crypto Signal Monitor v3.2.5

Alle 5 Minuten: frische LiveCoinWatch-Daten → BTC + 8 am stärksten **bestätigte** Akkumulations-/Distributionsmuster → Discord. Keine KI, kein Cache, kein KV.

```text
🟡3=7🔵B🟡P🟡V🟠🟠🟠N🟡FR:01
🟣8▲7🟢B🟢P🟣V🟣🟢🟢N🟣SADIWIF
🟠5▼7🟡B🟠P🟠V🟡🟠🟠N🟠APE
```

| Feld | Aussage |
|---|---|
| Anfang | geglättete Nähe: `🟣` bestätigte Akkumulation ↔ `🔴` bestätigte Distribution |
| `X▲/▼/=` | 0–8 unabhängiger geprüfte Bestätigungen; **keine** Erfolgswahrscheinlichkeit |
| `7` | 7-Tage-Lage relativ zur eigenen Historie |
| `B` | Coin vs. BTC; bei BTC eigene Preisbewegung **mit** Volumen-/Druckbestätigung |
| `P` | zeitlich bestätigter Kauf-/Verkaufsdruck |
| `V` | Änderung des LCW-24h-Rollvolumens über 10/20/60 Min. |
| `N` | konservatives Gesamtsignal; starke Farbe nur bei Übereinstimmung mehrerer Felder |
| `SADI` | höchstens 2 robust positive Wochentage aus 120 **abgeschlossenen** Kalendertagen |
| Ende | Coin-Kürzel; BTC-Zeile endet mit Minute |

Farben: `🟣` nur anhaltendes BUY-Extrem · `🟢` bestätigt positiv · `🔵` positiv, noch nicht vollständig bestätigt · `🟡` neutral/uneinig · `🟠` bestätigte Warnung · `🔴` nur anhaltendes SELL-Extrem · `🟤` fragliches Fenster · `⚪` Daten fehlen.

Qualität: 10/20/60-Min.-Muster + rekonstruierte Zustände von vor 5–35 Min.; starke Farben benötigen mindestens 4 fortlaufende Bestätigungen, hohe Datenqualität, stabile Pfade und kein ungelöstes Gegensignal. Ein einzelner Volumensprung kann kein starkes Signal erzeugen. Rangfolge: bestätigte Bedingungen → Extremnähe in stabilen Stufen → Beständigkeit/Datenqualität.

Wochentage: nur abgeschlossene Tage, je Datum genau 1 Beobachtung; 120 Tage + 60-/30-Tage-Bestätigung, robuste Medianwerte, Ausreißertest und Mindesttrefferquote. Die Anzeige bleibt während eines Kalendertags unverändert; bei zu schwacher Evidenz erscheint kein Tag.

Betrieb: GitHub-Secrets `LCW_API_KEY`, `DISCORD_WEBHOOK_URL` · Cloudflare unverändert: `ENABLED=1` aktiv, `2` Pause · Cron `1,6,11,16,21,26,31,36,41,46,51,56 * * * *`.
