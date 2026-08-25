# Mac-free UHF setup: approval-locked static 43

## What this setup does

`generated/uhf-static-43.m3u` is the approved expanded playlist. Every entry is a direct HTTPS HLS URL: no `resolve/` URL, Mac process, Docker container, NAS, or USB drive is needed during playback.

GitHub Pages is the always-online playlist host:

1. An approved registry change is pushed to `main`.
2. GitHub Actions validates both approval locks and rebuilds both editions.
3. A successful run publishes `uhf-static-43.m3u` at a stable HTTPS URL.
4. UHF downloads that URL when the playlist is imported or refreshed.

UHF has no verified public write API, so this is necessarily pull-based. Publication is automatic; UHF refreshes the same URL instead of receiving a file pushed into its private app storage.

## Contents and approval boundary

The expanded edition contains 43 channels. It is the earlier static 28 plus:

- Rai News 24
- RTS Info
- 3CatInfo
- Al Jazeera English
- Moldova 1
- CBS News 24/7
- Espreso TV
- TGCom24
- NBC News NOW
- ABC News Live — disclosed Tubi FAST distributor
- LRT TV
- Class CNBC — disclosed Amagi distributor
- Euronews Русский
- Euronews Polski
- ZDFinfo

Euronews Français was already in the original 28 and remains present exactly once.

LRT Lituanica is not a forty-fourth entry: live testing found its official URL returning 404 and its former permanent fallback domain no longer resolving. LRT TV is the proven side of the previously proposed `LRT Lituanica / LRT TV` alternative; its current stable LRT HLS path passed the complete media-segment test.

The original approved 33 and legacy static 28 remain independently reproducible. The expanded overlay does not rewrite or silently replace them.

## Required NordVPN routes

UHF displays the required country directly in the channel name, for example `ZDFinfo [VPN: Germany]`. The M3U also carries `uhf-vpn-required`, `uhf-vpn-route`, and `uhf-vpn-label` metadata.

| NordVPN country | Visibly flagged channels | Reason |
|---|---|---|
| France | ARTE Français | French simulcast route |
| Italy | Rai News 24; Class CNBC | Approved Rai route; Class CNBC returns 403 outside its Italian distribution route |
| Germany | phoenix; ZDFinfo | German public-broadcaster geo policy |
| Switzerland | RTS Info | Approved Swiss public-broadcaster route |

LRT TV currently passes from Peru. NordVPN Lithuania is recorded as its retry route, not marked as mandatory.

The playlist cannot switch NordVPN countries by itself. Before opening a flagged channel on Apple TV:

1. Leave UHF.
2. Open NordVPN on Apple TV.
3. Connect to the country shown in the UHF channel name.
4. Return to UHF and start the channel.

## One-time GitHub publication

The repository must first be connected to a GitHub account:

1. Create or select a public GitHub repository.
2. Push this repository's `main` branch.
3. Open **Settings → Pages**.
4. Under **Build and deployment**, choose **GitHub Actions**.
5. Open **Actions** and wait for **Publish UHF static playlist**.

The primary UHF URL will be:

```text
https://GITHUB-USERNAME.github.io/REPOSITORY-NAME/uhf-static-43.m3u
```

The rollback URL ends in `uhf-static-28.m3u`.

Do not put VPN credentials, tokens, private IPTV accounts, or subscription URLs into this public repository.

## One-time UHF import

1. Open UHF on iPhone or iPad if available; entering the URL there is easier than using the Apple TV remote.
2. Choose **Add playlist → M3U / M3U8 URL**.
3. Name it `Juanjo International TV — Static 43`.
4. Paste the GitHub Pages URL ending in `uhf-static-43.m3u`.
5. Leave EPG/XMLTV blank for the first import.
6. Save and confirm that UHF reports 43 channels.
7. If you use UHF Pro multi-device sync, let the playlist synchronize to Apple TV. Otherwise, enter the same URL once on Apple TV.

After approved updates, use UHF's playlist refresh/update control. The source URL never changes.

## Automation boundary

| Event | Result |
|---|---|
| Approved change pushed to `main` | Tests, build, and HTTPS publication run automatically. |
| Approval lock or test fails | The live hosted playlist is not replaced. |
| UHF refreshes | It downloads the new 43-channel contents from the same URL. |
| A broadcaster changes a stream | The source must be researched, revalidated, approved, and republished. |
| A flagged channel is opened | Its documented NordVPN country must already be active on Apple TV. |

The workflow deliberately publishes only the two static editions. The Mac-dependent original 33-channel playlist is never exposed as the primary hosted UHF source.
