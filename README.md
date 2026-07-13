# Crypto Signal Monitor V3.2.1 – Fresh & Compact

Direkter Ersatz für V3.2 Fresh und weiterhin auf dem zuverlässig laufenden V3.0-Aufbau:

- kein Cloudflare KV
- kein `TEST_KEY`
- alle aktuellen Daten und Historien bei jedem Lauf frisch von Live Coin Watch
- immer BTC plus die acht auffälligsten Coins
- extrem kompakte Discord-Zeilen ohne Abstandhalter
- maximal ein bis zwei stärkste Wochentage

## Discord-Ausgabe

```text
🟢:01 6/7▲7🔵B🟢P🟢V🟢🟣🟢N🟡DIDO
🟣ETH7/8▲7🟢B🟢P🟣V🟣🟢🔵N🟡DIDO
🔴DGE6/8▼7🟠B🔴P🔴V🟢🟢🔵N🟠SASO
```

Nur die erste Zeile enthält genau ein Leerzeichen: direkt nach der Uhrzeit. Sonst werden keine Abstandhalter oder Leerzeichen verwendet.

## GitHub aktualisieren

1. ZIP entpacken.
2. Den **Inhalt** des Ordners `crypto-signal-monitor` in die oberste Ebene des Repositorys hochladen.
3. Vorhandene Dateien überschreiben und committen.
4. Diese GitHub-Secrets behalten:

```text
LCW_API_KEY
DISCORD_WEBHOOK_URL
```

5. Einmal manuell testen:

```text
Actions → Crypto Signal Monitor → Run workflow
send_discord: false
```

Danach mit `send_discord: true` testen.

## Cloudflare Worker

Den Inhalt von `cloudflare-worker.js` in den bestehenden Cloudflare Worker kopieren und deployen.

Normale Variablen:

```text
GH_OWNER     = GitHub-Benutzername
GH_REPO      = Repository-Name
GH_REF       = master
GH_WORKFLOW  = monitor.yml
ENABLED      = 1
```

Secret:

```text
GH_PAT
```

`TEST_KEY` wird nicht benötigt.

### Einfache Steuerung

```text
ENABLED = 1  → aktiv
ENABLED = 2  → pausiert
```

Der Cron-Trigger bleibt bestehen. Bei `2` nimmt Cloudflare das Ereignis an, startet aber keinen GitHub-Lauf.

### Cron alle fünf Minuten

```cron
1,6,11,16,21,26,31,36,41,46,51,56 * * * *
```

## Kürzel

- Anfangskreis: Gesamtsignal des Coins; bei BTC grün oder schwarz nach der BTC-Sammelbedingung
- `:01`: Minute der Analyse
- `6/7▲`: sechs von sieben positiven BTC-Bedingungen erfüllt
- `7/8▲` bzw. `6/8▼`: erfüllte positive bzw. negative Coin-Bedingungen
- `7`: Kursrichtung über sieben Tage
- `B`: kurzfristige Stärke gegenüber BTC; in der BTC-Zeile grün/schwarz nach der BTC-Sammelbedingung
- `P`: kombinierter Kursdruck mit Volumenbestätigung
- `V`: Volumentrend in der Reihenfolge 10/20/60 Minuten
- `N`: historische Bewertung des aktuellen Wochentags bzw. Zeitblocks
- `DIDO`, `FR`, `SASO`: stärkster oder zwei stärkste Wochentage, direkt aneinandergefügt

## Farben

- `🟣`: außergewöhnlich stark oder Volumenspitze
- `🟢`: klar positiv
- `🔵`: leicht positiv
- `🟡`: neutral oder gemischt
- `🟠`: nachlassend oder negative Warnung
- `🔴`: klar negativ
- `🟤`: Daten vorhanden, aber unsicher
- `⚪`: nicht genügend frische Daten für mindestens zwei Kurzfenster
- `⚫`: BTC-Gesamtbedingung nicht erfüllt

`🟤` kann durch sehr geringes oder unplausibles Volumen, ungewöhnlich wenige Historienpunkte oder extreme Datensprünge entstehen.

`⚪` erscheint nur, wenn der frische Historienabruf fehlschlägt oder kein passender Vergleichspunkt für mindestens zwei der Fenster 10/20/60 Minuten vorhanden ist.

## Coin-Kürzel

Coin-Codes mit mehr als drei Zeichen werden automatisch auf drei Zeichen verkürzt:

```text
DOGE → DGE
HBAR → HBR
RENDER → RND
FARTCOIN → FRT
```

## Lokaler Test

```bash
python -m unittest discover -s tests -v
python main.py --no-send
```
