# Locked approval record

The user approved this exact 33-channel lineup on 2026-08-24. There is no laboratory, disabled, companion, or automatically admitted tier in the implementation. A future change requires a new explicit approval and a corresponding update to `approved_channel_ids`.

| # | ID | Channel | Disclosed transport | Required route |
|---:|---|---|---|---|
| 1 | `bfm-business` | BFM Business | Official HLS | Direct |
| 2 | `france24-fr` | France 24 Français | Official HLS | Direct |
| 3 | `euronews-fr` | Euronews Français | Official HLS | Direct |
| 4 | `tv5monde-info` | TV5MONDE Info | Official HLS | Direct |
| 5 | `arte-fr` | ARTE Français | Official HLS | NordVPN France |
| 6 | `bloomberg-eu` | Bloomberg Television Europe | Official HLS | Direct |
| 7 | `bloomberg-us` | Bloomberg Television US | Official HLS | Direct |
| 8 | `bbc-news` | BBC News | Official HLS | Direct |
| 9 | `france24-en` | France 24 English | Official HLS | Direct |
| 10 | `euronews-en` | Euronews English | Official HLS | Direct |
| 11 | `dw-en` | DW English | Official HLS | Direct |
| 12 | `yahoo-finance` | Yahoo Finance | FAST partner | Direct |
| 13 | `nhk-world` | NHK World-Japan | Official HLS | Direct |
| 14 | `cna` | CNA | Broadcaster CDN | Direct |
| 15 | `suspilne-kyiv` | Suspilne Kyiv | Official HLS | Direct |
| 16 | `tvp-world` | TVP World | Working mirror | Direct |
| 17 | `current-time` | Current Time | Official HLS | Direct |
| 18 | `tv-rain` | TV Rain / Дождь | Official HLS | Direct |
| 19 | `tvr-info` | TVR Info | Broadcaster CDN | Direct |
| 20 | `etv-plus` | ETV+ | Official HLS | Direct |
| 21 | `tvp-info` | TVP Info | Working mirror | Direct |
| 22 | `ct24` | ČT24 | Named distributor | Direct |
| 23 | `belsat-vottak` | Belsat / Vot Tak / Slawa | Working distributor | Direct |
| 24 | `rtve-24h` | RTVE Canal 24 Horas | Nord Spain | NordVPN Spain |
| 25 | `orf-iii` | ORF III | Official HLS | Direct |
| 26 | `rai-news-24` | Rai News 24 | Nord Italy | NordVPN Italy |
| 27 | `canal-z` | Canal Z | Broadcaster CDN | Direct |
| 28 | `ln24` | LN24 | Nord Belgium resolver | NordVPN Belgium |
| 29 | `npo-politiek` | NPO Politiek en Nieuws | Nord Netherlands resolver | NordVPN Netherlands |
| 30 | `rtl-letzebuerg` | RTL Télé Lëtzebuerg | Official HLS | Direct |
| 31 | `tagesschau24` | tagesschau24 | Official HLS | Direct |
| 32 | `phoenix` | phoenix | Nord Germany | NordVPN Germany |
| 33 | `rts-info` | RTS Info | Nord Switzerland | NordVPN Switzerland |

## Disclosure rule

The following labels must remain visible in configuration, playlist attributes, and health reports:

- TVP World and TVP Info: `Working mirror`.
- ČT24: `Named distributor`.
- Belsat / Vot Tak / Slawa: `Working distributor`.
- Yahoo Finance: `FAST partner`.
- LN24 and NPO: their named NordVPN runtime resolvers.

No source may be silently upgraded to “official” or swapped to an undisclosed mirror merely because it is technically reachable.
