# UHF and NordVPN setup

## 1. Build the approved playlist

From `/Users/juanjoguzmanc/Documents/ChatGPT/UHF`:

```sh
python3 international-tv-hub/scripts/build_hub.py \
  --check \
  --network-route DIRECT \
  --network-label "Peru direct" \
  --verify-egress-country
```

This generates `international-tv-hub/generated/uhf-international.m3u` with exactly 33 channels. A registry mismatch fails before anything is published.

## 2. Serve it to Apple TV

```sh
python3 international-tv-hub/scripts/serve_hub.py
```

Use the URL labeled `en0` or `en1`, normally:

```text
http://192.168.1.x:8765/uhf-international.m3u
```

Do not use the VPN tunnel address. The server replaces the two relative runtime-resolver URLs with this same LAN address before sending the playlist to UHF.

## 3. Add it in UHF

1. On Apple TV, open UHF and add an M3U playlist by URL.
2. Name it `Juanjo International TV`.
3. Enter the LAN URL printed by `serve_hub.py`.
4. Leave XMLTV/EPG blank for the first import.
5. Refresh and confirm that UHF reports 33 channels.

The six groups are `FR | French`, `INTL | Global`, `CEE | Eastern Europe`, `EU | National`, `BENELUX | News`, and `DE-CH | Public`.

## 4. NordVPN routes

The route is playback metadata, not an admission filter. Use these country exits when the corresponding channel is selected:

| NordVPN country | Channels |
|---|---|
| France | ARTE Français |
| Spain | RTVE Canal 24 Horas |
| Italy | Rai News 24 |
| Belgium | LN24 |
| Netherlands | NPO Politiek en Nieuws |
| Germany | phoenix |
| Switzerland | RTS Info |

RTVE and Rai currently also pass from Peru, but their approved route labels remain Spain and Italy. The other five are blocked from the current Peru egress.

For LN24 and NPO, connect both the Mac and Apple TV to the required country while launching the channel. The Mac performs the short official-player resolution; UHF follows the redirect and retrieves the media through Apple TV's active VPN route.

## 5. Revalidate a route

After manually selecting the country in NordVPN on the Mac, run the matching command from `docs/VALIDATION.md`. `--verify-egress-country` prevents a mislabeled report if NordVPN did not actually switch.

## 6. Failure handling

1. Confirm the Mac and Apple TV are using the channel's documented route.
2. Run the single-channel validation command.
3. Read its route-specific report under `reports/`.
4. If a static URL fails but an alternate passes, the generated playlist adopts the passing alternate for that build.
5. If a dynamic resolver fails, do not replace it with a stale hardcoded URL; verify the egress country and inspect the official player chain.

NordVPN solves ordinary territory restrictions. It does not solve expired streams, DRM, broadcaster-side VPN rejection, or a removed channel.
