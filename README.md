# Crypto-Signal-Monitor v3.2.7

`🟣/🟢/🔵/🟡/🟠/🔴` = stark positiv bis stark negativ · `🟤` unsicher · `⚪` fehlend

Zeile: `●X▲/▼7●B●P●V●●●N●[Tage][Coin/Minute]`

- Anfang: bestätigte Nähe zu Akkumulation oder Distribution
- `X`: bestätigte Kriterien · `▲/▼/=` Richtung
- `7`: 7-Tage-Kontext relativ zur eigenen Historie
- `B`: Kurzfriststärke gegenüber BTC; bei BTC bestätigte Eigenstärke
- `P`: Kauf-/Verkaufsdruck aus Kurs + Volumen
- `V`: Volumentrend 10/20/60 Minuten
- `N`: konservative Gesamtlage
- `SA…FR`: höchstens zwei robuste positive Wochentage

Wochentage werden einmal täglich aus bis zu 300 abgeschlossenen Tagen berechnet. Junge Coins nutzen automatisch nur ihre vorhandene Teilhistorie; leere Zeiträume vor dem Listing sind kein Fehler. Kurzzeitwerte werden bei jedem Lauf frisch geladen. Die API-Steuerung erlaubt schnelle normale Läufe, begrenzt aber längere Tagesläufe sicher als gleitendes Request-Fenster.
