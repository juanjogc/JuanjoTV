from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_hub.py"
EXPANDED_CONFIG = (
    Path(__file__).resolve().parents[1] / "config" / "channels-static-expanded.json"
)
SPEC = importlib.util.spec_from_file_location("build_hub", SCRIPT)
assert SPEC and SPEC.loader
build_hub = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = build_hub
SPEC.loader.exec_module(build_hub)

SERVE_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "serve_hub.py"
SERVE_SPEC = importlib.util.spec_from_file_location("serve_hub", SERVE_SCRIPT)
assert SERVE_SPEC and SERVE_SPEC.loader
serve_hub = importlib.util.module_from_spec(SERVE_SPEC)
sys.modules[SERVE_SPEC.name] = serve_hub
SERVE_SPEC.loader.exec_module(serve_hub)


def sample_config():
    return {
        "schema_version": 1,
        "hub_name": "Test hub",
        "group_order": ["INTL | News"],
        "group_files": {"INTL | News": "intl-news.m3u"},
        "vpn_profiles": {
            "off_first": {
                "routes": ["DIRECT", "ORIGIN"],
                "description": "test",
            }
        },
        "channels": [
            {
                "id": "enabled-news",
                "name": "Enabled News",
                "tvg_id": "EnabledNews.test",
                "group": "INTL | News",
                "country": "GB",
                "language": "eng",
                "stream_url": "https://example.com/live.m3u8",
                "official_page": "https://example.com/live",
                "source_class": "official_cdn",
                "source_label": "Official HLS",
                "distribution": "Example broadcaster",
                "enabled": True,
                "curated_rank": 1,
                "vpn_policy": "off_first",
                "notes": "test",
            },
            {
                "id": "disabled-news",
                "name": "Disabled News",
                "tvg_id": "DisabledNews.test",
                "group": "INTL | News",
                "country": "US",
                "language": "eng",
                "stream_url": "https://example.org/live.m3u8",
                "official_page": "https://example.org/live",
                "source_class": "third_party_mirror",
                "source_label": "Working mirror",
                "distribution": "Example mirror",
                "enabled": False,
                "vpn_policy": "off_first",
                "notes": "test",
            },
        ],
    }


class RegistryTests(unittest.TestCase):
    def test_reference_registry_is_valid(self):
        config = build_hub.load_config(build_hub.DEFAULT_CONFIG)
        build_hub.validate_config(config)

    def test_reference_registry_is_exactly_the_approved_33(self):
        config = build_hub.load_config(build_hub.DEFAULT_CONFIG)
        ids = [channel["id"] for channel in config["channels"]]
        self.assertTrue(config["approval_locked"])
        self.assertEqual(len(ids), 33)
        self.assertEqual(ids, config["approved_channel_ids"])
        self.assertTrue(all(channel["enabled"] for channel in config["channels"]))

    def test_reference_static_export_is_exactly_the_approved_28(self):
        config = build_hub.load_config(build_hub.DEFAULT_CONFIG)
        static_ids = [
            channel["id"]
            for channel in config["channels"]
            if channel["enabled"] and not channel.get("resolver")
        ]
        self.assertEqual(len(static_ids), 28)
        self.assertEqual(static_ids, config["approved_static_channel_ids"])

    def test_expanded_registry_is_exactly_the_approved_static_43(self):
        config = build_hub.load_config(EXPANDED_CONFIG)
        build_hub.validate_config(config)
        ids = [channel["id"] for channel in config["channels"]]
        self.assertEqual(len(ids), 43)
        self.assertEqual(ids, config["approved_channel_ids"])
        self.assertEqual(ids, config["approved_static_channel_ids"])
        self.assertTrue(all(not channel.get("resolver") for channel in config["channels"]))
        self.assertEqual(ids.count("euronews-fr"), 1)
        self.assertNotIn("lrt-lituanica", ids)

    def test_expanded_overlay_does_not_modify_original_33(self):
        expanded = build_hub.load_config(EXPANDED_CONFIG)
        original = build_hub.load_config(build_hub.DEFAULT_CONFIG)
        self.assertEqual(len(original["channels"]), 33)
        self.assertEqual(
            original["approved_channel_ids"],
            [channel["id"] for channel in original["channels"]],
        )
        self.assertEqual(len(expanded["channels"]), 43)

    def test_duplicate_ids_are_rejected(self):
        config = sample_config()
        duplicate = dict(config["channels"][0])
        duplicate["name"] = "Duplicate"
        duplicate.pop("curated_rank")
        config["channels"].append(duplicate)
        with self.assertRaisesRegex(ValueError, "duplicate channel id"):
            build_hub.validate_config(config)

    def test_unknown_vpn_profile_is_rejected(self):
        config = sample_config()
        config["channels"][0]["vpn_policy"] = "missing"
        with self.assertRaisesRegex(ValueError, "vpn_policy is not defined"):
            build_hub.validate_config(config)


class PlaylistTests(unittest.TestCase):
    def test_disabled_channels_are_not_published(self):
        config = sample_config()
        build_hub.validate_config(config)
        selected = build_hub.select_generated_channels(config, {}, False)
        with tempfile.TemporaryDirectory() as directory:
            paths = build_hub.generate_playlists(config, Path(directory), selected)
            combined = paths[0].read_text(encoding="utf-8")
        self.assertIn("Enabled News", combined)
        self.assertNotIn("Disabled News", combined)
        self.assertIn('group-title="INTL | News"', combined)

    def test_reference_playlist_preserves_disclosures_and_dynamic_resolvers(self):
        config = build_hub.load_config(build_hub.DEFAULT_CONFIG)
        selected = build_hub.select_generated_channels(config, {}, False)
        with tempfile.TemporaryDirectory() as directory:
            paths = build_hub.generate_playlists(config, Path(directory), selected)
            combined = paths[0].read_text(encoding="utf-8")
        self.assertEqual(combined.count("#EXTINF:"), 33)
        self.assertIn('uhf-source-label="Working mirror"', combined)
        self.assertIn('uhf-source-label="Named distributor"', combined)
        self.assertIn('uhf-source-label="Working distributor"', combined)
        self.assertIn("resolve/ln24.m3u8", combined)
        self.assertIn("resolve/npo-politiek.m3u8", combined)
        self.assertIn("resolve/rtve-24h.m3u8", combined)
        self.assertIn("resolve/rts-info.m3u8", combined)

    def test_reference_static_export_has_28_direct_urls_and_no_resolvers(self):
        config = build_hub.load_config(build_hub.DEFAULT_CONFIG)
        selected = build_hub.select_generated_channels(config, {}, False)
        with tempfile.TemporaryDirectory() as directory:
            paths = build_hub.generate_playlists(config, Path(directory), selected)
            static_path = next(
                path for path in paths if path.name == config["static_export_file"]
            )
            static_playlist = static_path.read_text(encoding="utf-8")
        self.assertEqual(static_playlist.count("#EXTINF:"), 28)
        self.assertNotIn("resolve/", static_playlist)
        self.assertIn("ARTE Fran\u00e7ais", static_playlist)
        self.assertIn("phoenix", static_playlist)
        for channel_id in {
            "rtve-24h",
            "rai-news-24",
            "ln24",
            "npo-politiek",
            "rts-info",
        }:
            channel = next(
                item for item in config["channels"] if item["id"] == channel_id
            )
            self.assertNotIn(f',{channel["name"]}\n', static_playlist)

    def test_expanded_static_playlist_has_43_direct_urls_and_visible_vpn_flags(self):
        config = build_hub.load_config(EXPANDED_CONFIG)
        selected = build_hub.select_generated_channels(config, {}, False)
        with tempfile.TemporaryDirectory() as directory:
            paths = build_hub.generate_playlists(config, Path(directory), selected)
            static_path = next(
                path for path in paths if path.name == config["static_export_file"]
            )
            playlist = static_path.read_text(encoding="utf-8")
        self.assertEqual(playlist.count("#EXTINF:"), 43)
        self.assertNotIn("resolve/", playlist)
        for name in {
            "Euronews Français",
            "Euronews Русский",
            "Euronews Polski",
            "ZDFinfo",
            "LRT TV",
            "Class CNBC",
        }:
            self.assertIn(name, playlist)
        self.assertNotIn("LRT Lituanica", playlist)
        self.assertEqual(playlist.count('tvg-id="EuronewsFrench.fr"'), 1)
        self.assertIn("Class CNBC [VPN: Italy]", playlist)
        self.assertIn("ZDFinfo [VPN: Germany]", playlist)
        self.assertIn('uhf-vpn-required="true"', playlist)
        self.assertIn('uhf-vpn-label="NordVPN Germany"', playlist)
        self.assertIn('uhf-source-label="Tubi FAST distributor"', playlist)

    @mock.patch("build_hub._open_bytes")
    def test_probe_validates_master_variant_and_media_segment(self, open_bytes):
        open_bytes.side_effect = [
            (
                200,
                "application/vnd.apple.mpegurl",
                "https://example.com/master.m3u8",
                b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nmedia.m3u8\n",
            ),
            (
                200,
                "application/vnd.apple.mpegurl",
                "https://example.com/media.m3u8",
                b"#EXTM3U\n#EXTINF:4,\nsegment.ts\n",
            ),
            (206, "video/mp2t", "https://example.com/segment.ts", b"video"),
        ]
        result = build_hub.probe_url(
            "enabled-news", "https://example.com/master.m3u8", 1
        )
        self.assertEqual(result.status, "healthy")
        self.assertEqual(result.stage, "segment")
        self.assertEqual(result.segment_url, "https://example.com/segment.ts")

    @mock.patch("build_hub._open_bytes")
    def test_ln24_resolver_extracts_fresh_official_hls(self, open_bytes):
        config = build_hub.load_config(build_hub.DEFAULT_CONFIG)
        channel = next(item for item in config["channels"] if item["id"] == "ln24")
        open_bytes.return_value = (
            200,
            "text/html",
            channel["resolver"]["endpoint"],
            b'<source src="https:\\/\\/cdn.example\\/live\\/manifest.m3u8">',
        )
        self.assertEqual(
            build_hub.resolve_channel_url(channel, 1),
            "https://cdn.example/live/manifest.m3u8",
        )

    @mock.patch("build_hub._open_bytes")
    def test_ln24_resolver_reports_server_side_region_denial(self, open_bytes):
        config = build_hub.load_config(build_hub.DEFAULT_CONFIG)
        channel = next(item for item in config["channels"] if item["id"] == "ln24")
        open_bytes.return_value = (
            200,
            "text/html",
            channel["resolver"]["endpoint"],
            b"Ce contenu n&#039;est pas disponible dans votre r\xc3\xa9gion.",
        )
        with self.assertRaisesRegex(
            build_hub.ResolverError, "denied this VPN exit server-side"
        ):
            build_hub.resolve_channel_url(channel, 1)

    @mock.patch("build_hub._open_bytes")
    def test_npo_resolver_uses_anonymous_token_and_stream_link(self, open_bytes):
        config = build_hub.load_config(build_hub.DEFAULT_CONFIG)
        channel = next(
            item for item in config["channels"] if item["id"] == "npo-politiek"
        )
        token = "a" * 30 + "." + "b" * 30 + "." + "c" * 30
        open_bytes.side_effect = [
            (200, "application/json", "https://npo.nl/token", ('{"token":"' + token + '"}').encode()),
            (
                200,
                "application/json",
                "https://prod.npoplayer.nl/stream-link",
                b'{"stream":{"src":"https://cdn.example/npo/live.m3u8?fresh=1"}}',
            ),
        ]
        self.assertEqual(
            build_hub.resolve_channel_url(channel, 1),
            "https://cdn.example/npo/live.m3u8?fresh=1",
        )
        stream_call = open_bytes.call_args_list[1]
        self.assertEqual(stream_call.kwargs["method"], "POST")
        self.assertEqual(stream_call.args[2]["Authorization"], token)

    @mock.patch("build_hub._open_bytes")
    def test_srgssr_resolver_uses_official_media_composition(self, open_bytes):
        config = build_hub.load_config(build_hub.DEFAULT_CONFIG)
        channel = next(item for item in config["channels"] if item["id"] == "rts-info")
        hls_url = "https://cdn.example/rts-info/hls-master.m3u8?dw=7201"
        response = {
            "chapterUrn": "urn:rts:video:1967124",
            "chapterList": [
                {
                    "resourceList": [
                        {
                            "url": "https://cdn.example/rts-info/master.mpd",
                            "streaming": "DASH",
                            "quality": "HD",
                        },
                        {
                            "url": hls_url,
                            "streaming": "HLS",
                            "protocol": "HLS-DVR",
                            "quality": "HD",
                            "presentation": "DEFAULT",
                        },
                    ]
                }
            ],
        }
        open_bytes.return_value = (
            200,
            "application/json",
            channel["resolver"]["endpoint"],
            json.dumps(response).encode(),
        )
        self.assertEqual(build_hub.resolve_channel_url(channel, 1), hls_url)
        self.assertIn("onlyChapters=false", open_bytes.call_args.args[0])
        self.assertIn("vector=portalplay", open_bytes.call_args.args[0])

    @mock.patch("build_hub._open_bytes")
    def test_rtve_resolver_preserves_official_player_headers(self, open_bytes):
        config = build_hub.load_config(build_hub.DEFAULT_CONFIG)
        channel = next(item for item in config["channels"] if item["id"] == "rtve-24h")
        resolved = "https://rtvelivestream.rtve.es/rtvesec/24h/master.m3u8?idasset=1694255"
        open_bytes.return_value = (
            200,
            "application/vnd.apple.mpegurl",
            resolved,
            b"#EXTM3U\n#EXTINF:4,\nsegment.ts\n",
        )
        self.assertEqual(build_hub.resolve_channel_url(channel, 1), resolved)
        request_headers = open_bytes.call_args.args[2]
        self.assertEqual(request_headers["Origin"], "https://www.rtve.es")
        self.assertEqual(
            request_headers["Referer"],
            "https://www.rtve.es/play/videos/directo/24h/",
        )

    @mock.patch("build_hub._open_bytes")
    def test_rai_resolver_uses_current_mediapolis_json_flow(self, open_bytes):
        config = build_hub.load_config(build_hub.DEFAULT_CONFIG)
        channel = next(item for item in config["channels"] if item["id"] == "rai-news-24")
        resolved = "https://rai-cdn.example/rainews1/hls/playlist_mo.m3u8?tk2=fresh"
        response = {
            "video": [resolved],
            "playlist": [{"type": "bumper", "url": ""}, {"type": "main", "url": resolved}],
        }
        open_bytes.return_value = (
            200,
            "application/json",
            channel["resolver"]["endpoint"],
            json.dumps(response).encode(),
        )
        self.assertEqual(build_hub.resolve_channel_url(channel, 1), resolved)
        request_url = open_bytes.call_args.args[0]
        request_headers = open_bytes.call_args.args[2]
        self.assertIn("cont=1", request_url)
        self.assertIn("output=62", request_url)
        self.assertEqual(request_headers["x-ua-token"], "null")
        self.assertEqual(request_headers["Origin"], "https://www.rainews.it")

    def test_unhealthy_channel_can_be_excluded(self):
        config = sample_config()
        result = build_hub.ProbeResult(
            "enabled-news", "unhealthy", 403, 12, None, None, "HTTP 403"
        )
        selected = build_hub.select_generated_channels(config, {"enabled-news": result}, True)
        self.assertEqual(selected, [])

    def test_healthy_channel_is_selected(self):
        config = sample_config()
        result = build_hub.ProbeResult(
            "enabled-news",
            "healthy",
            200,
            12,
            "https://example.com/live.m3u8",
            "application/vnd.apple.mpegurl",
            "HLS manifest received",
        )
        selected = build_hub.select_generated_channels(config, {"enabled-news": result}, True)
        self.assertEqual([channel["id"] for channel in selected], ["enabled-news"])

    @mock.patch("build_hub.probe_url")
    def test_probe_uses_first_healthy_alternate(self, probe_url):
        channel = sample_config()["channels"][0]
        channel["alternate_stream_urls"] = ["https://backup.example.com/live.m3u8"]
        probe_url.side_effect = [
            build_hub.ProbeResult(
                channel["id"], "unhealthy", None, 5, channel["stream_url"], None, "DNS"
            ),
            build_hub.ProbeResult(
                channel["id"],
                "healthy",
                200,
                7,
                channel["alternate_stream_urls"][0],
                "application/vnd.apple.mpegurl",
                "HLS manifest received",
            ),
        ]
        result = build_hub.probe_channel(channel, timeout=1)
        self.assertEqual(result.status, "healthy")
        self.assertEqual(result.final_url, channel["alternate_stream_urls"][0])
        self.assertIn("alternate endpoint 2", result.detail)

    def test_country_specific_vpn_route_is_expanded(self):
        config = sample_config()
        config["vpn_country_routes"] = {"GB": ["DIRECT", "GB", "NL"]}
        routes = build_hub.vpn_routes_for(config, config["channels"][0])
        self.assertEqual(routes, ["DIRECT", "GB", "NL"])


class RuntimeProxyTests(unittest.TestCase):
    def test_rtve_manifest_rewrites_variants_and_uri_attributes(self):
        manifest = (
            '#EXTM3U\n'
            '#EXT-X-MEDIA:TYPE=AUDIO,URI="audio.m3u8"\n'
            '#EXT-X-STREAM-INF:BANDWIDTH=1000\n'
            'video.m3u8?live=1\n'
        )
        rewritten = serve_hub.rewrite_hls_manifest(
            manifest,
            "https://rtvelivestream.rtve.es/rtvesec/24h/master.m3u8",
            "http://192.168.1.10:8765/",
            "rtve-24h",
        )
        self.assertIn("http://192.168.1.10:8765/hls-proxy/rtve-24h?", rewritten)
        self.assertIn("audio.m3u8", urllib.parse.unquote(rewritten))
        self.assertIn("video.m3u8?live=1", urllib.parse.unquote(rewritten))

    def test_proxy_target_url_encodes_public_upstream(self):
        target = "https://rtvelivestream.rtve.es/live/segment.ts?token=one&part=2"
        proxied = serve_hub.proxy_target_url(
            "http://127.0.0.1:8765/", "rtve-24h", target
        )
        parsed = urllib.parse.urlparse(proxied)
        self.assertEqual(parsed.path, "/hls-proxy/rtve-24h")
        self.assertEqual(urllib.parse.parse_qs(parsed.query)["url"], [target])


if __name__ == "__main__":
    unittest.main()
