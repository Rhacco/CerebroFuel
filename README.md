# CF v3.6.3

Kompakter Lighter-Monitor für kleine manuelle Perpetual-Trades.

```text
PMP🔵UNI🔵HYP🔴AAV🟢GRM🟢NER🟣:43
🟣▲ 5🟢20🟢60🟢 V🟢L🟢B🟢K🟢F🟢 NERP
🟢▲ 5🟡20🟢60🟢 V🟢L🟢B🟢K🟢F🟢 AAVT
```

Oben stehen stets die Top‑6 aufsteigend; die beste Chance liegt direkt vor der
Berliner Minute. Unten erscheinen die Top‑2 immer. Rang 3 wird nur bei engem
Abstand zur Einstiegsfreigabe ergänzt, Rang 4 nur bei einem weiteren
außergewöhnlich nahen und vollständig bestätigten Kandidaten.

- `🟣▲/▼` frischer Soforteinstieg
- `🟢▲` / `🔴▼` starke Richtung
- `🔵▲` / `🟠▼` Aufbau
- `🟡▲/▼` schwache Tendenz · `⚫?` unsichere Daten

Das letzte Zeichen jeder Detailzeile bezeichnet das Setup:

- `P` bestätigter Preis-/Volumendruck über 10/20/60 Minuten
- `T` ruhiger Rücksetzer und stabile Wiederaufnahme im 60‑Minuten-Trend
- `W` frühe bestätigte Wende nach einem außergewöhnlichen Ausschlag

`5/20/60` zeigt Impuls, Bestätigung und Kontext. `V` bewertet das
setupgerechte Volumen, `L` Liquidität/OI, `K` den ausführbaren 50‑USDC-
Roundtrip und `F` Funding relativ zur Pfeilrichtung. `B` ist bei Altcoins der
BTC-Risikofilter und bei BTC die Breite der liquiden Referenzmärkte.

Bei `V/L/B/K/F` gilt `🟢` günstig, `🟡` grenzwertig, `🔴` entgegenwirkend und
`🟤` nicht belastbar verfügbar.

Abweichende Kürzel: `HYP` HYPE · `PMP` PUMP · `AAV` AAVE · `NER` NEAR ·
`GRM` GRAM.

```bash
python main.py --no-send
```

Für minutengenaue `W`-Fenster: Worker-Cron `* * * * *`.
