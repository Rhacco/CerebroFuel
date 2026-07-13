# Krypto-Signal-Monitor – Version 3.1

Version 3.1 zeigt immer genau **BTC plus die acht aktuell auffälligsten Coins** aus dem gesamten konfigurierten Pool. Steigende und fallende Bewegungen werden gemeinsam nach ihrer Auffälligkeit sortiert. Es gibt keine Überschriften, Leerzeilen oder Fußzeilen in Discord.

## Beispiel

```text
₿14:01 🟢 · 6/7▲ · 7d🔵 · P🟢 · V🟢🟣🟢 · N🟡 · DI/DO
🟣 ETH · 7/8▲ · 7d🟢 · vB🟢 · P🟣 · V🟣🟢🔵 · N🟡 · DI/DO/FR
🔴 DOGE · 6/8▼ · 7d🟠 · vB🔴 · P🔴 · V🟢🟢🔵 · N🟠 · SA/SO
```

Die Reihenfolge bei `V` ist immer **10 / 20 / 60 Minuten**.

## Bedeutung der Darstellung

- `₿14:01`: BTC-Referenz und deutsche Ortszeit des Berichts; das Wort `BTC` wird nicht zusätzlich ausgeschrieben.
- Kreis direkt nach der BTC-Uhrzeit:
  - `🟢`: BTC hatte über 10/20/60 Minuten keinen relevanten Einbruch **und** alle drei Volumentrends sind eindeutig steigend.
  - `⚫`: mindestens eine dieser Bedingungen fehlt. Das ist bewusst ein strenger Sammelschalter.
- `X/8▲` oder `X/8▼`: erfüllte positive beziehungsweise negative Kurzfristbedingungen. Bei BTC sind es `X/7`, weil ein Vergleich von BTC mit sich selbst entfällt.
- `7d`: Kursrichtung der letzten sieben Tage.
- `vB`: gewichtete relative Kursstärke des Coins gegenüber BTC über 10/20/60 Minuten.
- `P`: kurzfristiger Druck aus Kursbewegung und Volumenbestätigung über 10/20/60 Minuten.
- `V🟣🟢🔵`: Veränderung des von LCW gemeldeten **rollierenden 24h-Volumens** gegenüber vor 10/20/60 Minuten. Es ist ein sehr aktueller Volumentrend, aber nicht exakt das ausschließlich in diesem Intervall gehandelte Volumen.
- `N`: historische Bewertung des aktuellen Wochentag-/Vierstundenblocks.
- `MO/DI/MI/DO/FR/SA/SO`: zwei bis vier historisch stärkste Wochentage.

## Farben

- `🟣` außergewöhnlich stark beziehungsweise deutliche positive Volumenspitze
- `🟢` klar positiv
- `🔵` leicht positiv
- `🟡` neutral oder gemischt
- `🟠` nachlassend beziehungsweise negative Warnstufe
- `🔴` klar negativ
- `🟤` unsichere Datenbasis
- `⚪` keine ausreichenden Daten
- `⚫` nur in der BTC-Zeile: strenger BTC-Sammelschalter nicht erfüllt

### Warum kann `🟤` erscheinen?

`🟤` wird verwendet, wenn Werte vorhanden sind, ihre Aussagekraft aber eingeschränkt ist. Typische Gründe:

- das aktuelle 24h-Volumen liegt unter dem in `config.json` gesetzten Mindestwert;
- ein Volumenwert ist null, unplausibel oder zeigt einen extremen Sprung, der eher auf einen LCW-/Symbolwechsel oder eine Datenstörung hindeutet;
- der Coin ist so wenig liquide, dass kleine Einzelbewegungen das Ergebnis stark verzerren können.

Der Coin kann trotzdem unter den Top 8 erscheinen, wird bei der Sortierung aber abgewertet.

### Warum kann `⚪` erscheinen?

`⚪` bedeutet, dass keine belastbare Berechnung möglich war. Typische Gründe:

- in den ersten 10, 20 oder 60 Minuten nach der Einrichtung existiert der benötigte ältere Snapshot noch nicht;
- ein Cloudflare-Cron-Lauf oder LCW-Abruf ist ausgefallen;
- `CF_STATE_URL` oder `CF_STATE_KEY` fehlt beziehungsweise ist falsch;
- LCW hat für diesen Coin oder Zeitpunkt keinen passenden Wert geliefert.

Der Cloudflare Worker sammelt auch bei `10 Min`, `30 Min` oder `Pause` weiterhin alle fünf Minuten Snapshots. Dadurch bleiben V10/20/60 aktuell.

## Auswahl der acht Coins

Alle Coins des bisherigen Pools werden bei jedem GitHub-Lauf frisch über `/coins/map` geladen. Die Auffälligkeit berücksichtigt insbesondere:

- absolute Kursbewegungen über 10/20/60 Minuten;
- Stärke und Veränderung des Volumentrends über 10/20/60 Minuten;
- kurzfristige Abweichung von BTC;
- bestätigten positiven oder negativen Druck;
- ergänzend 7-Tage-Bewegung.

Danach werden exakt die acht höchsten Auffälligkeitswerte gewählt. Nur falls LCW weniger als acht Pool-Coins auflösen kann, erscheinen entsprechend weniger.

## Cloudflare-Worker aktualisieren

### 1. KV-Namespace anlegen und verbinden

Im Cloudflare-Dashboard:

```text
Workers & Pages → KV → Create namespace
```

Beispielname: `crypto-scheduler-state`.

Danach beim Worker:

```text
Settings → Bindings → Add binding → KV Namespace
Variable name: SCHEDULER_KV
KV namespace: crypto-scheduler-state
```

Der Variablenname muss exakt `SCHEDULER_KV` sein.

### 2. Worker-Secrets

Unter `Settings → Variables and Secrets`:

- vorhandene Werte behalten: `GH_OWNER`, `GH_REPO`, `GH_REF`, `GH_WORKFLOW`, `GH_PAT`, `TEST_KEY`;
- zusätzlich als Secret eintragen: `LCW_API_KEY`;
- optional getrennte Schlüssel: `CONTROL_KEY` und `STATE_KEY`.

Wenn `CONTROL_KEY` oder `STATE_KEY` fehlen, verwendet der Worker automatisch den vorhandenen `TEST_KEY`.

### 3. Worker-Code ersetzen

Den Inhalt von `cloudflare-worker.js` vollständig in den Cloudflare-Editor kopieren und **Deploy** drücken.

### 4. Cron ändern

Den alten Cron-Trigger löschen und diesen eintragen:

```cron
1,6,11,16,21,26,31,36,41,46,51,56 * * * *
```

Damit läuft der Basis-Worker immer um `:01, :06, :11, :16 …`. Cloudflare-Cron verwendet UTC; für die Minutenfolge ist das unerheblich. Änderungen können einige Minuten benötigen, bis sie aktiv sind.

## Steuerungsseite

Aufrufen:

```text
https://DEIN-WORKER.DEIN-SUBDOMAIN.workers.dev/control?key=DEIN_TEST_KEY
```

Schaltflächen:

- `5 Min`: GitHub um `:01, :06, :11, :16 …`
- `10 Min`: GitHub um `:01, :11, :21, :31, :41, :51`
- `30 Min`: GitHub um `:01` und `:31`
- `Pause`: keine automatischen GitHub-Läufe; Snapshots werden weiterhin gesammelt
- `Jetzt starten`: sofort Snapshot erfassen und GitHub starten

Die Auswahl wird in Workers KV gespeichert und bleibt nach Deployments erhalten.

## Zwei neue GitHub-Secrets

Im GitHub-Repository:

```text
Settings → Secrets and variables → Actions → New repository secret
```

Eintragen:

- `CF_STATE_URL` = `https://DEIN-WORKER.DEIN-SUBDOMAIN.workers.dev/state`
- `CF_STATE_KEY` = dein `STATE_KEY`; falls nicht separat angelegt, derselbe Wert wie `TEST_KEY`

Die bestehenden Secrets bleiben:

- `LCW_API_KEY`
- `DISCORD_WEBHOOK_URL`

## Repository aktualisieren

1. ZIP entpacken.
2. Den Inhalt des Ordners `crypto-signal-monitor` in die oberste Ebene des Repositorys hochladen.
3. Vorhandene Dateien überschreiben und die neuen Dateien `state_client.py` und `wrangler.jsonc.example` mit hochladen.
4. Änderungen committen.
5. Worker und GitHub-Secrets wie oben einrichten.
6. Über die Cloudflare-Steuerungsseite `Jetzt starten` testen.

Nach der ersten Einrichtung füllt sich die Volumenanzeige schrittweise:

- nach ungefähr 10 Minuten: V10 verfügbar;
- nach ungefähr 20 Minuten: V10 und V20 verfügbar;
- nach ungefähr 60 Minuten: alle drei Werte verfügbar.

## Konfigurierbare Schwellen

In `config.json` lassen sich insbesondere ändern:

- `top_coin_count`: aktuell 8;
- `minimum_reliable_volume_usd`: Grenze für `🟤`;
- `price`: Schwellen je 10/20/60 Minuten;
- `volume`: Schwellen je 10/20/60 Minuten;
- `btc_no_drop_pct`: maximal tolerierter BTC-Rückgang für den grünen BTC-Sammelschalter.

## Technischer Abrufumfang

Der Worker benötigt pro fünf Minuten einen gemeinsamen `/coins/map`-Abruf für den gesamten Pool. Jeder tatsächlich gestartete GitHub-Lauf benötigt einen weiteren gemeinsamen `/coins/map`-Abruf sowie frische Historien nur für BTC und die acht ausgewählten Coins. Dadurch bleibt die Zahl der API-Aufrufe deutlich niedriger als bei einer Historienabfrage für den gesamten Pool.
