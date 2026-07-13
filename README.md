# Crypto Signal Monitor V3.2.2

Direkter Ersatz für V3.2.1 auf dem zuverlässig laufenden V3.0-Aufbau:

- kein Cloudflare KV und kein `TEST_KEY`
- alle aktuellen Werte und Historien bei jedem Lauf frisch von Live Coin Watch
- immer eine feste BTC-Referenzzeile plus acht auffälligste Coins
- BTC-Zeile beginnt immer mit exakt zwei gleichen Kreisen
- Coin-Zeilen strikt nach `X/8` absteigend sortiert
- pro Zeile genau die zwei positivsten Wochentage aus Kurs und Volumen
- kompakte Discord-Ausgabe ohne Abstandhalter oder Leerzeilen

## Discord-Ausgabe

```text
🟢🟢:01 6/7▲7🔵B🟢P🟢V🟢🟣🟢N🟣SADI
🟣ETH8/8▲7🟢B🟢P🟣V🟣🟢🔵N🟢SOMO
🟢SOL7/8▲7🔵B🟢P🟢V🟢🔵🟡N🔵SAMI
🔴DGE6/8▼7🟠B🔴P🔴V🟢🟢🔵N🔴SASO
```

Die BTC-Zeile bleibt immer oben. Danach folgt die Sortierung ausschließlich zuerst nach der sichtbaren Sicherheit `X/8`: `8/8`, `7/8`, `6/8` usw. Gleichstände werden nach Datenqualität, Richtungsdeutlichkeit und Auffälligkeit aufgelöst.

## Neue N-Logik

`N` ist jetzt ein aktuelles Nachfrage-/Aktivitätssignal. Es wird hauptsächlich aus den frischen Kurs- und Volumenänderungen über 10, 20 und 60 Minuten berechnet. Die historische Qualität des aktuellen Zeitfensters wirkt nur noch als kleiner Bestätigungsfaktor.

- Kurs und Volumen steigen gemeinsam klar: positiv
- Kurs bleibt stabil und Volumen steigt stark: besonders positiv, häufig `🟣`
- Kurs steigt bei sinkendem Volumen: nur schwach positiv oder gemischt
- Kurs fällt bei steigendem Volumen: bestätigter Verkaufsdruck, häufig `🔴`
- Kurs und Volumen fallen gemeinsam: nachlassende Nachfrage, `🟠` bis `🔴`
- widersprüchliche Fenster: `🟡`

Damit wird `N` nicht mehr hauptsächlich aus einem historischen Wochentag abgeleitet und sollte deutlich häufiger sinnvoll zwischen positiv, neutral und negativ unterscheiden.

## Wochentage

Am Ende jeder Zeile stehen immer die zwei stärksten Wochentage, soweit mindestens zwei brauchbar sind. Die Bewertung kombiniert:

- Kursbewegung
- Veränderung des LCW-Volumenwerts
- Zuverlässigkeit und Trefferquote der Beobachtungen

Stabiler Kurs mit deutlich steigendem Volumen zählt positiv. Fallender Kurs bei steigendem Volumen zählt deutlich negativ. Nach der Auswahl werden die zwei Tage chronologisch angezeigt, wobei die Reihenfolge mit Samstag beginnt:

```text
SA → SO → MO → DI → MI → DO → FR
```

Beispiele: `SADI`, `SASO`, `MIDO`.

## Kürzel

- Anfangskreis: Gesamtsignal des Coins
- zwei Anfangskreise in Zeile 1: BTC-Gesamtbedingung, immer exakt doppelt
- `:01`: Minute der Analyse
- `6/7▲`: sechs von sieben positiven BTC-Bedingungen erfüllt
- `8/8▲` oder `7/8▼`: erfüllte positive oder negative Coin-Bedingungen
- `7`: Kursrichtung über sieben Tage
- `B`: kurzfristige Stärke gegenüber BTC; bei BTC die Markt-Gesamtbedingung
- `P`: kombinierter Kursdruck mit Volumenbestätigung
- `V`: Volumentrend in der Reihenfolge 10/20/60 Minuten
- `N`: aktuelle Nachfrage/Aktivität aus Kurs plus Volumen
- `SADI`: zwei positivste Wochentage

## Farben

- `🟣`: außergewöhnlich stark
- `🟢`: klar positiv
- `🔵`: leicht positiv
- `🟡`: neutral oder gemischt
- `🟠`: nachlassend oder negative Warnung
- `🔴`: klar negativ
- `🟤`: Daten vorhanden, aber unzuverlässig
- `⚪`: zu wenige frische Vergleichsdaten
- `⚫`: BTC-Gesamtbedingung nicht erfüllt

`🟤` kann bei zu geringem Volumen, zu wenigen Historienpunkten oder unplausiblen Datensprüngen entstehen. `⚪` erscheint nur, wenn für mindestens zwei der Zeitfenster kein brauchbarer Vergleichspunkt vorhanden ist.

## GitHub aktualisieren

1. ZIP entpacken.
2. Den Inhalt des Ordners in die oberste Ebene des Repositorys hochladen.
3. Vorhandene Dateien überschreiben und committen.
4. GitHub-Secrets behalten:

```text
LCW_API_KEY
DISCORD_WEBHOOK_URL
```

5. Manuell testen:

```text
Actions → Crypto Signal Monitor → Run workflow
send_discord: false
```

Danach mit `send_discord: true` testen.

## Cloudflare Worker

Den Inhalt von `cloudflare-worker.js` in den vorhandenen Worker kopieren und deployen.

Normale Variablen:

```text
GH_OWNER
GH_REPO
GH_REF
GH_WORKFLOW
ENABLED = 1
```

Secret:

```text
GH_PAT
```

Steuerung:

```text
ENABLED = 1  → aktiv
ENABLED = 2  → pausiert
```

Cron alle fünf Minuten:

```cron
1,6,11,16,21,26,31,36,41,46,51,56 * * * *
```

## Lokaler Test

```bash
python -m unittest discover -s tests -v
python main.py --no-send
```
