# Juanjo International Television Hub

This project preserves the exact 33-channel lineup approved on 2026-08-24 and separately builds the approved 43-channel Mac-free expansion from 2026-08-25. Both registries are approval-locked: adding, removing, disabling, or reordering a channel without updating its approved ID list makes validation fail.

The default registry produces the original 33-channel playlist and legacy static 28. The overlay in `config/channels-static-expanded.json` preserves the base registry while producing the fully static 43-channel UHF playlist. Public broadcaster streams are used directly where possible. Every mirror, FAST feed, and named distributor remains labeled in the registry, M3U metadata, and health reports.

## Build and live-check

From `/Users/juanjoguzmanc/Documents/ChatGPT/UHF`:

```sh
python3 international-tv-hub/scripts/build_hub.py \
  --check \
  --network-route DIRECT \
  --network-label "Peru direct" \
  --verify-egress-country
```

The live check validates the top-level HLS manifest, selects the highest-bandwidth media playlist, and retrieves bytes from a current media segment. The build always keeps the approved 33 in the main playlist unless `--exclude-unhealthy` is explicitly requested.

Outputs:

- `generated/uhf-international.m3u` — all 33 approved channels.
- `generated/uhf-static-28.m3u` — the exact 28 approved direct-play channels; no Mac-side resolver.
- `generated/uhf-expanded-43.m3u` — combined approved Mac-free expansion.
- `generated/uhf-static-43.m3u` — hosted UHF source; all 43 entries are direct HLS and VPN-required names are visibly flagged.
- `generated/fr-french.m3u` — French-language core.
- `generated/intl-global.m3u` — global news and markets.
- `generated/cee-eastern-europe.m3u` — Central/Eastern Europe and Russian opposition sources.
- `generated/eu-national.m3u` — Spain, Austria, and Italy.
- `generated/benelux-news.m3u` — Belgium, Netherlands, and Luxembourg.
- `generated/de-ch-public.m3u` — Germany and Switzerland.
- `reports/source-health.md` — per-channel validation chain, source disclosure, and VPN route.

## Runtime player resolvers

Four approved channels use official dynamic player chains implemented in the local server:

- LN24: official Rise player → current Infomaniak HLS, requiring NordVPN Belgium.
- NPO: anonymous NPO Start token → official NPO Player `stream-link` → current HLS, requiring NordVPN Netherlands.
- RTVE Canal 24 Horas: official ZTNR player → Golumi live CDN through a host-restricted, header-preserving local HLS proxy, requiring NordVPN Spain.
- RTS Info: official SRG SSR media composition for `urn:rts:video:1967124` → current Akamai HLS-DVR, requiring NordVPN Switzerland.

The generated M3U uses relative `resolve/...` paths for those four channels. `serve_hub.py` rewrites them to the Mac's current LAN address. It returns a fresh redirect for ordinary resolvers and proxies RTVE's HLS chain because that broadcaster requires its official browser headers on every playlist and segment request.

```sh
python3 international-tv-hub/scripts/serve_hub.py
```

Keep the Mac on the route required by a dynamic resolver while starting that channel. The Apple TV should use the same NordVPN country for the resulting media requests.

## Preserved disclosures

- TVP World — `Working mirror` (lowa8026-cmyk GitHub; Antik fallback).
- TVP Info — `Working mirror` (lowa8026-cmyk GitHub; Antik fallback).
- ČT24 — `Named distributor` (Antik Telecom).
- Belsat / Vot Tak / Slawa — `Working distributor` (Prosto TV).
- Yahoo Finance — `FAST partner` (working CloudFront feed; Amagi/Plex fallbacks).

These labels are not cosmetic: tests assert that mirror/distributor labels remain in the generated playlist.

## Tests

```sh
python3 -m unittest discover -s international-tv-hub/tests -v
```

See `docs/UHF_SETUP.md` for Apple TV setup and `docs/VALIDATION.md` for the route-by-route validation commands.

For the Mac-free hosted method, see `docs/UHF_STATIC_SETUP.md`. The GitHub Pages workflow publishes `uhf-static-43.m3u` as the primary UHF source and retains `uhf-static-28.m3u` only as a rollback file.
