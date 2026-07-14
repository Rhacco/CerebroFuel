# Crypto Signal Monitor v3.2.4

Alle 5 Minuten: frische LCW-Daten → BTC + 8 Coins mit größter Nähe zu **Akkumulation** oder **Distribution** → Discord. Keine KI, kein Cache, kein Cloudflare-KV.

```text
🟢6▲7🔵B🟢P🟢V🟣🟢🔵N🟢SADI:01
🟣8▲7🟢B🟢P🟣V🟣🟢🟢N🟣SADIWIF
🔴7▼7🟠B🔴P🔴V🟠🔴🔴N🔴SASOETH
```

| Feld | Aussage |
|---|---|
| Anfang | Gesamtnähe zum Extrem: stabiler Kurs + stark vorauslaufendes Volumen ↔ steigender Kurs + zurückfallendes Volumen |
| `X▲/▼` | 0–8 bestätigte Kriterien; keine Erfolgswahrscheinlichkeit |
| `7` | 7-Tage-Lage relativ zur eigenen 120-Tage-Verteilung |
| `B` | relative 10/20/60-Min.-Stärke zu BTC; bei BTC eigene Marktbestätigung |
| `P` | aktueller Kurs-/Volumendruck |
| `V` | LCW-Volumentrend 10/20/60 Min. |
| `N` | konservatives Gesamtsignal aus Muster, `P`, `B`, `7` und Datenqualität |
| `SADI` | bis zu 2 robust positive Wochentage; 120 Tage, mind. 12 Werte/Tag, letzte 45 Tage müssen bestätigen |
| Ende | Coin-Kürzel; erste Zeile endet mit Analyseminute |

Farben: `🟣` nur streng bestätigtes positives Extrem · `🟢` klar positiv · `🔵` leicht positiv · `🟡` neutral/gemischt · `🟠` negativ/Warnung · `🔴` nur streng bestätigtes negatives Extrem · `🟤` unsicheres Fenster · `⚪` Daten fehlen.

Qualität: 12-Stunden-Eigenbaseline, robuste Median/MAD-Auswertung, 10/20/60-Min.-Übereinstimmung, konservative Extremfarben, einzelne fehlerhafte Fenster bleiben isoliert. `V` nutzt Veränderungen des von LCW gemeldeten rollierenden 24h-Volumens, nicht echtes Börsen-Kerzenvolumen.

Betrieb: GitHub-Secrets `LCW_API_KEY`, `DISCORD_WEBHOOK_URL` · Cloudflare unverändert: `ENABLED=1` aktiv, `2` Pause · Cron `1,6,11,16,21,26,31,36,41,46,51,56 * * * *`.
