# Crypto Signal Monitor V3.2.3

Alle 5 Minuten: frische Live-Coin-Watch-Daten → BTC + 8 auffälligste Coins → kompakte Discord-Nachricht. Keine KI, kein Cache, kein Cloudflare KV.

```text
🟢 :01 6▲7🔵B🟢P🟣V🟣🟢🔵N🟣SADI
🟣W      8▲7🟢B🟢P🟣V🟣🟢🟢N🟣SADI
🔴WIF7▼7🟠B🔴P🔴V🟠🔴🔴N🔴SASO
```

## Logik

- `X▲/▼`: erfüllte BUY-/SELL-Bedingungen; Coins maximal 8, BTC maximal 7.
- 10/20/60 Min. werden getrennt bewertet: Kurs + Volumen, Übereinstimmung, BTC-Vergleich, `P`, `N`, 7-Tage-Lage.
- **Akkumulation:** Kurs stabil/leicht steigend + Volumen deutlich stärker steigend → früh stark positiv.
- **Distribution:** Kurs steigt schneller als Volumen oder Kurs fällt bei steigendem Volumen → früh stark negativ.
- Sortierung: zuerst höchste sichtbare Sicherheit, dann stärkste Kurs-/Volumen-Eindeutigkeit.
- Nur ein fehlerhaftes Zeitfenster wird braun; die übrigen Fenster bleiben nutzbar. Grund steht im GitHub-Log.
- Schluss: zwei positivste Wochentage aus Kurs + Volumen, ausgewählt und ab `SA` chronologisch angezeigt.

## Kürzel

| Zeichen | Bedeutung |
|---|---|
| Anfangskreis | Gesamtsignal |
| `:01` | Analyseminute |
| `7` | 7-Tage-Kurslage |
| `B` | relative Stärke zu BTC; bei BTC Marktfreigabe |
| `P` | aktueller Kurs-/Volumendruck |
| `V` | Volumentrend 10/20/60 Min. |
| `N` | aktuelle Nachfrage/Distribution aus Kurs + Volumen |
| `SADI` | stärkste Tage Samstag + Dienstag |

## Farben

`🟣` außergewöhnlich stark · `🟢` klar positiv · `🔵` leicht positiv · `🟡` neutral/gemischt · `🟠` Warnung/nachlassend · `🔴` klar negativ · `🟤` einzelnes unsicheres Datenfenster · `⚪` Vergleichsdaten fehlen · `⚫` BTC-Marktfreigabe nicht erfüllt

Feste Kürzel u. a.: `NER` Near · `HBR` HBAR · `DGE` Dogecoin · `RND` Render · `ZKS` zkSync · `EFI` ETHFI · `MRP` Morpho. Codes unter 3 Zeichen erhalten je fehlendem Zeichen drei Leerzeichen (`OP` + 3, `W` + 6).

## Betrieb

GitHub-Secrets: `LCW_API_KEY`, `DISCORD_WEBHOOK_URL`  
Cloudflare: `GH_OWNER`, `GH_REPO`, `GH_REF`, `GH_WORKFLOW`, `ENABLED=1`; Secret `GH_PAT`  
Pause: `ENABLED=2` · Cron: `1,6,11,16,21,26,31,36,41,46,51,56 * * * *`

Upload: ZIP-Inhalt ins Repository kopieren, überschreiben, committen, danach `Actions → Crypto Signal Monitor → Run workflow` testen.

