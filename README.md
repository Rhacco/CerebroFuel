# Krypto-Signal-Monitor – Version 2.0

Regelbasierter BUY/SELL-Monitor für BTC und zwei konfigurierbare Coin-Gruppen. Cloudflare löst alle fünf Minuten einen öffentlichen GitHub-Actions-Workflow aus. Das Ergebnis wird ohne Überschriften, Leerzeilen oder Fußnote kompakt an Discord gesendet.

## Beispielausgabe

```text
BTC · 5/8▲ · 24h+ · 7d= · vB= · P+ · N= · DI/DO
🟢 ETH · 8/8▲ · 24h++ · 7d+++ · vB++ · P+++ · N+ · DI/DO/FR
🟢 SOL · 7/8▲ · 24h++ · 7d+ · vB+++ · P++ · N+ · MO/DI/DO
🔴 DOGE · 7/8▼ · 24h-- · 7d- · vB--- · P--- · N⚠ · SA/SO
🟢 HYPE · 8/8▲ · 24h+++ · 7d++ · vB+++ · P+++ · N+ · DI/MI/DO
🔴 TIA · 6/8▼ · 24h- · 7d-- · vB--- · P-- · N- · MO/SA
```

Die tatsächliche Ausgabe enthält nur Coins mit einem klaren BUY- oder SELL-Signal. BTC steht immer als Referenz in der ersten Zeile.

## Kürzel

- `X/8▲`: X von 8 BUY-Bedingungen sind erfüllt.
- `X/8▼`: X von 8 SELL-Bedingungen sind erfüllt.
- `X/8=`: BUY und SELL sind gleich stark; dies kann praktisch nur bei BTC vorkommen.
- `24h`: Kursrichtung der letzten 24 Stunden.
- `7d`: Kursrichtung der letzten sieben Tage.
- `vB`: kombinierte relative Stärke oder Schwäche gegenüber Bitcoin aus 24h und 7d.
- `P`: Kauf- oder Verkaufsdruck aus Kursrichtung und LCW-Volumentrend.
- `N+`: aktueller Wochentag-/4-Stunden-Block war in den letzten 90 Tagen eher günstig.
- `N=`: aktueller Zeitblock war historisch neutral.
- `N-`: aktueller Zeitblock war historisch eher schwach.
- `N⚠`: aktueller Zeitblock war historisch deutlich schwach oder riskant.
- `N?`: zu wenig passende Zeitdaten; es wird nichts geraten.
- `MO/DI/...`: die zwei bis vier historisch stärksten Wochentage für den Coin.
- `+ / ++ / +++`: leicht / klar / stark positiv.
- `- / -- / ---`: leicht / klar / stark negativ.
- `=`: neutral.
- `?`: nicht genug Daten.

## Die acht Bedingungen

Für BUY und SELL werden jeweils dieselben acht Bereiche in die passende Richtung geprüft:

1. Kurs 24h
2. Kurs 7d
3. LCW-Volumentrend 24h
4. LCW-Volumentrend 7d
5. Stärke gegen BTC 24h
6. Stärke gegen BTC 7d
7. Druck `P`
8. aktueller Zeitblock `N`

Ein Nicht-BTC-Coin wird erst ab `6/8` angezeigt.

### Zusätzliche BUY-Pflichtbedingungen

- LCW-Volumentrend über 24h klar steigend
- bestätigender 7d-Volumentrend oder Stärke gegen BTC
- positiver Druck `P`
- aktueller Zeitblock nicht negativ

### Zusätzliche SELL-Pflichtbedingungen

- LCW-Volumen über 24h steigt, während der Kurs fällt
- negativer Druck `P`
- klare Schwäche gegen BTC über 24h oder 7d

Die angezeigten `24h`- und `7d`-Zeichen beschreiben den **Kurs**. Die Volumentrends sind Filter und Bestandteil von `X/8` sowie `P`, werden aber aus Platzgründen nicht als zusätzliches Feld ausgegeben.

## Coin-Reihenfolge

Ohne Überschriften oder Leerzeilen:

1. BTC immer zuerst
2. klare BUY-Coins der ersten Gruppe
3. klare SELL-Coins der ersten Gruppe
4. klare BUY-Coins der zweiten Gruppe
5. klare SELL-Coins der zweiten Gruppe

Die Gruppen und Coin-Codes stehen in `config.json`. `ZKSYNC` wird angezeigt, aber bei Live Coin Watch mit dem API-Code `ZK` abgefragt.

## API-sparender Betrieb alle fünf Minuten

- Aktuelle Werte aller Coins kommen mit **einem** `/coins/map`-Aufruf je Durchlauf.
- Historische Daten werden nur alle sechs Stunden neu geladen.
- Dazwischen stellt GitHub Actions die Historie aus einem Cache wieder her.
- Die Historie wird auf ungefähr einen Punkt pro Stunde reduziert.
- Es wird keine KI und keine optische Bildanalyse verwendet.

Damit bleibt die Zahl der Live-Coin-Watch-Abfragen trotz der großen Coin-Liste niedrig.

## Update im bestehenden Repository

1. ZIP entpacken.
2. Den kompletten Inhalt des Ordners in die oberste Ebene des Repositorys hochladen.
3. Vorhandene Dateien überschreiben.
4. **Commit changes** anklicken.
5. Unter **Actions → Crypto Signal Monitor → Run workflow** zuerst mit `send_discord = false` testen.
6. Danach einmal mit `send_discord = true` testen.

Die vorhandenen Secrets bleiben unverändert:

- `LCW_API_KEY`
- `DISCORD_WEBHOOK_URL`

## GitHub Actions und Cloudflare

Der Workflow enthält absichtlich **keinen GitHub-Cron** mehr. Er wird nur über `workflow_dispatch` gestartet. Dein Cloudflare Worker kann unverändert weiterhin `monitor.yml` alle fünf Minuten auslösen.

Für `:04, :09, :14, :19 ... :59` lautet der Cloudflare-Cron:

```cron
4,9,14,19,24,29,34,39,44,49,54,59 * * * *
```

Eine aktuelle Kopie des Workers liegt als `cloudflare-worker.js` bei.

## Erster Lauf

Der erste Lauf dauert länger, weil für alle Coins die 90-Tage-Historie geladen wird. Danach nutzt das System sechs Stunden lang den GitHub-Actions-Cache und läuft deutlich schneller. Wenn ein einzelner LCW-Code nicht verfügbar ist, wird nur dieser Coin übersprungen; der gesamte Bericht läuft weiter.

## Einstellungen

Wichtige Felder in `config.json`:

```json
{
  "history_days": 90,
  "history_refresh_hours": 6,
  "recommendation_threshold": 6,
  "time_block_hours": 4
}
```

- `history_days`: Grundlage für Wochentage und Zeitblock.
- `history_refresh_hours`: Abstand zwischen den umfangreicheren Historienabrufen.
- `recommendation_threshold`: Mindestanzahl erfüllter BUY-/SELL-Bedingungen.
- `time_block_hours`: Länge eines Zeitblocks für `N`.

## Lokaler Test

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
pip install -r requirements.txt
set LCW_API_KEY=DEIN_KEY
python main.py --no-send
```

Historie unabhängig vom Cache neu laden:

```bash
python main.py --no-send --force-history-refresh
```

## Dateien

- `main.py`: Ablauf, Cache-Entscheidung und Bericht
- `analysis.py`: Regeln, Zählung, Wochentage und Format
- `lcw_client.py`: Live-Coin-Watch-API
- `history_store.py`: kompakter History-Cache
- `discord_sender.py`: Discord-Versand und Aufteilung langer Berichte
- `config.json`: Coins, Gruppen und Schwellenwerte
- `.github/workflows/monitor.yml`: externer GitHub-Actions-Start
- `cloudflare-worker.js`: Scheduler-Kopie
- `tests/test_analysis.py`: Funktionstests
