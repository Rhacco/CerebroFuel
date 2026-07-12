# Krypto-Signal-Monitor – Version 1

Automatischer 30-Minuten-Bericht für **HBAR** und **UNI**, jeweils gegen **Bitcoin**. Der Bericht wird durch GitHub Actions berechnet und über einen Discord-Webhook versendet.

## Was Version 1 auswertet

- Kursentwicklung über 24 Stunden und 7 Tage
- relative Stärke oder Schwäche gegenüber BTC
- `N++` bis `N--`: Nachfrage-/Verkaufsdruck-Proxy aus Kurs und 24h-Volumen
- `CB++` bis `CB-`: Kurs-Comeback anhand der Position innerhalb der letzten 24 Stunden
- historisch starke und gefährliche Wochentag-/Uhrzeitblöcke aus 42 Tagen Kursdaten
- festes technisches Signal: `EIN`, `WARTEN` oder `AUS`

Die Signale sind regelbasiert und reproduzierbar. Sie sind keine Finanz- oder Anlageberatung. Eine optische Chart-KI ist in Version 1 noch nicht enthalten.

## Discord-Format

```text
📊 12.07 20:37 | BTC +1.2/-0.7 | MKT↗
HBAR $0.1235 | +3.4/+6.1 | vsB +2.2/+6.8 | N+ CB+ TZ+ WT>WE | 🟢EIN
UNI $7.432 | -1.1/+2.8 | vsB -2.3/+3.5 | N- CB? TZ⚠ WE>WT | 🟡WARTEN
⏱ HBAR: +DI 12–16h / ⚠SO 00–04h · UNI: +MO 08–12h / ⚠SA 20–00h | 42T
24h/7d · autom. technisches Signal, keine Anlageberatung · Daten: Live Coin Watch
```

Abkürzungen:

- `vsB`: Prozentpunkte stärker oder schwächer als Bitcoin
- `N`: Nachfrage-/Verkaufsdruck-Proxy
- `CB`: Comeback
- `TZ+`, `TZ=`, `TZ⚠`: aktueller Wochentag-/Uhrzeitblock historisch positiv, neutral oder riskant
- `WT>WE`: Werktage waren stärker als das Wochenende; `WE>WT` entsprechend umgekehrt

## 1. Dateien in GitHub hochladen

1. ZIP-Datei entpacken.
2. Im privaten GitHub-Repository **Add file → Upload files** öffnen.
3. Den gesamten Inhalt des Ordners `crypto-signal-monitor` hineinziehen. Wichtig: `.github/workflows/monitor.yml` muss ebenfalls hochgeladen werden.
4. Unten **Commit changes** auswählen.

## 2. Kostenlosen Live-Coin-Watch-API-Key erstellen

1. `https://www.livecoinwatch.com/tools/api` öffnen.
2. Registrieren oder anmelden.
3. Kostenlosen API-Key erzeugen und kopieren.

Der Key gehört niemals direkt in `config.json` oder in den Programmcode.

## 3. Discord-Webhook erstellen

1. In Discord den gewünschten Server und Kanal öffnen.
2. **Servereinstellungen → Integrationen → Webhooks** öffnen.
3. Einen neuen Webhook für den Zielkanal erstellen.
4. **Webhook-URL kopieren**.

Die Webhook-URL ist geheim zu behandeln, weil jeder Besitzer dieser URL Nachrichten in den Kanal senden kann.

## 4. GitHub-Secrets anlegen

Im Repository:

1. **Settings → Secrets and variables → Actions** öffnen.
2. **New repository secret** anklicken.
3. Diese beiden Secrets exakt mit folgenden Namen anlegen:

| Name | Inhalt |
|---|---|
| `LCW_API_KEY` | API-Key von Live Coin Watch |
| `DISCORD_WEBHOOK_URL` | vollständige Discord-Webhook-URL |

## 5. Ersten manuellen Test starten

1. Im Repository den Reiter **Actions** öffnen.
2. Links **Crypto Signal Monitor** auswählen.
3. **Run workflow** anklicken.
4. Für einen ersten Test `Bericht an Discord senden = false` wählen.
5. Nach erfolgreichem Test erneut starten und `true` wählen.

Im Workflow-Lauf zeigt der Schritt **Analyse ausführen** den kompletten Bericht. Zusätzlich wird unter **Artifacts** eine Datei mit `latest_report.txt` und `latest_analysis.json` für sieben Tage gespeichert.

## Automatischer Zeitplan

Der Workflow läuft automatisch jeweils um Minute **07 und 37** jeder Stunde:

```yaml
- cron: "7,37 * * * *"
```

Das entspricht ungefähr einem 30-Minuten-Intervall. GitHub kann geplante Läufe gelegentlich etwas verzögern.

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

Weitere Coins können beispielsweise so ergänzt werden:

```json
"coins": ["HBAR", "UNI", "SOL", "XRP"]
```

Jeder zusätzliche Coin benötigt pro Durchlauf zwei weitere API-Abfragen: eine aktuelle Abfrage und eine Historienabfrage.

## Lokal testen – optional

Voraussetzungen: Python 3.11 oder neuer.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
pip install -r requirements.txt
set LCW_API_KEY=DEIN_KEY
python main.py --no-send
```

Für den lokalen Discord-Test zusätzlich:

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
- `.github/workflows/monitor.yml`: automatischer GitHub-Actions-Zeitplan
- `tests/test_analysis.py`: kleine Funktionstests

## Spätere Erweiterungen

Der Aufbau ist vorbereitet für zusätzliche Coins, kürzere Momentaufnahmen, Warnungen nur bei Signalwechsel, Chart-Screenshots, optische KI-Auswertung und getrennte Ein-/Ausstiegsalarme.
