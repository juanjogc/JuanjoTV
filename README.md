# JuanjoTV

A Mac-free international television hub for UHF on Apple TV, iPhone, and iPad.

## UHF playlist

```text
https://juanjogc.github.io/JuanjoTV/uhf-static-43.m3u
```

The playlist contains 43 approval-locked channels. Every entry is a direct HTTPS HLS source, so the Mac does not need to remain on during playback.

Channels that need NordVPN display the required country in their UHF name. The playlist itself cannot switch VPN countries; connect NordVPN on Apple TV before opening a flagged channel.

GitHub Actions validates, rebuilds, and publishes the playlist after approved changes are pushed to `main`.

See [the complete setup guide](international-tv-hub/docs/UHF_STATIC_SETUP.md).
