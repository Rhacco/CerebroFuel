# Krypto-Monitor v3.3.3 – Discord-Legende

Ausgabe: **1× BTC-Marktqualität + 8 stärkste Kaufchancen oder Verkaufswarnungen**.

```text
[Signal][0–8][▲/▼/=]7[7D-Vol.]B[24H][7D]P[Druck]V[10][30][60]N[Erholung][Wochentage][Coin]
```

Bei BTC steht am Ende statt des Coin-Kürzels die Laufminute, z. B. `:01`.

## Farben

| Farbe | Bedeutung |
|---|---|
| 🟣 | außergewöhnlich stark positiv / sehr gute Kaufchance |
| 🟢 | klar positiv |
| 🔵 | früher oder moderater positiver Hinweis |
| 🟡 | neutral / derzeit kein klares Signal |
| 🟠 | Verkaufs- oder Risikowarnung |
| 🔴 | dringendes negatives Signal |
| 🟤 | Daten unsicher oder teilweise unzuverlässig |
| ⚪ | Daten fehlen |

Bei der **BTC-Zeile** beschreibt der erste Kreis die aktuelle Marktqualität. Bei den **Top 8** beschreibt er die Stärke der Kaufchance oder Verkaufswarnung.

## Discord-Kürzel

| Kürzel | Bedeutung |
|---|---|
| `0–8` | Signalstärke; keine Prozentangabe |
| `▲` | Kaufchance / positiver Lauf |
| `▼` | Verkaufswarnung / negativer Lauf |
| `=` | kein klarer Ausschlag |
| `7` | Volumentrend über 7 Tage |
| `B••` | Kurs relativ zu BTC über 24 Stunden und 7 Tage; bei BTC absolute Entwicklung |
| `P•` | aktueller Kauf- oder Verkaufsdruck |
| `V•••` | Volumenentwicklung über 10, 30 und 60 Minuten |
| `N•` | Stabilisierung oder Erholung nach einem Rückgang |
| `MO–SO` | bis zu zwei historisch beste Wochentage |

`V` verwendet bevorzugt echte 5-Minuten-Börsenkerzen; ohne passendes Börsenpaar dient die LCW-Volumenentwicklung als Fallback.

## Weniger offensichtliche Coin-Kürzel

| Discord | Coin | Discord | Coin |
|---|---|---|---|
| `ARK` | Arkham | `MND` | Monad |
| `BOM` | BOOK OF MEME | `GAL` | Gala |
| `ORD` | Ordinals | `KAI` | Kaito |
| `MOO` | Moo Deng | `PNT` | Peanut the Squirrel |
| `MEG` | MegaETH | `WRM` | Wormhole |
| `XPL` | Plasma | `AAV` | Aave |
| `OPT` | Optimism | `TRP` | OFFICIAL TRUMP |
| `PEP` | Pepe | `BNK` | Bonk |
| `DGE` | Dogecoin | `ION` | IO.NET |
| `SNC` | Sonic | `ATH` | Aethir |
| `SSH` | SushiSwap | `FRT` | Fartcoin |
| `ZET` | ZetaChain | `HYP` | Hyperliquid |
| `AVX` | Avalanche | `NER` | NEAR Protocol |
| `OND` | Ondo | `MRP` | Morpho |
| `PGU` | Pudgy Penguins | `EFI` | ether.fi |
| `PYT` | Pyth Network | `FLK` | FLOKI |
| `ZKS` | zkSync | `KMN` | Kamino |
| `RND` | Render | `BIO` | Bio Protocol |

Alle anderen Coin-Kürzel entsprechen weitgehend ihrem üblichen Symbol, zum Beispiel `ETH`, `SOL`, `SUI`, `TAO`, `JTO`, `TIA`, `WLD`, `SEI`, `RAY`, `ARB` und `INJ`.
