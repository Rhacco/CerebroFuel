# Crypto Signal Monitor v3.8.0

Lighter-native Marktanalyse mit Discord-Ausgabe und dauerhaftem Paper-Trading.

## Aktiver Marktpool

`BTC, ETH, SOL, HYPE, ADA`

Der Pool ist bewusst auf liquide Perpetual-Märkte mit belastbarerem Open Interest, engerer Ausführung und gleichmäßigerem Ein-Minuten-Volumen reduziert. Zusätzlich blockiert eine dynamische Tape-Qualitätsprüfung lückenhafte oder von einzelnen Ausreißern dominierte Verläufe.

## Signale

- `E` – frühe Expansion nach Kompression; nur solange wenig vom erwarteten Weg verbraucht ist
- `T` – bestätigte Trendfortsetzung nach Rücksetzer
- `W` – bestätigte Wende nach außergewöhnlichem Schock

Abgelaufene oder bereits weit gelaufene Chancen werden nicht als Einstieg angezeigt.

## Discord

- Kopfzeile: Top 5, ohne lila Sofortfarbe
- Detailzeilen: BTC mindestens einmal, außer 2–4 andere Märkte sind klar auffälliger
- grüne Standardprüfungen bleiben unsichtbar; nur Warnungen wie `V!`, `L!`, `K!`, `B!`, `F!` erscheinen
- unveränderte Berichte werden unterdrückt; Heartbeat standardmäßig alle 15 Minuten
- Paper-Aktionen werden sofort gesendet

## Paper-Trading

Starke Signale dürfen als kleine 1-$-Probe starten. Sofortsignale können größer eröffnet oder eine Probe ausbauen. Bis zu drei Märkte können gleichzeitig laufen; Positions-, Gesamtmargin-, Richtungs- und Stop-Risikolimits bleiben aktiv.

## Start

```bash
python main.py --no-send
```

Für Discord `DISCORD_WEBHOOK_URL` setzen. Laufzeitdateien liegen in `output/`.
