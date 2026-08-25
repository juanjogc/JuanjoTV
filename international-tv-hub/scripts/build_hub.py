#!/usr/bin/env python3
"""Validate a channel registry and generate UHF-ready M3U playlists."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "channels.json"
DEFAULT_OUTPUT_DIR = ROOT / "generated"
DEFAULT_REPORT = ROOT / "reports" / "source-health.md"
USER_AGENT = (
    "Mozilla/5.0 (AppleTV; U; CPU OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko)"
)
HLS_CONTENT_TYPES = {
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "audio/mpegurl",
    "audio/x-mpegurl",
}


@dataclass(frozen=True)
class ProbeResult:
    channel_id: str
    status: str
    http_code: int | None
    elapsed_ms: int
    final_url: str | None
    content_type: str | None
    detail: str
    stage: str = "none"
    segment_url: str | None = None


class ResolverError(RuntimeError):
    """Raised when an official dynamic player cannot produce a playback URL."""


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)

    base_filename = raw.get("base_config")
    if base_filename is None:
        return raw
    if (
        not isinstance(base_filename, str)
        or Path(base_filename).name != base_filename
        or not base_filename.endswith(".json")
    ):
        raise ValueError("base_config must name a local .json file")
    base_path = path.parent / base_filename
    if base_path.resolve() == path.resolve():
        raise ValueError("base_config cannot reference itself")

    config = load_config(base_path)
    overlay_controls = {
        "base_config",
        "excluded_channel_ids",
        "channel_overrides",
        "added_channels",
        "reindex_curated_rank",
    }
    mergeable_maps = {
        "group_files",
        "route_names",
        "route_country_codes",
        "vpn_profiles",
        "vpn_country_routes",
    }
    for key, value in raw.items():
        if key in overlay_controls:
            continue
        if key in mergeable_maps and isinstance(value, dict):
            config[key] = {**config.get(key, {}), **value}
        else:
            config[key] = value

    excluded = set(raw.get("excluded_channel_ids", []))
    overrides = raw.get("channel_overrides", {})
    channels: list[dict[str, Any]] = []
    for base_channel in config["channels"]:
        channel_id = base_channel["id"]
        if channel_id in excluded:
            continue
        channel = dict(base_channel)
        channel.update(overrides.get(channel_id, {}))
        channels.append(channel)
    channels.extend(raw.get("added_channels", []))

    approved_order = config.get("approved_channel_ids")
    if isinstance(approved_order, list):
        by_id = {channel.get("id"): channel for channel in channels}
        channels = [by_id[channel_id] for channel_id in approved_order if channel_id in by_id]
        channels.extend(
            channel for channel in by_id.values() if channel.get("id") not in approved_order
        )
    if raw.get("reindex_curated_rank"):
        for rank, channel in enumerate(channels, start=1):
            channel["curated_rank"] = rank
    config["channels"] = channels
    return config


def validate_config(config: dict[str, Any]) -> None:
    errors: list[str] = []
    if config.get("schema_version") != 1:
        errors.append("schema_version must be 1")

    groups = config.get("group_order")
    if not isinstance(groups, list) or not groups:
        errors.append("group_order must be a non-empty list")
        groups = []

    group_files = config.get("group_files")
    if not isinstance(group_files, dict):
        errors.append("group_files must be an object")
        group_files = {}

    for group in groups:
        filename = group_files.get(group)
        if not isinstance(filename, str) or not filename.endswith(".m3u"):
            errors.append(f"group_files[{group!r}] must name an .m3u file")

    combined_file = config.get("combined_file", "uhf-international.m3u")
    if (
        not isinstance(combined_file, str)
        or Path(combined_file).name != combined_file
        or not combined_file.endswith(".m3u")
    ):
        errors.append("combined_file must name a local .m3u file")

    vpn_profiles = config.get("vpn_profiles")
    if not isinstance(vpn_profiles, dict) or not vpn_profiles:
        errors.append("vpn_profiles must be a non-empty object")
        vpn_profiles = {}
    else:
        for profile_name, profile in vpn_profiles.items():
            routes = profile.get("routes") if isinstance(profile, dict) else None
            if not isinstance(routes, list) or not routes:
                errors.append(f"vpn_profiles[{profile_name!r}].routes must be a non-empty list")

    country_routes = config.get("vpn_country_routes", {})
    if not isinstance(country_routes, dict):
        errors.append("vpn_country_routes must be an object")
    else:
        for country, routes in country_routes.items():
            if not isinstance(routes, list) or not routes:
                errors.append(f"vpn_country_routes[{country!r}] must be a non-empty list")

    channels = config.get("channels")
    if not isinstance(channels, list) or not channels:
        errors.append("channels must be a non-empty list")
        channels = []

    required = {
        "id",
        "name",
        "group",
        "country",
        "language",
        "stream_url",
        "official_page",
        "source_class",
        "source_label",
        "distribution",
        "enabled",
        "vpn_policy",
        "notes",
    }
    ids: set[str] = set()
    ranks: set[int] = set()
    for index, channel in enumerate(channels):
        label = f"channels[{index}]"
        if not isinstance(channel, dict):
            errors.append(f"{label} must be an object")
            continue
        missing = sorted(required - channel.keys())
        if missing:
            errors.append(f"{label} missing: {', '.join(missing)}")
            continue

        channel_id = channel["id"]
        if not isinstance(channel_id, str) or not re.fullmatch(r"[a-z0-9-]+", channel_id):
            errors.append(f"{label}.id must use lowercase letters, digits, and hyphens")
        elif channel_id in ids:
            errors.append(f"duplicate channel id: {channel_id}")
        else:
            ids.add(channel_id)

        if channel["group"] not in groups:
            errors.append(f"{label}.group is not in group_order: {channel['group']!r}")
        if not isinstance(channel["enabled"], bool):
            errors.append(f"{label}.enabled must be boolean")
        elif config.get("approval_locked") and not channel["enabled"]:
            errors.append(f"{label}.enabled must be true in an approval-locked registry")
        if channel.get("vpn_policy") not in vpn_profiles:
            errors.append(f"{label}.vpn_policy is not defined in vpn_profiles")
        else:
            required_route = channel.get("required_route")
            profile_routes = vpn_profiles.get(channel.get("vpn_policy"), {}).get("routes", [])
            if required_route is not None and required_route not in profile_routes:
                errors.append(
                    f"{label}.required_route must appear in its vpn_policy routes"
                )

        for field in ("source_label", "distribution"):
            if not isinstance(channel.get(field), str) or not channel[field].strip():
                errors.append(f"{label}.{field} must be a non-empty string")

        for field in ("stream_url", "official_page"):
            value = channel.get(field)
            parsed = urllib.parse.urlparse(value) if isinstance(value, str) else None
            if not parsed or parsed.scheme != "https" or not parsed.netloc:
                errors.append(f"{label}.{field} must be an absolute HTTPS URL")
            elif parsed.username or parsed.password:
                errors.append(f"{label}.{field} must not contain credentials")

        alternate_urls = channel.get("alternate_stream_urls", [])
        if not isinstance(alternate_urls, list):
            errors.append(f"{label}.alternate_stream_urls must be a list")
        else:
            for alternate_index, value in enumerate(alternate_urls):
                parsed = urllib.parse.urlparse(value) if isinstance(value, str) else None
                if not parsed or parsed.scheme != "https" or not parsed.netloc:
                    errors.append(
                        f"{label}.alternate_stream_urls[{alternate_index}] must be an absolute HTTPS URL"
                    )
                elif parsed.username or parsed.password:
                    errors.append(
                        f"{label}.alternate_stream_urls[{alternate_index}] must not contain credentials"
                    )

        request_headers = channel.get("request_headers", {})
        if not isinstance(request_headers, dict):
            errors.append(f"{label}.request_headers must be an object")
        else:
            for key, value in request_headers.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    errors.append(f"{label}.request_headers must contain string values")
                elif "\n" in key + value or "\r" in key + value:
                    errors.append(f"{label}.request_headers must not contain newlines")

        resolver = channel.get("resolver")
        if resolver is not None:
            if not isinstance(resolver, dict):
                errors.append(f"{label}.resolver must be an object")
            elif resolver.get("type") not in {
                "ln24_player",
                "npo_player",
                "rai_mediapolis",
                "rtve_live_player",
                "srgssr_media_composition",
            }:
                errors.append(f"{label}.resolver.type is unsupported")
            else:
                for resolver_field in ("endpoint",):
                    value = resolver.get(resolver_field)
                    parsed = urllib.parse.urlparse(value) if isinstance(value, str) else None
                    if not parsed or parsed.scheme != "https" or not parsed.netloc:
                        errors.append(
                            f"{label}.resolver.{resolver_field} must be an absolute HTTPS URL"
                        )
                proxy_hls = resolver.get("proxy_hls", False)
                if not isinstance(proxy_hls, bool):
                    errors.append(f"{label}.resolver.proxy_hls must be boolean")
                elif proxy_hls:
                    allowed_hosts = resolver.get("allowed_hosts")
                    if not isinstance(allowed_hosts, list) or not allowed_hosts:
                        errors.append(
                            f"{label}.resolver.allowed_hosts must be a non-empty list for HLS proxying"
                        )
                    else:
                        for host in allowed_hosts:
                            if (
                                not isinstance(host, str)
                                or not host
                                or urllib.parse.urlparse(f"https://{host}").hostname != host
                            ):
                                errors.append(
                                    f"{label}.resolver.allowed_hosts must contain hostnames only"
                                )

        rank = channel.get("curated_rank")
        if rank is not None:
            if not isinstance(rank, int) or rank < 1:
                errors.append(f"{label}.curated_rank must be a positive integer")
            elif rank in ranks:
                errors.append(f"duplicate curated_rank: {rank}")
            else:
                ranks.add(rank)

    if ranks and ranks != set(range(1, max(ranks) + 1)):
        errors.append("curated_rank values must be contiguous from 1")

    approved_ids = config.get("approved_channel_ids")
    static_export_file = config.get("static_export_file")
    approved_static_ids = config.get("approved_static_channel_ids")
    channel_ids_in_order = [
        channel.get("id") for channel in channels if isinstance(channel, dict)
    ]
    if config.get("approval_locked"):
        if not isinstance(approved_ids, list) or not approved_ids:
            errors.append("approved_channel_ids must be a non-empty list when approval_locked")
        elif approved_ids != channel_ids_in_order:
            errors.append("channels must exactly match approved_channel_ids in approved order")

        if (
            not isinstance(static_export_file, str)
            or Path(static_export_file).name != static_export_file
            or not static_export_file.endswith(".m3u")
        ):
            errors.append("static_export_file must name a local .m3u file")
        expected_static_ids = [
            channel.get("id")
            for channel in channels
            if isinstance(channel, dict)
            and channel.get("enabled")
            and not channel.get("resolver")
        ]
        if not isinstance(approved_static_ids, list) or not approved_static_ids:
            errors.append(
                "approved_static_channel_ids must be a non-empty list when approval_locked"
            )
        elif approved_static_ids != expected_static_ids:
            errors.append(
                "approved_static_channel_ids must exactly match enabled non-resolver channels in approved order"
            )

    if errors:
        raise ValueError("Invalid channel registry:\n- " + "\n- ".join(errors))


def _headers(
    channel_headers: dict[str, str] | None = None,
    *,
    accept: str = "application/vnd.apple.mpegurl,application/x-mpegURL,*/*;q=0.8",
    byte_range: str | None = None,
) -> dict[str, str]:
    headers = {"Accept": accept, "User-Agent": USER_AGENT}
    headers.update(channel_headers or {})
    if byte_range:
        headers["Range"] = byte_range
    return headers


def _open_bytes(
    url: str,
    timeout: float,
    headers: dict[str, str],
    *,
    data: bytes | None = None,
    method: str | None = None,
    limit: int = 1_048_576,
) -> tuple[int, str, str, bytes]:
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read(limit)
        return (
            response.getcode(),
            response.headers.get_content_type().lower(),
            response.geturl(),
            payload,
        )


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read(512).decode("utf-8", errors="replace").strip()
    except OSError:
        body = ""
    body = "".join(
        character if character.isprintable() and character != "\ufffd" else " "
        for character in body
    )
    body = re.sub(r"\s+", " ", body)
    if body and body.lstrip()[:1] not in {"<", "{", "["}:
        body = ""
    return f"HTTP {exc.code}" + (f": {body[:240]}" if body else "")


def _find_jwt(value: Any) -> str | None:
    if isinstance(value, str) and value.count(".") == 2 and len(value) > 60:
        return value
    if isinstance(value, dict):
        for child in value.values():
            token = _find_jwt(child)
            if token:
                return token
    if isinstance(value, list):
        for child in value:
            token = _find_jwt(child)
            if token:
                return token
    return None


def _find_hls_url(value: Any) -> str | None:
    if isinstance(value, str):
        decoded = html.unescape(value).replace("\\/", "/")
        match = re.search(r"https://[^\s\"'<>]+?\.m3u8(?:\?[^\s\"'<>]*)?", decoded)
        return match.group(0) if match else None
    if isinstance(value, dict):
        for child in value.values():
            url = _find_hls_url(child)
            if url:
                return url
    if isinstance(value, list):
        for child in value:
            url = _find_hls_url(child)
            if url:
                return url
    return None


def _resolve_ln24(channel: dict[str, Any], timeout: float) -> str:
    resolver = channel["resolver"]
    try:
        _, _, _, payload = _open_bytes(
            resolver["endpoint"],
            timeout,
            _headers(
                channel.get("request_headers"),
                accept="text/html,application/xhtml+xml,*/*;q=0.8",
            ),
        )
    except urllib.error.HTTPError as exc:
        raise ResolverError(_http_error_detail(exc)) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ResolverError(str(getattr(exc, "reason", exc))) from exc
    player_html = payload.decode("utf-8", errors="replace")
    url = _find_hls_url(player_html)
    if not url:
        normalized_player_html = html.unescape(player_html).lower()
        if "ce contenu n'est pas disponible dans votre région" in normalized_player_html:
            raise ResolverError(
                "official LN24 player denied this VPN exit server-side as outside its allowed region"
            )
        raise ResolverError("official LN24 player returned no HLS URL (player changed)")
    return url


def _resolve_npo(channel: dict[str, Any], timeout: float) -> str:
    resolver = channel["resolver"]
    token_url = resolver["endpoint"] + "?" + urllib.parse.urlencode(
        {"productId": resolver["product_id"]}
    )
    token_headers = _headers(
        channel.get("request_headers"), accept="application/json"
    )
    token_headers["User-Agent"] = "npostart-web-prod"
    try:
        _, _, _, token_payload = _open_bytes(token_url, timeout, token_headers)
        token_value = json.loads(token_payload.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ResolverError(f"NPO token: {_http_error_detail(exc)}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise ResolverError(f"NPO token: {exc}") from exc
    token = _find_jwt(token_value)
    if not token:
        raise ResolverError("NPO token endpoint returned no JWT")

    request_body = {
        "profileName": "hls",
        "drmType": "fairplay",
        "referrerUrl": resolver["referrer_url"],
        "ster": {
            "identifier": "npo-app-desktop",
            "deviceType": 4,
            "player": "web",
        },
    }
    stream_headers = _headers(channel.get("request_headers"), accept="*/*")
    stream_headers.update(
        {"Authorization": token, "Content-Type": "application/json"}
    )
    try:
        _, _, _, stream_payload = _open_bytes(
            resolver["stream_link_endpoint"],
            timeout,
            stream_headers,
            data=json.dumps(request_body, separators=(",", ":")).encode("utf-8"),
            method="POST",
        )
        stream_value = json.loads(stream_payload.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ResolverError(f"NPO stream-link: {_http_error_detail(exc)}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise ResolverError(f"NPO stream-link: {exc}") from exc
    url = _find_hls_url(stream_value)
    if not url:
        raise ResolverError("NPO stream-link returned no HLS URL")
    return url


def _resolve_srgssr_media_composition(
    channel: dict[str, Any], timeout: float
) -> str:
    resolver = channel["resolver"]
    separator = "&" if "?" in resolver["endpoint"] else "?"
    composition_url = resolver["endpoint"] + separator + urllib.parse.urlencode(
        {"onlyChapters": "false", "vector": "portalplay"}
    )
    try:
        _, _, _, payload = _open_bytes(
            composition_url,
            timeout,
            _headers(channel.get("request_headers"), accept="application/json"),
        )
        composition = json.loads(payload.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ResolverError(f"SRG SSR media composition: {_http_error_detail(exc)}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise ResolverError(f"SRG SSR media composition: {exc}") from exc

    chapters = composition.get("chapterList", []) if isinstance(composition, dict) else []
    hls_resources = [
        resource
        for chapter in chapters
        if isinstance(chapter, dict)
        for resource in chapter.get("resourceList", [])
        if isinstance(resource, dict)
        and resource.get("streaming") == "HLS"
        and isinstance(resource.get("url"), str)
        and ".m3u8" in resource["url"]
    ]
    if not hls_resources:
        block_reason = composition.get("blockReason") if isinstance(composition, dict) else None
        suffix = f" ({block_reason})" if block_reason else ""
        raise ResolverError(f"official SRG SSR response returned no HLS resource{suffix}")
    preferred = max(
        hls_resources,
        key=lambda resource: (
            resource.get("presentation") == "DEFAULT",
            resource.get("quality") == "HD",
            resource.get("protocol") == "HLS-DVR",
        ),
    )
    return preferred["url"]


def _resolve_rtve_live_player(channel: dict[str, Any], timeout: float) -> str:
    resolver = channel["resolver"]
    try:
        _, content_type, final_url, payload = _open_bytes(
            resolver["endpoint"],
            timeout,
            _headers(channel.get("request_headers")),
        )
    except urllib.error.HTTPError as exc:
        raise ResolverError(f"RTVE live player: {_http_error_detail(exc)}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ResolverError(f"RTVE live player: {getattr(exc, 'reason', exc)}") from exc
    manifest = payload.decode("utf-8", errors="replace").lstrip("\ufeff\r\n ")
    if not (manifest.startswith("#EXTM3U") or content_type in HLS_CONTENT_TYPES):
        raise ResolverError("official RTVE player returned no HLS manifest")
    return final_url


def _resolve_rai_mediapolis(channel: dict[str, Any], timeout: float) -> str:
    resolver = channel["resolver"]
    endpoint = urllib.parse.urlsplit(resolver["endpoint"])
    query = urllib.parse.parse_qsl(endpoint.query, keep_blank_values=True)
    query = [(key, value) for key, value in query if key != "output"]
    query.append(("output", str(resolver.get("output", "62"))))
    relinker_url = urllib.parse.urlunsplit(
        (endpoint.scheme, endpoint.netloc, endpoint.path, urllib.parse.urlencode(query), endpoint.fragment)
    )
    headers = _headers(channel.get("request_headers"), accept="application/json")
    headers["x-ua-token"] = str(resolver.get("ua_token", "null"))
    try:
        _, _, _, payload = _open_bytes(relinker_url, timeout, headers)
        response = json.loads(payload.decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        raise ResolverError(f"Rai Mediapolis: {_http_error_detail(exc)}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise ResolverError(f"Rai Mediapolis: {exc}") from exc

    candidates = [
        item.get("url")
        for item in response.get("playlist", [])
        if isinstance(item, dict) and item.get("type") in {"main", "content"}
    ]
    candidates.extend(response.get("video", []))
    for url in candidates:
        parsed = urllib.parse.urlparse(url) if isinstance(url, str) else None
        if parsed and parsed.scheme == "https" and parsed.netloc and ".m3u8" in parsed.path:
            return url
    raise ResolverError("official Rai Mediapolis response returned no main HLS URL")


def resolve_channel_url(channel: dict[str, Any], timeout: float) -> str:
    resolver = channel.get("resolver")
    if not resolver:
        return channel["stream_url"]
    if resolver["type"] == "ln24_player":
        return _resolve_ln24(channel, timeout)
    if resolver["type"] == "npo_player":
        return _resolve_npo(channel, timeout)
    if resolver["type"] == "rai_mediapolis":
        return _resolve_rai_mediapolis(channel, timeout)
    if resolver["type"] == "rtve_live_player":
        return _resolve_rtve_live_player(channel, timeout)
    if resolver["type"] == "srgssr_media_composition":
        return _resolve_srgssr_media_composition(channel, timeout)
    raise ResolverError(f"unsupported resolver type: {resolver.get('type')}")


def _variant_url(manifest: str, base_url: str) -> str | None:
    lines = [line.strip() for line in manifest.splitlines()]
    candidates: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        bandwidth_match = re.search(r"(?:AVERAGE-)?BANDWIDTH=(\d+)", line)
        bandwidth = int(bandwidth_match.group(1)) if bandwidth_match else 0
        for uri in lines[index + 1 :]:
            if not uri or uri.startswith("#"):
                continue
            candidates.append((bandwidth, urllib.parse.urljoin(base_url, uri)))
            break
    return max(candidates, default=(0, ""))[1] or None


def _segment_url(manifest: str, base_url: str) -> str | None:
    lines = [line.strip() for line in manifest.splitlines()]
    for line in lines:
        if line and not line.startswith("#"):
            return urllib.parse.urljoin(base_url, line)
    for line in lines:
        if line.startswith("#EXT-X-MAP:"):
            match = re.search(r'URI="([^"]+)"', line)
            if match:
                return urllib.parse.urljoin(base_url, match.group(1))
    return None


def probe_url(
    channel_id: str,
    url: str,
    timeout: float,
    channel_headers: dict[str, str] | None = None,
) -> ProbeResult:
    started = time.monotonic()
    headers = _headers(channel_headers)
    try:
        code, content_type, final_url, payload = _open_bytes(url, timeout, headers)
        manifest = payload.decode("utf-8", errors="replace").lstrip("\ufeff\r\n ")
        if not (manifest.startswith("#EXTM3U") or content_type in HLS_CONTENT_TYPES):
            elapsed_ms = round((time.monotonic() - started) * 1000)
            return ProbeResult(
                channel_id,
                "unhealthy",
                code,
                elapsed_ms,
                url,
                content_type,
                "response is not an HLS manifest",
                "manifest",
            )

        media_url = final_url
        media_manifest = manifest
        stage_path = ["manifest"]
        for _ in range(3):
            variant = _variant_url(media_manifest, media_url)
            if not variant:
                break
            stage_path.append("variant")
            _, _, media_url, media_payload = _open_bytes(variant, timeout, headers)
            media_manifest = media_payload.decode("utf-8", errors="replace").lstrip(
                "\ufeff\r\n "
            )
            if not media_manifest.startswith("#EXTM3U"):
                raise ResolverError("selected variant is not an HLS manifest")

        segment_url = _segment_url(media_manifest, media_url)
        if not segment_url:
            elapsed_ms = round((time.monotonic() - started) * 1000)
            return ProbeResult(
                channel_id,
                "unhealthy",
                code,
                elapsed_ms,
                url,
                content_type,
                "HLS media manifest contains no playable segment",
                "media",
            )
        segment_code, segment_type, _, segment_payload = _open_bytes(
            segment_url,
            timeout,
            _headers(channel_headers, accept="video/*,audio/*,*/*;q=0.8", byte_range="bytes=0-1023"),
            limit=1024,
        )
        elapsed_ms = round((time.monotonic() - started) * 1000)
        if segment_code not in {200, 206} or not segment_payload:
            return ProbeResult(
                channel_id,
                "unhealthy",
                segment_code,
                elapsed_ms,
                url,
                segment_type,
                "media segment returned no bytes",
                "segment",
                segment_url,
            )
        stage_path.extend(["media", "segment"])
        return ProbeResult(
            channel_id,
            "healthy",
            segment_code,
            elapsed_ms,
            url,
            content_type,
            " → ".join(stage_path) + " validated",
            "segment",
            segment_url,
        )
    except urllib.error.HTTPError as exc:
        elapsed_ms = round((time.monotonic() - started) * 1000)
        return ProbeResult(
            channel_id,
            "unhealthy",
            exc.code,
            elapsed_ms,
            url,
            None,
            _http_error_detail(exc),
            "request",
        )
    except (urllib.error.URLError, TimeoutError, OSError, ResolverError) as exc:
        elapsed_ms = round((time.monotonic() - started) * 1000)
        reason = getattr(exc, "reason", exc)
        return ProbeResult(
            channel_id,
            "unhealthy",
            None,
            elapsed_ms,
            url,
            None,
            str(reason),
            "request",
        )


def probe_channel(channel: dict[str, Any], timeout: float) -> ProbeResult:
    resolver_elapsed_started = time.monotonic()
    try:
        primary_url = resolve_channel_url(channel, timeout)
    except ResolverError as exc:
        elapsed_ms = round((time.monotonic() - resolver_elapsed_started) * 1000)
        return ProbeResult(
            channel["id"],
            "unhealthy",
            None,
            elapsed_ms,
            None,
            None,
            f"resolver: {exc}",
            "resolver",
        )

    urls = [primary_url, *channel.get("alternate_stream_urls", [])]
    failures: list[str] = []
    elapsed_ms = 0
    last: ProbeResult | None = None
    for index, url in enumerate(urls, start=1):
        result = probe_url(
            channel["id"], url, timeout, channel.get("request_headers")
        )
        elapsed_ms += result.elapsed_ms
        if result.status == "healthy":
            detail = result.detail
            if index > 1:
                detail = f"alternate endpoint {index}: {detail}"
            return ProbeResult(
                result.channel_id,
                result.status,
                result.http_code,
                elapsed_ms,
                url,
                result.content_type,
                detail,
                result.stage,
                result.segment_url,
            )
        failures.append(f"endpoint {index}: {result.detail}")
        last = result

    assert last is not None
    return ProbeResult(
        last.channel_id,
        "unhealthy",
        last.http_code,
        elapsed_ms,
        last.final_url,
        last.content_type,
        "; ".join(failures),
        last.stage,
        last.segment_url,
    )


def probe_channels(
    channels: Iterable[dict[str, Any]], timeout: float, workers: int
) -> dict[str, ProbeResult]:
    channels = list(channels)
    results: dict[str, ProbeResult] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(probe_channel, channel, timeout): channel["id"]
            for channel in channels
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results[result.channel_id] = result
    return results


def m3u_escape(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace('"', "'").replace("\n", " ")


def channel_lines(
    channel: dict[str, Any],
    route_names: dict[str, str] | None = None,
    visible_vpn_flags: bool = False,
) -> list[str]:
    required_route = channel.get("required_route")
    vpn_label = (route_names or {}).get(required_route, required_route or "Direct")
    display_name = channel["name"]
    if required_route and visible_vpn_flags:
        visible_country = vpn_label.removeprefix("NordVPN ")
        display_name = f"{display_name} [VPN: {visible_country}]"
    attributes = [
        f'tvg-id="{m3u_escape(channel.get("tvg_id", ""))}"',
        f'tvg-name="{m3u_escape(display_name)}"',
        f'group-title="{m3u_escape(channel["group"])}"',
        f'uhf-country="{m3u_escape(channel["country"])}"',
        f'uhf-language="{m3u_escape(channel["language"])}"',
        f'uhf-source="{m3u_escape(channel["source_class"])}"',
        f'uhf-source-label="{m3u_escape(channel["source_label"])}"',
        f'uhf-distribution="{m3u_escape(channel["distribution"])}"',
        f'uhf-vpn-required="{str(bool(required_route)).lower()}"',
        f'uhf-vpn-route="{m3u_escape(required_route or "DIRECT")}"',
        f'uhf-vpn-label="{m3u_escape(vpn_label)}"',
    ]
    if channel.get("logo_url"):
        attributes.append(f'tvg-logo="{m3u_escape(channel["logo_url"])}"')
    playback_url = (
        f"resolve/{channel['id']}.m3u8" if channel.get("resolver") else channel["stream_url"]
    )
    return [f"#EXTINF:-1 {' '.join(attributes)},{display_name}", playback_url]


def ordered_channels(config: dict[str, Any], channels: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    group_index = {name: index for index, name in enumerate(config["group_order"])}
    return sorted(
        channels,
        key=lambda item: (
            group_index[item["group"]],
            item.get("curated_rank", 10_000),
            item["name"].casefold(),
        ),
    )


def write_playlist(
    path: Path,
    channels: Iterable[dict[str, Any]],
    route_names: dict[str, str] | None = None,
    visible_vpn_flags: bool = False,
) -> None:
    lines = ["#EXTM3U"]
    for channel in channels:
        lines.extend(channel_lines(channel, route_names, visible_vpn_flags))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def select_generated_channels(
    config: dict[str, Any],
    results: dict[str, ProbeResult],
    exclude_unhealthy: bool,
) -> list[dict[str, Any]]:
    selected = []
    for channel in config["channels"]:
        if not channel["enabled"]:
            continue
        result = results.get(channel["id"])
        if exclude_unhealthy and (result is None or result.status != "healthy"):
            continue
        selected_channel = channel
        if (
            result
            and result.status == "healthy"
            and not channel.get("resolver")
            and result.final_url != channel["stream_url"]
        ):
            selected_channel = dict(channel)
            selected_channel["stream_url"] = result.final_url
        selected.append(selected_channel)
    return ordered_channels(config, selected)


def generate_playlists(
    config: dict[str, Any],
    output_dir: Path,
    channels: list[dict[str, Any]],
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []

    route_names = config.get("route_names", {})
    visible_vpn_flags = bool(config.get("visible_vpn_flags", False))
    combined = output_dir / config.get("combined_file", "uhf-international.m3u")
    write_playlist(combined, channels, route_names, visible_vpn_flags)
    generated.append(combined)

    for group in config["group_order"]:
        path = output_dir / config["group_files"][group]
        write_playlist(
            path,
            (channel for channel in channels if channel["group"] == group),
            route_names,
            visible_vpn_flags,
        )
        generated.append(path)

    static_export_file = config.get("static_export_file")
    if static_export_file:
        selected_by_id = {channel["id"]: channel for channel in channels}
        static_channels = [
            selected_by_id[channel_id]
            for channel_id in config["approved_static_channel_ids"]
            if channel_id in selected_by_id
        ]
        static_path = output_dir / static_export_file
        write_playlist(static_path, static_channels, route_names, visible_vpn_flags)
        generated.append(static_path)
    return generated


def report_status(channel: dict[str, Any], results: dict[str, ProbeResult]) -> tuple[str, str, str]:
    result = results.get(channel["id"])
    if result:
        http = str(result.http_code) if result.http_code is not None else "—"
        return result.status, http, str(result.elapsed_ms)
    if not channel["enabled"]:
        return "disabled", "—", "—"
    return "not checked", "—", "—"


def md_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def vpn_routes_for(config: dict[str, Any], channel: dict[str, Any]) -> list[str]:
    country_routes = config.get("vpn_country_routes", {})
    if channel["country"] in country_routes:
        return country_routes[channel["country"]]
    profile_name = channel["vpn_policy"]
    return config["vpn_profiles"][profile_name]["routes"]


def route_label(config: dict[str, Any], route: str) -> str:
    return config.get("route_names", {}).get(route, route)


def detect_egress_country(timeout: float = 8) -> str:
    request = urllib.request.Request(
        "https://ipinfo.io/country",
        headers={"Accept": "text/plain", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        country = response.read(16).decode("ascii", errors="ignore").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", country):
        raise RuntimeError(f"unexpected egress-country response: {country!r}")
    return country


def write_health_report(
    path: Path,
    config: dict[str, Any],
    results: dict[str, ProbeResult],
    generated_channels: list[dict[str, Any]],
    network_label: str,
    network_route: str,
) -> None:
    now = dt.datetime.now(dt.timezone.utc).astimezone()
    healthy = sum(result.status == "healthy" for result in results.values())
    unhealthy = sum(result.status == "unhealthy" for result in results.values())
    lines = [
        "# International television source health",
        "",
        f"Generated: `{now.isoformat(timespec='seconds')}`",
        f"Network route: **{network_label}** (`{network_route}`)",
        "",
        f"Registry: **{len(config['channels'])}** channels  ",
        f"Published: **{len(generated_channels)}** enabled channels  ",
        f"Probed: **{len(results)}** — {healthy} healthy, {unhealthy} unhealthy",
        "",
        "A healthy probe means the complete live chain passed: top-level manifest, selected media playlist, and bytes from a current media segment. It does not grant rights or guarantee future availability.",
        "",
        "| Rank | ID | Channel | Group | Published | Probe | Stage | HTTP | ms | Source label | Distribution | Required route | Retry sequence | Note |",
        "|---:|---|---|---|:---:|---|---|---:|---:|---|---|---|---|---|",
    ]
    published_ids = {channel["id"] for channel in generated_channels}
    for channel in ordered_channels(config, config["channels"]):
        status, http, elapsed = report_status(channel, results)
        result = results.get(channel["id"])
        rank = channel.get("curated_rank", "—")
        lines.append(
            "| "
            + " | ".join(
                md_cell(value)
                for value in (
                    rank,
                    channel["id"],
                    channel["name"],
                    channel["group"],
                    "yes" if channel["id"] in published_ids else "no",
                    status,
                    result.stage if result else "—",
                    http,
                    elapsed,
                    channel["source_label"],
                    channel["distribution"],
                    route_label(config, channel.get("required_route") or "DIRECT"),
                    " → ".join(
                        route_label(config, route)
                        for route in vpn_routes_for(config, channel)
                    ),
                    channel["notes"],
                )
            )
            + " |"
        )

    unhealthy_results = [result for result in results.values() if result.status != "healthy"]
    if unhealthy_results:
        lines.extend(["", "## Probe failures", ""])
        channel_by_id = {channel["id"]: channel for channel in config["channels"]}
        for result in sorted(unhealthy_results, key=lambda item: item.channel_id):
            channel = channel_by_id[result.channel_id]
            lines.append(f"- **{channel['name']}**: {md_cell(result.detail)}")

    resolver_policy = (
        "- Dynamic official players are resolved at request time by the local hub server; the Mac and Apple TV must use the documented NordVPN route."
        if any(channel.get("resolver") for channel in config["channels"])
        else "- This static edition contains no dynamic player resolvers and needs no Mac-side process during playback."
    )
    lines.extend(
        [
            "",
            "## Policy",
            "",
            f"- This registry is approval-locked: generated playlists may contain only the {len(config['approved_channel_ids'])} approved channel IDs.",
            "- Mirror and distributor channels retain their source labels in the registry, M3U metadata, and this report.",
            resolver_policy,
            "- A failed direct Peru probe triggers the channel's NordVPN route sequence; geography is a retry condition, not an automatic rejection.",
            "- Use the same NordVPN country on Apple TV when playing a stream that only passed through that route.",
            "- Do not add scraped subscription channels, DRM manifests, credentials, tokens, or private URLs to this repository.",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--check", action="store_true", help="Probe enabled streams before generating")
    parser.add_argument(
        "--check-disabled",
        action="store_true",
        help="With --check, also probe quarantined/disabled candidates",
    )
    parser.add_argument(
        "--exclude-unhealthy",
        action="store_true",
        help="Publish only streams that passed this run; requires --check",
    )
    parser.add_argument("--timeout", type=float, default=None, help="Per-stream timeout in seconds")
    parser.add_argument("--workers", type=int, default=6, help="Concurrent probe workers")
    parser.add_argument(
        "--network-label",
        default=None,
        help="Label recorded in the health report, e.g. 'NordVPN France'",
    )
    parser.add_argument(
        "--network-route",
        default="DIRECT",
        help="Declared active route code, e.g. DIRECT, BE, NL, ES, IT, DE, or CH",
    )
    parser.add_argument(
        "--verify-egress-country",
        action="store_true",
        help="Refuse to label a report unless the public egress country matches --network-route",
    )
    parser.add_argument(
        "--channel",
        action="append",
        dest="channel_ids",
        help="Probe only this approved channel ID; repeat for multiple channels",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.exclude_unhealthy and not args.check:
        print("error: --exclude-unhealthy requires --check", file=sys.stderr)
        return 2
    if args.workers < 1:
        print("error: --workers must be at least 1", file=sys.stderr)
        return 2

    try:
        config = load_config(args.config)
        validate_config(config)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    known_routes = {
        route
        for profile in config["vpn_profiles"].values()
        for route in profile["routes"]
    }
    if args.network_route not in known_routes:
        print(f"error: unknown --network-route {args.network_route!r}", file=sys.stderr)
        return 2
    network_label = args.network_label or route_label(config, args.network_route)
    if args.verify_egress_country:
        expected_country = config.get("route_country_codes", {}).get(args.network_route)
        try:
            actual_country = detect_egress_country()
        except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as exc:
            print(f"error: could not verify egress country: {exc}", file=sys.stderr)
            return 1
        if expected_country and actual_country != expected_country:
            print(
                f"error: route {args.network_route} expects {expected_country}, "
                f"but public egress is {actual_country}",
                file=sys.stderr,
            )
            return 2

    known_channel_ids = {channel["id"] for channel in config["channels"]}
    requested_channel_ids = set(args.channel_ids or [])
    unknown_channel_ids = requested_channel_ids - known_channel_ids
    if unknown_channel_ids:
        print(
            "error: unknown --channel IDs: " + ", ".join(sorted(unknown_channel_ids)),
            file=sys.stderr,
        )
        return 2

    results: dict[str, ProbeResult] = {}
    if args.check:
        timeout = args.timeout or float(config.get("default_timeout_seconds", 10))
        probe_set = [
            channel
            for channel in config["channels"]
            if (channel["enabled"] or args.check_disabled)
            and (not requested_channel_ids or channel["id"] in requested_channel_ids)
        ]
        results = probe_channels(probe_set, timeout=timeout, workers=args.workers)

    selected = select_generated_channels(config, results, args.exclude_unhealthy)
    generated = generate_playlists(config, args.output_dir, selected)
    write_health_report(
        args.report,
        config,
        results,
        selected,
        network_label,
        args.network_route,
    )

    print(f"Registry valid: {len(config['channels'])} channels")
    if args.check:
        healthy = sum(result.status == "healthy" for result in results.values())
        print(f"Health check: {healthy}/{len(results)} healthy")
    print(f"Published: {len(selected)} channels")
    for path in generated:
        print(path)
    print(args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
