# Krypto-Signal-Monitor – Version 1.1

Automatischer 30-Minuten-Bericht für **HBAR** und **UNI**, jeweils gegen **Bitcoin**. GitHub Actions berechnet den Bericht und sendet ihn über einen Discord-Webhook.

## Änderungen in Version 1.1

- Trend-Prozentwerte in Discord wurden durch `+`, `++`, `+++`, `=`, `-`, `--`, `---` ersetzt.
- `TZ` wurde durch das verständlichere `Jetzt` ersetzt.
- Statt `WT>WE` wird nur noch der stärkste Wochentag als `Top:DI` angezeigt.
- Das unklare Wort „Lernphase“ wurde entfernt.
- Wenn die API nicht genug brauchbare Zeitdaten liefert, erscheint ausdrücklich `Zeitdaten:zu wenig`. Dann wird kein Zeitmuster geraten.
- Die genauen Prozentwerte bleiben weiterhin in `output/latest_analysis.json` erhalten.

## Discord-Format

```text
📊 12.07 21:37 | BTC 24h+ 7d- | Markt→
HBAR $0.1235 | 24h++ 7d+ | vsBTC ++/+ | Druck++ Comeback+ | Jetzt+ Top:DI | 🟢EIN
UNI $7.432 | 24h- 7d= | vsBTC --/+ | Druck- Comeback? | Jetzt⚠ Top:MO | 🟡WARTEN
+/++/+++ = leicht/klar/stark · Zeitbasis 42T · autom. technisches Signal · keine Anlageberatung · LCW
```

Die Reihenfolge bei `vsBTC` ist immer **24 Stunden / 7 Tage**.

## Bedeutung der kompakten Angaben

- `24h++`: in 24 Stunden klar positiv
- `7d---`: in 7 Tagen stark negativ
- `vsBTC ++/+`: über 24 Stunden klar und über 7 Tage leicht stärker als Bitcoin
- `Druck++`: klarer Kauf-/Nachfragedruck aus Kursrichtung und relativem Volumen
- `Druck--`: klarer Verkaufsdruck
- `Comeback+`: Erholung innerhalb der letzten 24 Stunden
- `Jetzt+`: der aktuelle Wochentag-/4-Stunden-Block war historisch eher günstig
- `Jetzt=`: historisch neutral
- `Jetzt⚠`: historisch eher schwach oder riskant
- `Top:DI`: Dienstag war im betrachteten Zeitraum der stärkste Wochentag
- `Zeitdaten:zu wenig`: weniger als 20 brauchbare historische Bewegungen; das Programm gibt bewusst keine Zeitbewertung aus

`Zeitdaten:zu wenig` ist **keine Lernphase einer KI**. Bei jedem Lauf werden die letzten 42 Tage neu abgerufen und direkt berechnet. Reichen die Daten nicht aus, wird die Zeitangabe ausgelassen, statt etwas zu schätzen.

## Was ausgewertet wird

- Kursrichtung über 24 Stunden und 7 Tage
- relative Stärke oder Schwäche gegenüber BTC
- Nachfrage-/Verkaufsdruck aus Kurs und 24h-Volumen
- Kurs-Comeback innerhalb der vergangenen 24 Stunden
- aktueller Wochentag-/4-Stunden-Block aus historischen Daten
- genau ein stärkster Wochentag
- festes technisches Signal: `EIN`, `WARTEN` oder `AUS`

Die Signale sind regelbasiert und reproduzierbar. Sie sind keine Finanz- oder Anlageberatung. Eine optische Chart-KI ist noch nicht enthalten.

## Update im bestehenden Repository

1. ZIP-Datei entpacken.
2. Den Inhalt des Ordners `crypto-signal-monitor` in die oberste Ebene des bestehenden Repositorys hochladen.
3. Vorhandene Dateien überschreiben lassen.
4. **Commit changes** anklicken.
5. Unter **Actions → Crypto Signal Monitor → Run workflow** einen manuellen Test starten.

Deine bestehenden GitHub-Secrets bleiben erhalten:

- `LCW_API_KEY`
- `DISCORD_WEBHOOK_URL`

## Ersteinrichtung

### 1. Dateien in GitHub hochladen

1. ZIP-Datei entpacken.
2. Im privaten GitHub-Repository **Add file → Upload files** öffnen.
3. Den gesamten Inhalt des Ordners `crypto-signal-monitor` hineinziehen.
4. Prüfen, dass `.github/workflows/monitor.yml` vorhanden ist.
5. **Commit changes** auswählen.

### 2. GitHub-Secrets

Unter **Settings → Secrets and variables → Actions** müssen diese Repository-Secrets vorhanden sein:

| Name | Inhalt |
|---|---|
| `LCW_API_KEY` | API-Key von Live Coin Watch |
| `DISCORD_WEBHOOK_URL` | vollständige Discord-Webhook-URL |

### 3. Manueller Test

1. **Actions** öffnen.
2. **Crypto Signal Monitor** auswählen.
3. **Run workflow** anklicken.
4. Zunächst `Bericht an Discord senden = false` wählen.
5. Nach erfolgreichem Lauf erneut mit `true` testen.

## Automatischer Zeitplan

Der Workflow läuft ungefähr um Minute **07 und 37** jeder Stunde:

```yaml
- cron: "7,37 * * * *"
```

GitHub kann geplante Läufe gelegentlich etwas verzögern.

## Coins oder Einstellungen ändern

Nur `config.json` bearbeiten:

```json
{
  "reference_coin": "BTC",
  "coins": ["HBAR", "UNI"],
  "currency": "USD",
  "timezone": "Europe/Berlin",
  "history_days": 42,
  "time_block_hours": 4,
  "seasonality_min_samples": 3,
  "request_timeout_seconds": 30,
  "discord_username": "Krypto-Monitor",
  "signal_thresholds": {
    "entry": 3.0,
    "exit": -2.5
  }
}
```

Weitere Coins:

```json
"coins": ["HBAR", "UNI", "SOL", "XRP"]
```

## Lokaler Test – optional

Voraussetzung: Python 3.11 oder neuer.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
pip install -r requirements.txt
set LCW_API_KEY=DEIN_KEY
python main.py --no-send
```

Für einen lokalen Discord-Test zusätzlich:

```bash
set DISCORD_WEBHOOK_URL=DEINE_WEBHOOK_URL
python main.py
```

## Dateien

- `main.py`: Ablauf und Dateiausgabe
- `lcw_client.py`: Live-Coin-Watch-API
- `analysis.py`: Kennzahlen, Zeitmuster und Signallogik
- `discord_sender.py`: Discord-Versand
- `config.json`: Coins und veränderbare Einstellungen
- `.github/workflows/monitor.yml`: GitHub-Actions-Zeitplan
- `tests/test_analysis.py`: Funktionstests
