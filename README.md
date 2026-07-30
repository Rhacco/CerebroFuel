# CF v3.6.3

Kompakter Lighter-Monitor für kleine, manuell ausgeführte Perpetual-Trades.
Er führt keine Orders aus.

## Discord

Beispiel:

```text
PMP🔵UNI🔵HYP🔴AAV🟢GRM🟢:43
🟣▲P 5🟢20🟢60🟢 V🟢L🟢B🟢K🟢F🟢 GRM
🟢▲T 5🟡20🟢60🟢 V🟢L🟢B🟢K🟢F🟢 AAV
```

Oben stehen die Top‑5. Innerhalb dieser fünf steigt die Sicherheit von links
nach rechts; die beste aktuelle Chance steht direkt vor der Berliner Uhrzeit.
Das Coin-Kürzel steht immer vor seiner Farbe.

- `🟢` stark Long · `🔴` stark Short
- `🔵` Long baut sich auf · `🟠` Short baut sich auf
- `🟡` nur leichte Tendenz · `⚫` Daten nicht verlässlich

Darunter stehen immer die Top‑2 mit Pfeil, auch bei Gelb. Eine dritte
beziehungsweise vierte Detailzeile erscheint nur für ein echtes Aufbau‑ oder
stärkeres Signal. Das Coin-Kürzel steht am Zeilenende.

- `🟣▲/▼` frisches, sofort handelbares Long-/Short-Fenster
- `🟢▲` / `🔴▼` starke Richtung, aber kein frischer Soforteinstieg
- `🔵▲` / `🟠▼` Aufbau · `🟡▲/▼` schwach
- `⚫?` ausschließlich für unzuverlässige Daten
- `P` anhaltender Preis-/Volumendruck
- `T` kleiner Rücksetzer mit Wiederaufnahme im klaren Trend
- `W` schnelle Wende nach einem ungewöhnlich harten Ausschlag

`5/20/60` zeigt unmittelbare Tendenz, Bestätigung und Stundenkontext. Die
Freigabe arbeitet genauer: `P` mit 10/20/60‑Minuten-Druck, `T` mit
5/15/60‑Minuten-Trend und Rücksetzer, `W` mit Schock und Gegenbestätigung über
wenige Minuten.

- `V` setupgerechte Volumenbestätigung
- `L` Liquidität aus 24h-Volumen, Open Interest und Umsatz/OI
- `B` BTC-Kontext als Risikofilter
- `K` ausführbare Hin-und-zurück-Orderbuchkosten für 50 USDC
- `F` Funding relativ zur Pfeilrichtung

Bei `V/L/B/K/F` bedeutet `🟢` günstig, `🟡` grenzwertig, `🔴` dagegen bzw.
blockierend und `🟤` nicht zuverlässig verfügbar. Funding unterstützt oder
bremst ein Setup, erzeugt aber nie allein ein Signal.

Abweichende Kürzel: `HYP` HYPE · `PMP` PUMP · `AAV` AAVE · `NER` NEAR ·
`GRM` GRAM.

Start:

```bash
python main.py --no-send
```

Für die kurzen `W`-Fenster den Worker-Cron auf `* * * * *` setzen; ein bereits
laufender GitHub-Run wird nicht doppelt gestartet.

Lila ist eine streng gefilterte Momentaufnahme, keine Gewinngarantie.
