# Crypto Signal Monitor v3.2.5

Alle 5 Minuten: frische LiveCoinWatch-Daten → BTC + 8 Coins mit stärkster, **zeitlich bestätigter** Nähe zu Akkumulation oder Distribution → Discord. Keine KI, kein Cache, kein KV.

```text
🟢6▲7🔵B🟢P🟢V🟣🟢🔵N🟢SADI:01
🟣8▲7🟢B🟢P🟣V🟣🟢🟢N🟣SADIWIF
🟠5▼7🟡B🟠P🟠V🟡🟠🟠N🟠SASOAPE
```

| Feld | Bedeutung |
|---|---|
| Anfang | geglättete Extremnähe: Akkumulation ↔ Distribution |
| `X▲/▼` | 0–8 unabhängigere Bestätigungen; keine Erfolgswahrscheinlichkeit |
| `7` | 7-Tage-Lage relativ zur eigenen 120-Tage-Verteilung |
| `B` | Stärke zu BTC über 10/20/60 Min.; BTC = eigene Marktbestätigung |
| `P` | zeitlich geglätteter Kurs-/Volumendruck |
| `V` | LCW-Volumentrend 10/20/60 Min.; Einzelsprünge werden abgewertet |
| `N` | strengstes Gesamtsignal aus Muster, Verlauf, `P`, `B`, `7` und Datenqualität |
| `SADI` | bis zu 2 robust positive Wochentage aus 120 Tagen |
| Ende | Coin-Kürzel; BTC-Zeile endet mit Minute |

Farben: `🟣` nur anhaltende, mehrfach bestätigte Akkumulation · `🟢` bestätigt positiv · `🔵` positiv, noch nicht voll bestätigt · `🟡` neutral/Trendwechsel offen · `🟠` negative Warnung/Distribution · `🔴` nur anhaltendes, mehrfach bestätigtes SELL-Extrem · `🟤` unsicheres Fenster · `⚪` Daten fehlen.

Qualität: 10/20/60-Min.-Muster plus rekonstruierte Signale von vor 5–30 Min.; mindestens 3 aufeinanderfolgende Bestätigungen für starke Farben; gegensätzliche Alt-Signale bleiben als Schutz aktiv; einzelne Volumensprünge können kein starkes BUY erzeugen. Wochentage: Median/MAD, mindestens 12 Werte je Tag, letzte 45 Tage müssen die 120 Tage bestätigen; sonst kein Tag. `V` misst die Änderung des LCW-24h-Rollvolumens, kein echtes Kerzenvolumen.

Betrieb: GitHub-Secrets `LCW_API_KEY`, `DISCORD_WEBHOOK_URL` · Cloudflare unverändert: `ENABLED=1` aktiv, `2` Pause · Cron `1,6,11,16,21,26,31,36,41,46,51,56 * * * *`.
