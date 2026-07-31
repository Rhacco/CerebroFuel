# CF v3.7.1

Lighter-Monitor mit kompakten **T/W-Signalen** und zustandsfestem Paper-Trading.
Das frühere eigenständige P-Setup ist deaktiviert; Quote-Volumen bestätigt nur
noch eine bereits aus Preisstruktur erkannte Trend- oder Wenderichtung.

```text
PMP🔵UNI🔵HYP🔴AAV🟢GRM🟢ETH🟣:43
🟣▲ 5🟢20🟢60🟢 V🟢L🟢B🟢K🟢F🟢 ETHT
🟢▼ 5🔴20🔴60🟡 V🟢L🟢B🟡K🟢F🟢 HYPW
ETHL:2$20x HYPS:1$10x
```

Oben stehen stets die Top-6 aufsteigend; die beste Chance liegt direkt vor der
Berliner Minute. Darunter erscheinen die Top-2 immer, Rang 3 nur nahe an einer
Freigabe und Rang 4 ausschließlich bei vier vollständigen Spitzensignalen.

- `🟣▲/▼` frischer Soforteinstieg
- `🟢▲` / `🔴▼` starke Richtung
- `🔵▲` / `🟠▼` Aufbau
- `🟡▲/▼` schwache Tendenz · `⚫?` unsichere Daten
- `T` Trend-Rücksetzer/Fortsetzung · `W` bestätigte Schockwende
- `5/20/60` Impuls, Bestätigung und Kontext
- `V/L/B/K/F` Volumen, Liquidität/OI, BTC-Breite, Kosten und Funding

Aktiver Kernpool: `BTC ETH SOL HYPE GRAM UNI AAVE PUMP ADA XPL`.
Testpool: `NEAR DOGE KPEPE KBONK WIF`; diese Märkte erhalten 4-8
Qualitätspunkte Abzug und dürfen erst ab mindestens `84` Readiness und `80`
Confidence eine Sofortfreigabe erzeugen. Anzeigen: `NER DGE PEP BNK WIF`.

Das Paper-Konto startet mit `100$`, führt höchstens drei Positionen und wählt
den Hebel zwischen `10x` und `50x` anhand von Signalqualität, Stop-Abstand,
Marktbreite, Kosten und Lighter-Markthebel. Alte offene P-Paperpositionen aus
v3.7 werden beim ersten v3.7.1-Lauf kontrolliert geschlossen. Dasselbe gilt
für offene Positionen in entfernten Coins; Kontostand und übriger Verlauf werden
ohne Reset übernommen.

```bash
python main.py --no-send
```

Die weiterhin relevanten Analyse-, Kontext- und Zustandsmodule aus v3.7 bleiben
unverändert erhalten. Der nicht verwendete eigenständige `lcw_client.py` wurde
entfernt; Marktdaten stammen im aktiven Lauf direkt von Lighter.

GitHub benötigt `DISCORD_WEBHOOK_URL`. Der Worker ruft `monitor.yml` minütlich
auf; Cron: `* * * * *`.
