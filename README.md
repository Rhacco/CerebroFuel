# Krypto-Signal-Monitor – Version 3.0

Version 3.0 entfernt den bisherigen sechs Stunden alten History-Cache vollständig. Bei jedem Cloudflare-/GitHub-Start werden die aktuellen LCW-Daten neu geladen, die ausgewählten Historien neu abgefragt und sämtliche Signale neu berechnet.

Die Uhrzeit steht direkt in der BTC-Zeile. So ist sofort sichtbar, dass der Bericht wirklich aus dem aktuellen Fünf-Minuten-Lauf stammt. Trotz frischer Berechnung können einzelne Farbstufen gleich bleiben, wenn sich ein Wert zwar verändert, aber noch innerhalb derselben Signalstufe liegt.

## Beispielausgabe

```text
₿ BTC@14:04 · 4/6 · 24h🟢 · 7d🟡 · vB🟡 · P🟢 · N🟡 · DI/DO
🟢▲ ETH · 8/8 · 24h🟢🟢 · 7d🟢🟢🟢 · vB🟢🟢 · P🟢🟢🟢 · N🟢 · DI/DO/FR
🟢▲ SOL · 7/8 · 24h🟢🟢 · 7d🟢 · vB🟢🟢🟢 · P🟢🟢 · N🟢 · MO/DI/DO
🟡▲ HBAR · 5/8 · 24h🟢 · 7d🟢🟢 · vB🟢 · P🟡 · N🟡 · DI/MI
🔴▼ DOGE · 7/8 · 24h🔴🔴 · 7d🔴 · vB🔴🔴🔴 · P🔴🔴 · N🔴⚠ · SA/SO
🟡▼ ADA · 5/8 · 24h🔴 · 7d🔴🔴 · vB🔴 · P🟡 · N🔴 · MO/SA
```

Es gibt keine Überschriften, Leerzeilen oder Fußzeile. Bei mehr als 2.000 Zeichen teilt das Skript den Bericht automatisch zeilenweise in mehrere Discord-Nachrichten.

## Was ist wirklich frisch?

Bei jedem Lauf:

1. `/coins/map` lädt die aktuellen Werte aller konfigurierten Coins neu.
2. BTC sowie die für diesen Lauf analysierten Coins erhalten jeweils eine neue 42-Tage-Historie.
3. Volumentrend, Kurzbewegung, 24h/7d, Stärke gegen BTC, Druck, aktueller Zeitblock und Wochentage werden vollständig neu berechnet.
4. Es wird keine Ergebnis- oder History-Datei aus einem früheren GitHub-Lauf wiederhergestellt.

Damit der kostenlose LCW-Rahmen bei 288 Läufen täglich nicht überschritten wird, werden pro Lauf maximal 30 Coin-Historien frisch geladen:

- BTC
- alle 16 Coins der ersten Gruppe
- 13 jeweils neu vorselektierte Coins der zweiten Gruppe

Die Vorselektion der zweiten Gruppe wird ebenfalls bei jedem Lauf mit aktuellen LCW-Werten neu erstellt: ungefähr die stärksten BUY- und SELL-Kandidaten. Dadurch kann die Auswahl von Lauf zu Lauf wechseln. Mit dem einen gemeinsamen `/coins/map`-Abruf ergeben sich regulär 31 API-Aufrufe pro Lauf beziehungsweise 8.928 pro Tag.

## Anzeigeumfang

Je Coin-Gruppe werden pro Richtung normalerweise drei bis sechs Coins dargestellt:

- klare Signale zuerst
- danach bei Bedarf die stärksten Beobachtungssignale
- maximal sechs BUY- und sechs SELL-Zeilen pro Gruppe

Falls der Markt in einer Richtung keine ausreichend brauchbaren Kandidaten liefert, werden nicht künstlich drei falsche Empfehlungen erzeugt.

## Farben und Kürzel

### Zeilenanfang

- `🟢▲` = klares BUY-Signal; alle Pflichtfilter sind erfüllt
- `🔴▼` = klares SELL-Signal; alle Pflichtfilter sind erfüllt
- `🟡▲` = stärkster BUY-Beobachtungskandidat, aber noch kein klares Signal
- `🟡▼` = stärkster SELL-Beobachtungskandidat, aber noch kein klares Signal
- `₿` = BTC-Referenz

### X/Y

- Bei Altcoins: `X/8` = erfüllte Bedingungen von acht geprüften Bereichen
- Bei BTC: `X/6` = sechs anwendbare Bedingungen; die beiden BTC-gegen-BTC-Bedingungen entfallen

Die acht Altcoin-Bereiche sind:

1. Kurs 24h
2. Kurs 7d
3. Volumentrend 24h
4. Volumentrend 7d
5. Stärke gegen BTC 24h
6. Stärke gegen BTC 7d
7. Druck `P`
8. aktueller Zeitblock `N`

### Farbstufen

- `🟢` = leicht positiv
- `🟢🟢` = klar positiv
- `🟢🟢🟢` = stark positiv
- `🟡` = neutral
- `🔴` = leicht negativ
- `🔴🔴` = klar negativ
- `🔴🔴🔴` = stark negativ
- `⚪` = nicht ausreichend berechenbar

### Felder

- `24h` = Kursrichtung der letzten 24 Stunden
- `7d` = Kursrichtung der letzten sieben Tage
- `vB` = kombinierte Stärke oder Schwäche gegenüber BTC aus 24h und 7d
- `P` = Kauf-/Verkaufsdruck; berücksichtigt Kurs, frische Volumentrends sowie die jüngste Kurzbewegung
- `N🟢` = aktueller Wochentag-/Vierstundenblock war historisch eher günstig
- `N🟡` = aktueller Zeitblock war historisch neutral
- `N🔴` = aktueller Zeitblock war historisch eher schwach
- `N🔴⚠` = aktueller Zeitblock war historisch besonders schwach oder riskant
- `N⚪` = zu wenig passende Zeitdaten
- `MO/DI/MI/DO/FR/SA/SO` = zwei bis vier historisch stärkste Wochentage

Die Volumentrends werden aus frischen LCW-Historiendaten berechnet, aber aus Platzgründen nicht als eigenes Feld ausgegeben. Sie fließen in `X/8` und `P` ein.

## Update im bestehenden GitHub-Repository

1. ZIP entpacken.
2. Den Inhalt des Ordners `crypto-signal-monitor` in die oberste Ebene des Repositorys hochladen.
3. Vorhandene Dateien überschreiben.
4. **Commit changes** anklicken.
5. Unter **Actions → Crypto Signal Monitor → Run workflow** zuerst mit `send_discord = false` testen.
6. Danach einmal mit `send_discord = true` starten.

Die vorhandenen Secrets bleiben unverändert:

- `LCW_API_KEY`
- `DISCORD_WEBHOOK_URL`

Der Cloudflare Worker und sein Cron-Trigger bleiben unverändert.

### Alte Cache-Dateien

Version 3.0 verwendet sie nicht mehr. Diese alten Dateien können im Repository gelöscht werden, müssen aber nicht gelöscht werden:

- `history_store.py`
- Ordner `state`
- vorhandene GitHub-Actions-Caches mit dem Namen `lcw-history-v2-*`

Die neue `monitor.yml` enthält keine Cache-Schritte mehr.

## Wichtige Einstellungen in `config.json`

```json
{
  "history_days": 42,
  "fresh_history_group_limits": [16, 13],
  "min_per_category": 3,
  "max_per_category": 6,
  "watch_threshold": 4,
  "recommendation_threshold": 6
}
```

- `fresh_history_group_limits`: maximale Zahl frisch historisch analysierter Coins je Gruppe
- `min_per_category`: angestrebte Mindestzahl pro BUY-/SELL-Richtung
- `max_per_category`: maximale Zahl pro BUY-/SELL-Richtung
- `watch_threshold`: Mindestpunktzahl für gelbe Beobachtungszeilen
- `recommendation_threshold`: Mindestpunktzahl für klare grüne/rote Signale; Pflichtfilter gelten zusätzlich

## GitHub Actions und Cloudflare

Der Workflow enthält weiterhin keinen GitHub-Cron. Cloudflare löst `monitor.yml` über `workflow_dispatch` aus.

Für `:04, :09, :14, :19 ... :59`:

```cron
4,9,14,19,24,29,34,39,44,49,54,59 * * * *
```

## Lokaler Test

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
pip install -r requirements.txt
set LCW_API_KEY=DEIN_KEY
python main.py --no-send
```

## Dateien

- `main.py`: frische Datenabrufe, dynamische Vorauswahl und Ablauf
- `analysis.py`: Regeln, Farben, Punkte, Zeit-/Wochentaganalyse und Format
- `lcw_client.py`: Live-Coin-Watch-API ohne Ergebnis-Cache
- `discord_sender.py`: Discord-Versand und Aufteilung langer Berichte
- `config.json`: Coins, Limits und Schwellenwerte
- `.github/workflows/monitor.yml`: externer GitHub-Actions-Start ohne History-Cache
- `cloudflare-worker.js`: unveränderte Scheduler-Kopie
- `tests/test_analysis.py`: lokale Funktionstests
