# Crypto Signal Monitor v3.5.0

Alle 3 Minuten: LCW-Gesamtmarkt + geschlossene 1-Minuten-Börsenkerzen. Discord wird bei Änderungen oder spätestens alle 15 Minuten aktualisiert.

Discord-Absender: **CF v3.5.0** mit generischem Crypto-Symbol. Die Versionsnummer steht nicht mehr im Nachrichtentext.

```text
BTC🟢PAY🔵SCP🟢UTL🟣AI🔵MEM🟡:03
🟢6▲7🟢B🟢🔵P🟢V🔵🟢🟢N🟡UWSJUP
```

**Farben:** 🟣 außergewöhnlich stark · 🟢 klar positiv · 🔵 frühe Chance · 🟡 neutral · 🟠 Verkaufswarnung · 🔴 stark negativ · ⚪ fehlt

**Kopfzeile:** BTC · PAY Zahlung · SCP Smart Contracts · UTL Utility/DeFi · AI · MEM Meme

**Coinzeile:** `▲` Kauf · `▼` Verkauf · Zahl 1–8 Stärke · `7` Volumen 7 Tage · `B` relativ zu BTC 24h/7d · `P` Druck · `V` Volumen 10/30/60 Min · `N` Erholung

**Ende:** Kategorie `P/S/U/A/M` + zwei Wochenbereiche: `S` Sa/So · `M` Mo/Di · `W` Mi · `D` Do/Fr · `?` nicht belastbar

Start: Secrets `LCW_API_KEY` und `DISCORD_WEBHOOK_URL`; Cloudflare-Cron `*/3 * * * *`. Alte Cache-Versionen werden nicht übernommen.
