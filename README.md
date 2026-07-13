# Crypto Signal Monitor V3.2 – Fresh & Simple

Diese Fassung ist als direkter Ersatz für die gut laufende V3.0 gedacht:

- kein Cloudflare KV
- kein `TEST_KEY`
- keine Cloudflare-Zustands-API
- jeder GitHub-Lauf lädt aktuelle Daten und Historien frisch von Live Coin Watch
- immer BTC plus die acht auffälligsten Coins aus dem gesamten Pool
- keine Leerzeilen und keine unnötigen Abstände in Discord

## Discord-Ausgabe

Beispiel:

```text
🟢:01·6/7▲·7d🔵·vB🟢·P🟢·V🟢🟣🟢·N🟡·DI/DO
🟣ETH·7/8▲·7d🟢·vB🟢·P🟣·V🟣🟢🔵·N🟡·DI/DO/FR
🔴DGE·6/8▼·7d🟠·vB🔴·P🔴·V🟢🟢🔵·N🟠·SA/SO
```

Die erste Zeile beginnt mit dem BTC-Gesamtkreis und enthält danach nur die Minute des Laufs, zum Beispiel `🟢:01`, `⚫:06` oder `🟢:11`. `vB` steht anschließend an derselben Position wie in allen Coin-Zeilen.

## GitHub aktualisieren

1. ZIP entpacken.
2. Den **Inhalt** des Ordners `crypto-signal-monitor` in die oberste Ebene des Repositorys hochladen.
3. Vorhandene Dateien überschreiben und committen.
4. Diese GitHub-Secrets behalten:

```text
LCW_API_KEY
DISCORD_WEBHOOK_URL
```

Nicht benötigt werden:

```text
CF_STATE_URL
CF_STATE_KEY
```

5. Einmal manuell testen:

```text
Actions → Crypto Signal Monitor → Run workflow
send_discord: false
```

Danach mit `send_discord: true` testen.

## Cloudflare Worker

Den Inhalt von `cloudflare-worker.js` in den vorhandenen Cloudflare Worker kopieren und deployen.

Normale Variablen:

```text
GH_OWNER     = GitHub-Benutzername
GH_REPO      = Repository-Name
GH_REF       = master
GH_WORKFLOW  = monitor.yml
ENABLED      = true
```

Secret:

```text
GH_PAT
```

`TEST_KEY` wird nicht mehr benötigt und kann gelöscht werden.

### Cron: alle fünf Minuten

Im Cloudflare-Dashboard genau einen Cron-Trigger verwenden:

```cron
1,6,11,16,21,26,31,36,41,46,51,56 * * * *
```

Damit startet der Worker jeweils um `:01`, `:06`, `:11`, `:16` usw.

### Pausieren ohne Cron zu löschen

Im Worker unter **Settings → Variables and Secrets** ändern:

```text
ENABLED = false
```

Zum Fortsetzen:

```text
ENABLED = true
```

Der Cron bleibt bestehen. Bei `false` beendet der Worker das Ereignis, ohne GitHub zu starten.

Die öffentliche Worker-Adresse zeigt nur den Status an und löst keinen Lauf aus:

```json
{"ok":true,"scheduler":"enabled","interval":"5m"}
```

## Bedeutung der ersten Zeile

```text
🟢:01·6/7▲·7d🔵·vB🟢
```

- erster Kreis `🟢` oder `⚫`: schnelle BTC-Gesamtanzeige
- `:01`: Minute, in der die Analyse erstellt wurde
- `vB🟢`: BTC hatte in 10/20/60 Minuten keinen festgelegten Einbruch **und** der LCW-Volumentrend war in allen drei Fenstern klar steigend
- `vB⚫`: mindestens eine dieser BTC-Bedingungen war nicht erfüllt
- `vB` steht nach `7d` an derselben Position wie bei den übrigen Coins

Bei den anderen Coins bedeutet `vB` die kurzfristige Stärke oder Schwäche gegenüber BTC.

## Kürzel

- `6/7▲`: sechs von sieben positiven BTC-Bedingungen erfüllt
- `7/8▲`: sieben von acht positiven Coin-Bedingungen erfüllt
- `6/8▼`: sechs von acht negativen Coin-Bedingungen erfüllt
- `7d`: Kursrichtung über sieben Tage
- `vB`: relative Kurzfriststärke gegenüber BTC; in der ersten BTC-Zeile die grün/schwarze Marktbedingung
- `P`: kombinierter Kursdruck mit Volumenbestätigung
- `V`: Volumentrend in der festen Reihenfolge **10/20/60 Minuten**
- `N`: historische Bewertung des aktuellen Wochentags bzw. Zeitblocks
- `DI/DO/FR`: zwei bis vier historisch stärkste Wochentage

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

`🟤` kann durch sehr geringes oder unplausibles Volumen, ungewöhnlich wenige Historienpunkte oder einen extremen Datensprung entstehen.

`⚪` erscheint nur, wenn der frische LCW-Historienabruf fehlschlägt oder kein passender Vergleichspunkt für mindestens zwei der Fenster 10/20/60 Minuten vorhanden ist. Fehlende Zeitblockdaten werden neutral als `N🟡` behandelt; fehlende Wochentage werden weggelassen.

## Coin-Kürzel

Coin-Codes mit mehr als drei Zeichen werden automatisch auf drei Zeichen verkürzt:

```text
DOGE → DGE
HBAR → HBR
RENDER → RND
FARTCOIN → FRT
```

Codes mit höchstens drei Zeichen bleiben unverändert.

## Frische Daten und API-Verbrauch

Jeder Lauf verwendet ungefähr:

```text
1 × aktuelle Pool-Daten
13 × frische Kurzzeithistorien
9 × frische 42-Tage-Historien
```

Es gibt keinen Ergebnis- oder Historiencache zwischen den Läufen.

## Lokaler Test

```bash
python -m unittest discover -s tests -v
python main.py --no-send
```
