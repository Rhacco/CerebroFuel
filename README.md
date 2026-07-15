# Crypto-Signal-Monitor v3.2.7

`🟣/🟢/🔵/🟡/🟠/🔴` = stark positiv bis stark negativ · `🟤` unsicher · `⚪` fehlend

Zeile: `●X▲/▼7●B●P●V●●●N●[Tage][Coin/Minute]`

- Anfang: Nähe zu Akkumulation/Distribution, zeitlich bestätigt
- `X`: erfüllte Bestätigungen · `▲/▼/=` Richtung
- `7`: 7-Tage-Kontext relativ zur eigenen Historie
- `B`: Kurzfriststärke gegenüber BTC; bei BTC eigene bestätigte Stärke
- `P`: Kauf-/Verkaufsdruck aus Kurs + Volumen
- `V`: Volumentrend 10/20/60 Minuten
- `N`: konservative Gesamtlage
- `SA…FR`: höchstens zwei positive Wochentage

Wochentage: einmal täglich aus dichter, in 100-Tage-Blöcken geladener Langzeithistorie; danach Tagescache. Kurzzeitdaten: alle fünf Minuten frisch. API-Aufrufe werden seriell begrenzt, damit LCW nicht durch Anfragebursts drosselt.
