# Krypto-Monitor v3.3.0 – Volume Priority

`Kreis Zahl Richtung 7 B24 B7 P V10/V30/V60 N Tage Coin/Minute`

## Prioritäten

1. **Primärsignal und Rangfolge:** Größe der 30-Minuten-Schere `Volumentrend − Kurstrend`. Je größer der absolute Abstand, desto höher die Aufmerksamkeit. Die Richtung der Schere bestimmt `▲/▼` und den Anfangskreis.
2. **7:** positiver Trend des rollierenden LCW-Handelsvolumens über sieben Tage. Er wird aus dem bestehenden Tagescache berechnet und gibt höchstens 14 Bonuspunkte.
3. **Market Cap:** kleinere Marktkapitalisierung gibt höchstens 10 Bonuspunkte. Ein Liquiditätsfaktor verhindert, dass nahezu inaktive Micro-Caps nur wegen ihrer Größe gewinnen.
4. **Volatilität:** hohe jüngste realisierte Kursbewegung gibt höchstens 8 Bonuspunkte.
5. **N:** eigenständiges Crash-Recovery-Signal. Ein kürzlicher Drawdown, anschließende Kursstabilisierung/Rebound und steigender Volumentrend ergeben Blau, Grün oder Lila. Ein weiterlaufender Crash kann Orange/Rot ergeben.

## Kreise

- `V10/V30/V60`: ausschließlich kurzfristiger, mittlerer und längerer Trend des rollierenden LCW-24h-Volumens.
- `7`: Sieben-Tage-Volumentrend, nicht mehr der alte Sieben-Tage-Kurskreis.
- `B24 B7`: Kursperformance gegenüber BTC über 24 Stunden und sieben Tage. In der BTC-Zeile zeigen die Kreise BTC absolut.
- `P`: sekundärer Preis-/Volumendruck zur Diagnose; er entscheidet nicht mehr über die Rangfolge.
- `N`: Crash-Stabilisierung mit Volumenunterstützung.
- Tage: täglich eingefrorene Top-Wochentage aus vollständigen, BTC- und Pool-bereinigten Wochen.

## Vollpool und Credits

Alle konfigurierten Coins werden mit einer gemeinsamen LCW-Map-Abfrage und den gespeicherten Fünf-Minuten-Snapshots über `10/30/60m` geprüft. Danach werden regulär die 25 stärksten Kandidaten per Historie bestätigt; bei Datenlücken gilt ein harter Deckel von 26 Altcoin-Detailabrufen.

- regulär: `1 Map + BTC + 25 Coin-Historien ≈ 27 Requests`
- maximal: `1 Map + BTC + 26 Coin-Historien ≈ 28 Requests`
- bei 288 Fünf-Minuten-Läufen: regulär etwa `7.776`, maximal etwa `8.064` Monitor-Requests pro Tag, zuzüglich der einmaligen Tagesaktualisierung und seltener Retries

Der vorhandene v3.3.0-Tagescache bleibt kompatibel. Der neue Flash-Cache lädt bei der ersten Ausführung auch `flash-v329-` als Migrationsquelle. Cloudflare-Konfiguration und Secrets bleiben unverändert.
