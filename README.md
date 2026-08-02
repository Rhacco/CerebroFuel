# Crypto Signal Monitor v4.0.0

Pool: `HYPE ENA AAVE PUMP ZEC JUP SUI NEAR ONDO 1000PEPE 1000BONK BTC`. `ONDO`, `1000PEPE` und `1000BONK` bleiben bis Readiness `80` und Confidence `76` bedingte Paper-Kandidaten; Live-Liquidität bleibt eine harte Sperre.

## Discord

Zeile 1 enthält drei dynamische Altcoins plus BTC rechts, maximal 68 Zeichen. Ein akuter Coin darf BTC dort insgesamt 25 Minuten ersetzen. In den ersten 10 Minuten steht derselbe Coin zusätzlich an erster Stelle der Detailzeilen. Jeder Lauf sendet einen neuen Post.

## Akute Ereignisse

- `SEC!`/`NET!`: bestätigtes aktives Sicherheits- oder Netzwerkereignis aus dem verifizierten Feed.
- `SHK!`: strenger coinspezifischer 1/5/10-Minuten-Schock aus Lighter-Kurs, Quote-Volumen und BTC-Abweichung; die Ursache ist ausdrücklich unbestätigt.
- Beide sperren neue Paper-Einstiege. Ein fortdauernder Schock bleibt gesperrt, verlängert aber das 25-Minuten-BTC-Ersatzfenster nicht endlos.

Der optionale Feed läuft über `CRYPTO_EVENTS_URL` oder `CRYPTO_EVENTS_JSON`, wird minütlich geprüft und nach 10 Minuten ohne erfolgreiche Aktualisierung verworfen. Feed-Symbole `BONK/KBONK` und `PEPE/KPEPE` werden intern auf Lighters `1000BONK` und `1000PEPE` abgebildet. Ohne Feed erkennt die App Marktreaktionen als `SHK!`, aber keinen Exploit-Grund.

## Signale

`NEAR` Aufbau · `TRY` belastbare Probe · `NOW` frisches Sofortfenster · `WAIT` kein Einstieg. Ereignisse erzeugen nie selbst Long oder Short.

v4 nutzt ausschließlich den neuen `runtime-state-v400`-Cache und den separaten `paper-state-v400`-Checkpoint. Der Workflow verwendet einen Primärschlüssel plus genau einen Restore-Präfix und bleibt damit dauerhaft unter dem GitHub-Limit von zehn Gesamtschlüsseln.
