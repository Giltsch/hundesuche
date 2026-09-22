# Hundesuche

Durchsucht alle 3 Stunden Kleinanzeigen-Portale in Deutschland und den Nachbarländern
(Kleinanzeigen, deine-tierwelt, quoka, markt.de, willhaben, bazos.cz/sk, olx.pl,
marktplaats, subito, jofogas), Tierheim-Fundtierseiten und die Websuche nach einem
gestohlenen Schäferhund-Husky-Rüden. Treffer werden gescored und als Website veröffentlicht.

- **Report:** GitHub Pages dieses Repos
- **Push aufs Handy:** App „ntfy“ installieren → Topic abonnieren (steht im Secret `NTFY_TOPIC`)
- **Manuell starten / tiefer scannen:** Actions → scan → „Run workflow“ → z. B. `pages: 20`

Lokal: `npm ci && python3 scanner.py` (optional `--loop 15`, `--deep`).
