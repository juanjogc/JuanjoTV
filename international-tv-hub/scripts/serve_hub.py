#!/usr/bin/env python3
"""Serve generated playlists to UHF devices on the local network."""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import re
import socket
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import build_hub


ROOT = Path(__file__).resolve().parents[1]
MAX_PROXY_RESPONSE = 32 * 1024 * 1024


def proxy_target_url(base_url: str, channel_id: str, target_url: str) -> str:
    query = urllib.parse.urlencode({"url": target_url})
    return urllib.parse.urljoin(base_url, f"hls-proxy/{channel_id}?{query}")


def rewrite_hls_manifest(
    manifest: str, source_url: str, proxy_base_url: str, channel_id: str
) -> str:
    def rewrite_uri(uri: str) -> str:
        absolute = urllib.parse.urljoin(source_url, uri)
        if urllib.parse.urlparse(absolute).scheme not in {"http", "https"}:
            return uri
        return proxy_target_url(proxy_base_url, channel_id, absolute)

    rewritten: list[str] = []
    for line in manifest.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            rewritten.append(rewrite_uri(stripped))
            continue
        rewritten.append(
            re.sub(
                r'URI="([^"]+)"',
                lambda match: f'URI="{rewrite_uri(match.group(1))}"',
                line,
            )
        )
    return "\n".join(rewritten) + "\n"


class HubRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Serve playlists and turn approved dynamic players into fresh HLS redirects."""

    hub_config: dict = {}
    resolver_timeout: float = 10.0

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        resolver_match = re.fullmatch(
            r"/resolve/([a-z0-9-]+)\.m3u8", parsed.path
        )
        if resolver_match:
            self._serve_resolver(resolver_match.group(1))
            return
        proxy_match = re.fullmatch(r"/hls-proxy/([a-z0-9-]+)", parsed.path)
        if proxy_match:
            target_url = urllib.parse.parse_qs(parsed.query).get("url", [""])[0]
            self._serve_hls_proxy(proxy_match.group(1), target_url)
            return
        if parsed.path.endswith(".m3u"):
            self._serve_playlist(parsed.path)
            return
        super().do_GET()

    def _serve_resolver(self, channel_id: str) -> None:
        channels = {
            channel["id"]: channel for channel in self.hub_config.get("channels", [])
        }
        channel = channels.get(channel_id)
        if not channel or not channel.get("resolver"):
            self.send_error(404, "Unknown dynamic channel")
            return
        try:
            stream_url = build_hub.resolve_channel_url(channel, self.resolver_timeout)
        except build_hub.ResolverError as exc:
            payload = json.dumps(
                {
                    "error": str(exc),
                    "channel": channel_id,
                    "required_route": channel.get("required_route"),
                },
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if channel["resolver"].get("proxy_hls"):
            self._proxy_upstream(channel, stream_url)
        else:
            self.send_response(302)
            self.send_header("Location", stream_url)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

    def _serve_hls_proxy(self, channel_id: str, target_url: str) -> None:
        channels = {
            channel["id"]: channel for channel in self.hub_config.get("channels", [])
        }
        channel = channels.get(channel_id)
        if not channel or not channel.get("resolver", {}).get("proxy_hls"):
            self.send_error(404, "Unknown proxied channel")
            return
        self._proxy_upstream(channel, target_url)

    def _proxy_upstream(self, channel: dict, target_url: str) -> None:
        resolver = channel["resolver"]
        allowed_hosts = set(resolver.get("allowed_hosts", []))
        parsed_target = urllib.parse.urlparse(target_url)
        if (
            parsed_target.scheme != "https"
            or not parsed_target.hostname
            or parsed_target.hostname not in allowed_hosts
            or parsed_target.username
            or parsed_target.password
        ):
            self.send_error(403, "Proxy target is not approved")
            return

        upstream_headers = build_hub._headers(
            channel.get("request_headers"),
            accept=self.headers.get("Accept") or "*/*",
            byte_range=self.headers.get("Range"),
        )
        request = urllib.request.Request(target_url, headers=upstream_headers)
        try:
            with urllib.request.urlopen(request, timeout=self.resolver_timeout) as response:
                final_url = response.geturl()
                final_host = urllib.parse.urlparse(final_url).hostname
                if final_host not in allowed_hosts:
                    self.send_error(403, "Proxy redirect left the approved hosts")
                    return
                payload = response.read(MAX_PROXY_RESPONSE + 1)
                if len(payload) > MAX_PROXY_RESPONSE:
                    self.send_error(502, "Upstream response exceeds proxy limit")
                    return
                content_type = response.headers.get_content_type().lower()
                status = response.getcode()
                content_range = response.headers.get("Content-Range")
                accept_ranges = response.headers.get("Accept-Ranges")
        except urllib.error.HTTPError as exc:
            self.send_error(exc.code, f"Upstream HTTP {exc.code}")
            return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self.send_error(502, f"Upstream request failed: {getattr(exc, 'reason', exc)}")
            return

        is_manifest = (
            urllib.parse.urlparse(final_url).path.endswith(".m3u8")
            or content_type in build_hub.HLS_CONTENT_TYPES
            or payload.lstrip().startswith(b"#EXTM3U")
        )
        if is_manifest:
            host = self.headers.get("Host") or (
                f"{self.server.server_address[0]}:{self.server.server_address[1]}"
            )
            proxy_base_url = f"http://{host}/"
            text = payload.decode("utf-8", errors="replace")
            payload = rewrite_hls_manifest(
                text, final_url, proxy_base_url, channel["id"]
            ).encode("utf-8")
            content_type = "application/vnd.apple.mpegurl"

        self.send_response(status)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Cache-Control", "no-store" if is_manifest else "private, max-age=30")
        if content_range:
            self.send_header("Content-Range", content_range)
        if accept_ranges:
            self.send_header("Accept-Ranges", accept_ranges)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _serve_playlist(self, request_path: str) -> None:
        filename = Path(urllib.parse.unquote(request_path)).name
        allowed = {
            "uhf-international.m3u",
            *self.hub_config.get("group_files", {}).values(),
        }
        static_export_file = self.hub_config.get("static_export_file")
        if isinstance(static_export_file, str):
            allowed.add(static_export_file)
        if filename not in allowed:
            self.send_error(404, "Unknown playlist")
            return
        playlist_path = Path(self.directory) / filename
        try:
            lines = playlist_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            self.send_error(404, "Playlist not built")
            return
        host = self.headers.get("Host") or f"{self.server.server_address[0]}:{self.server.server_address[1]}"
        base_url = f"http://{host}/"
        rewritten = [
            urllib.parse.urljoin(base_url, line) if line.startswith("resolve/") else line
            for line in lines
        ]
        payload = ("\n".join(rewritten) + "\n").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "audio/x-mpegurl; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def routed_ip() -> str | None:
    connection = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        connection.connect(("192.0.2.1", 80))
        return connection.getsockname()[0]
    except OSError:
        return None
    finally:
        connection.close()


def local_ip_candidates() -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()
    for interface in ("en0", "en1", "en2", "en3"):
        try:
            completed = subprocess.run(
                ["/usr/sbin/ipconfig", "getifaddr", interface],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        address = completed.stdout.strip()
        if completed.returncode == 0 and address and address not in seen:
            candidates.append((interface, address))
            seen.add(address)

    route_address = routed_ip()
    if route_address and route_address not in seen and not route_address.startswith("127."):
        candidates.append(("current route; possibly VPN", route_address))
    return candidates


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "generated")
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--config", type=Path, default=build_hub.DEFAULT_CONFIG)
    parser.add_argument("--resolver-timeout", type=float, default=10.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    directory = args.directory.resolve()
    if not (directory / "uhf-international.m3u").exists():
        raise SystemExit(
            f"Missing {directory / 'uhf-international.m3u'}; run build_hub.py first."
        )

    config = build_hub.load_config(args.config)
    build_hub.validate_config(config)
    HubRequestHandler.hub_config = config
    HubRequestHandler.resolver_timeout = args.resolver_timeout
    handler = functools.partial(HubRequestHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer((args.bind, args.port), handler)
    print(f"Serving {directory}")
    if args.bind not in {"0.0.0.0", "::"}:
        print(f"UHF playlist URL: http://{args.bind}:{args.port}/uhf-international.m3u")
    else:
        candidates = local_ip_candidates()
        if candidates:
            print("UHF playlist URL candidates:")
            for label, address in candidates:
                print(f"  {label}: http://{address}:{args.port}/uhf-international.m3u")
            print("Use the en0/en1 address on the same home subnet as Apple TV, not a VPN-route address.")
        else:
            print(f"UHF playlist URL: http://THIS-MAC-LAN-IP:{args.port}/uhf-international.m3u")
    print("Keep this process and the Mac awake while UHF imports or refreshes the playlist.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
