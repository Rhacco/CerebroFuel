# CF v3.7

Lighter-Monitor mit kompakten P/T/W-Signalen und zustandsfestem Paper-Trading.

```text
PMP🔵UNI🔵HYP🔴AAV🟢GRM🟢NER🟣:43
🟣▲ 5🟢20🟢60🟢 V🟢L🟢B🟢K🟢F🟢 NERP
🟢▲ 5🟡20🟢60🟢 V🟢L🟢B🟢K🟢F🟢 AAVT
NERL:2$20x LITS:1$10x
```

Oben stehen stets die Top‑6 aufsteigend; die beste Chance liegt direkt vor der
Berliner Minute. Darunter erscheinen die Top‑2 immer, Rang 3 nur nahe an einer
Freigabe und Rang 4 ausschließlich bei vier vollständigen Spitzensignalen.

- `🟣▲/▼` frischer Soforteinstieg
- `🟢▲` / `🔴▼` starke Richtung
- `🔵▲` / `🟠▼` Aufbau
- `🟡▲/▼` schwache Tendenz · `⚫?` unsichere Daten

`P` steht für Preis-/Volumendruck, `T` für Trend-Rücksetzer und `W` für eine
bestätigte Schockwende. `5/20/60` zeigt Impuls, Bestätigung und Kontext.
`V/L/B/K/F` bewertet Volumen, Liquidität/OI, BTC-/Marktbreite,
Orderbuchausführung und Funding.

Das Paper-Konto startet mit `100$` und führt höchstens drei Positionen. Der
Hebel wird zwischen `10x` und `50x` aus Signalqualität, Stop-Abstand,
Marktbreite, Kosten und dem aktuellen Lighter-Markthebel gewählt.

```text
NERL:2$20x          Long, 2$ Margin, 20x
LITS:1$10x          Short, 1$ Margin, 10x
NERL:+1$20x         Long um 1$ erhöhen
NERC:1$+0.18$       1$ Margin mit 0.18$ netto schließen
BTCRS:2$15x-0.11$   BTC von Long auf 2$ Short bei 15x drehen
```

Schließungen und Reverse haben in der Discord-Zeile Vorrang. Weitere
gleichzeitig nötige Aktionen werden vollständig im Laufprotokoll festgehalten.
Ausführung, Funding, Stops, Teilziele und realisierte Ergebnisse landen
zusätzlich in `output/latest.json`; der Kontostand wird automatisch
wiederhergestellt und regelmäßig im Branch `paper-state` gesichert.

```bash
python main.py --no-send
```

GitHub benötigt `DISCORD_WEBHOOK_URL`. Der Worker ruft `monitor.yml` minütlich
auf; Cron: `* * * * *`.
