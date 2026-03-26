import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urljoin, urlparse

import requests

CONFIG_PATH = Path(os.getenv("APP_CONFIG_PATH", "/app/data/config.json"))
DEFAULT_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"


def ensure_parent(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "default_proxy": "",
        "auto_retry_enabled": False,
        "auto_retry_delay_seconds": 30,
        "auto_retry_max_attempts": 2,
    }


def save_config(cfg: dict):
    ensure_parent(CONFIG_PATH)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def build_headers(referer: Optional[str] = None, user_agent: Optional[str] = None) -> dict:
    headers = {"User-Agent": user_agent or DEFAULT_UA}
    if referer:
        headers["Referer"] = referer
    return headers


def build_proxies(proxy: Optional[str]) -> Optional[dict]:
    if not proxy:
        return None
    proxy = proxy.strip()
    if not proxy:
        return None
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", proxy):
        proxy = f"http://{proxy}"
    return {"http": proxy, "https": proxy}


def is_m3u8_url(url: str) -> bool:
    parsed = urlparse(url)
    path = (parsed.path or "").lower()
    return path.endswith(".m3u8") or ".m3u8?" in url.lower() or "m3u8" in path


def dedupe_keep_order(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def dedupe_stream_options(items: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for item in items:
        url = item.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        result.append(item)
    return result


def extract_title_from_html(html: str) -> Optional[str]:
    candidates = []
    patterns = [
        r'<meta\s+property="og:title"\s+content="([^"]+)"',
        r'<meta\s+name="twitter:title"\s+content="([^"]+)"',
        r'<meta\s+name="title"\s+content="([^"]+)"',
        r'<h1[^>]*>(.*?)</h1>',
        r'<title>(.*?)</title>',
    ]

    generic_markers = [
        "想爱爱就上有爱爱",
        "uaa.com｜有爱爱",
        "在线观看 | UAA视频",
        "有爱爱为您提供优质的成人内容",
    ]

    suffix_patterns = [
        r"\s*[|｜]\s*51吃瓜网.*$",
        r"\s*[|｜]\s*UAA视频\s*$",
        r"\s*[|｜]\s*有爱爱\s*$",
    ]

    def clean_title(raw: str) -> str:
        title = unescape(re.sub(r"<[^>]+>", " ", raw))
        title = re.sub(r"\s+", " ", title).strip()
        for suffix_pat in suffix_patterns:
            title = re.sub(suffix_pat, "", title).strip()
        return title

    for pat in patterns:
        for match in re.findall(pat, html, re.IGNORECASE | re.DOTALL):
            title = clean_title(match)
            if title:
                candidates.append(title)

    if not candidates:
        return None

    non_generic = [
        title for title in candidates
        if not any(marker in title for marker in generic_markers)
    ]
    pool = dedupe_keep_order(non_generic or candidates)
    pool.sort(key=lambda x: len(x), reverse=True)
    return pool[0]


def extract_m3u8_from_html(html: str):
    patterns = [
        r'https?://[^"\'\s>]+\.m3u8(?:\?[^"\'\s>]*)?',
        r'"(//[^"\']+\.m3u8(?:\?[^"\']*)?)"',
        r"'(//[^'\"]+\.m3u8(?:\?[^'\"]*)?)'",
        r'https?:\\/\\/.*?\.m3u8(?:[^"\'\s>]*)?',
        r'"url"\s*:\s*"(https?:\\/\\/.*?\.m3u8(?:[^"\\]*)?)"',
    ]
    found = []
    for pat in patterns:
        for match in re.findall(pat, html, re.IGNORECASE):
            candidate = match if isinstance(match, str) else match[0]
            candidate = candidate.replace("\\/", "/")
            if candidate.startswith("//"):
                candidate = "https:" + candidate
            found.append(candidate)

    for raw_cfg in re.findall(r"data-config='([^']+)'", html, re.IGNORECASE):
        try:
            cfg = json.loads(raw_cfg)
            video = cfg.get("video") or {}
            candidate = video.get("url")
            if isinstance(candidate, str) and ".m3u8" in candidate:
                found.append(candidate)
        except Exception:
            pass
    return dedupe_keep_order(found)


def fetch_webpage_html(url: str, referer: Optional[str] = None, user_agent: Optional[str] = None, proxy: Optional[str] = None) -> str:
    headers = build_headers(referer, user_agent)
    proxies = build_proxies(proxy)
    resp = requests.get(url, headers=headers, proxies=proxies, timeout=30)
    resp.raise_for_status()
    html = resp.text or ""
    if html.strip():
        return html

    cmd = ["curl", "-L", "--max-time", "30"]
    if user_agent:
        cmd += ["-A", user_agent]
    else:
        cmd += ["-A", "Mozilla/5.0"]
    if referer:
        cmd += ["-e", referer]
    if proxy:
        cmd += ["-x", proxy]
    cmd.append(url)
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="ignore")
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout
    return html


def probe_webpage(url: str, referer: Optional[str] = None, user_agent: Optional[str] = None, proxy: Optional[str] = None) -> dict:
    html = fetch_webpage_html(url, referer, user_agent, proxy)
    streams = extract_m3u8_from_html(html)
    return {
        "streams": streams,
        "stream_options": [{"url": s, "source": "html"} for s in streams],
        "title": extract_title_from_html(html),
    }


def extract_info_with_ytdlp(url: str, referer: Optional[str] = None, user_agent: Optional[str] = None, proxy: Optional[str] = None) -> dict:
    cmd = ["yt-dlp", "-J", "--no-warnings", "--skip-download"]
    if referer:
        cmd += ["--add-header", f"Referer:{referer}"]
    if user_agent:
        cmd += ["--add-header", f"User-Agent:{user_agent}"]
    if proxy:
        cmd += ["--proxy", proxy]
    cmd.append(url)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "yt-dlp failed")
    return json.loads(proc.stdout)


def build_stream_option(url: str, meta: Optional[dict] = None, source: str = "unknown") -> dict:
    meta = meta or {}
    filesize = meta.get("filesize") or meta.get("filesize_approx")
    width = meta.get("width")
    height = meta.get("height")
    format_note = meta.get("format_note") or meta.get("format_id")
    duration = meta.get("duration")
    tbr = meta.get("tbr")
    return {
        "url": url,
        "source": source,
        "filesize": filesize,
        "width": width,
        "height": height,
        "resolution": f"{width}x{height}" if width and height else None,
        "format_note": format_note,
        "duration": duration,
        "tbr": tbr,
    }


def choose_stream_url(info: dict, selected_url: Optional[str] = None, selected_index: Optional[int] = None) -> Optional[str]:
    streams = info.get("streams") or []
    if selected_url:
        for stream in streams:
            if stream == selected_url:
                return stream
        if ".m3u8" in selected_url:
            return selected_url
    if selected_index is not None and 0 <= selected_index < len(streams):
        return streams[selected_index]
    return streams[0] if streams else None


def discover_stream(
    url: str,
    referer: Optional[str] = None,
    user_agent: Optional[str] = None,
    proxy: Optional[str] = None,
    selected_url: Optional[str] = None,
    selected_index: Optional[int] = None,
) -> dict:
    info = {
        "source_url": url,
        "resolved_url": None,
        "title": None,
        "thumbnail": None,
        "is_m3u8": False,
        "extractor": None,
        "streams": [],
        "stream_options": [],
        "errors": [],
    }
    if is_m3u8_url(url):
        info.update({
            "resolved_url": url,
            "is_m3u8": True,
            "extractor": "direct",
            "streams": [url],
            "stream_options": [build_stream_option(url, source="direct")],
        })
        return info

    try:
        page = probe_webpage(url, referer, user_agent, proxy)
        streams = page.get("streams") or []
        if page.get("title"):
            info["title"] = page["title"]
        if streams:
            info["streams"] = dedupe_keep_order(info["streams"] + streams)
            info["stream_options"] = dedupe_stream_options(info["stream_options"] + (page.get("stream_options") or []))
            info.update({"resolved_url": choose_stream_url({"streams": info["streams"]}, selected_url, selected_index), "is_m3u8": True, "extractor": "html"})
    except Exception as exc:
        info["errors"].append(f"html 探测失败：{exc}")

    try:
        meta = extract_info_with_ytdlp(url, referer, user_agent, proxy)
        info["title"] = meta.get("title") or info.get("title")
        info["thumbnail"] = meta.get("thumbnail")

        extra_streams = []
        extra_options = []
        direct = meta.get("url")
        if isinstance(direct, str) and ".m3u8" in direct:
            extra_streams.append(direct)
            extra_options.append(build_stream_option(direct, meta, source="yt-dlp-direct"))
        for fmt in meta.get("formats", []) or []:
            fmt_url = fmt.get("url")
            if isinstance(fmt_url, str) and ".m3u8" in fmt_url:
                extra_streams.append(fmt_url)
                extra_options.append(build_stream_option(fmt_url, fmt, source="yt-dlp-format"))
        if extra_streams:
            info["streams"] = dedupe_keep_order(info["streams"] + extra_streams)
            info["stream_options"] = dedupe_stream_options(info["stream_options"] + extra_options)
            info.update({"resolved_url": choose_stream_url(info, selected_url, selected_index), "is_m3u8": True, "extractor": info.get("extractor") or "yt-dlp"})
    except Exception as exc:
        info["errors"].append(f"yt-dlp 探测失败：{exc}")

    if not info.get("resolved_url") and info.get("streams"):
        info["resolved_url"] = choose_stream_url(info, selected_url, selected_index)
        info["is_m3u8"] = True
        info["extractor"] = info.get("extractor") or "html"

    if not info.get("stream_options") and info.get("streams"):
        info["stream_options"] = [build_stream_option(s, source="fallback") for s in info["streams"]]

    return info


def ffmpeg_download(
    stream_url: str,
    output_path: Path,
    referer: Optional[str] = None,
    user_agent: Optional[str] = None,
    proxy: Optional[str] = None,
    progress_callback=None,
    should_cancel=None,
):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    header_lines = []
    if user_agent:
        header_lines.append(f"User-Agent: {user_agent}")
    if referer:
        header_lines.append(f"Referer: {referer}")

    cmd = ["ffmpeg", "-y", "-loglevel", "warning"]
    if proxy:
        cmd += ["-http_proxy", proxy]
    if header_lines:
        cmd += ["-headers", "\r\n".join(header_lines) + "\r\n"]
    cmd += [
        "-allowed_extensions", "ALL",
        "-allowed_segment_extensions", "ALL",
        "-protocol_whitelist", "file,http,https,tcp,tls,crypto,httpproxy",
        "-http_persistent", "1",
        "-http_multiple", "1",
        "-seg_max_retry", "8",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_on_network_error", "1",
        "-reconnect_delay_max", "5",
        "-progress", "pipe:1",
        "-i", stream_url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        "-movflags", "+faststart",
        str(output_path),
    ]

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    progress_lines = []
    stderr_lines = []
    stats = {
        "out_time_ms": 0,
        "total_size": 0,
        "speed": "",
        "bitrate": "",
    }

    def emit_progress():
        if not progress_callback:
            return
        out_time_ms = int(stats.get("out_time_ms") or 0)
        total_size = int(stats.get("total_size") or 0)
        speed = (stats.get("speed") or "").strip()
        bitrate = (stats.get("bitrate") or "").strip()
        pseudo_progress = max(8, min(95, 8 + out_time_ms // 15_000_000))
        parts = [f"已处理 {out_time_ms / 1000000:.1f}s"]
        if total_size > 0:
            parts.append(f"已下载 {total_size / 1024 / 1024:.1f}MB")
        if bitrate and bitrate != "N/A":
            parts.append(f"码率 {bitrate}")
        if speed and speed != "N/A":
            parts.append(f"速度 {speed}")
        progress_callback(pseudo_progress, "正在下载… " + " · ".join(parts))

    try:
        if process.stdout is not None:
            for line in process.stdout:
                if should_cancel and should_cancel():
                    process.terminate()
                    raise RuntimeError("下载已取消")
                line = line.strip()
                if not line:
                    continue
                progress_lines.append(line)
                if "=" in line:
                    key, value = line.split("=", 1)
                    if key in stats:
                        stats[key] = value
                if line.startswith("out_time_ms=") or line.startswith("total_size=") or line.startswith("bitrate=") or line.startswith("speed="):
                    emit_progress()
                elif line == "progress=end":
                    if progress_callback:
                        progress_callback(99, "正在收尾封装…")
        if process.stderr is not None:
            for raw_line in process.stderr:
                if should_cancel and should_cancel():
                    process.terminate()
                    raise RuntimeError("下载已取消")
                line = raw_line.rstrip()
                if line.strip():
                    stderr_lines.append(line)
    finally:
        returncode = process.wait()

    if returncode != 0:
        detail = "\n".join((stderr_lines or progress_lines)[-80:]).strip()
        if not detail:
            detail = f"ffmpeg exited with code {returncode}"
        raise RuntimeError(detail[-4000:])


def parse_simple_hls_manifest(manifest_text: str, manifest_url: str) -> dict:
    lines = [line.strip() for line in manifest_text.splitlines() if line.strip()]
    if not lines or lines[0] != "#EXTM3U":
        raise RuntimeError("不是合法的 HLS manifest")

    unsupported_tags = ("#EXT-X-KEY", "#EXT-X-MAP", "#EXT-X-BYTERANGE")
    for line in lines:
        if line.startswith(unsupported_tags):
            raise RuntimeError("复杂/加密 HLS，回退 ffmpeg")

    segments = []
    for line in lines:
        if line.startswith("#"):
            continue
        segment_url = urljoin(manifest_url, line)
        path = urlparse(segment_url).path.lower()
        if not (path.endswith(".ts") or path.endswith(".m4s") or path.endswith(".jpeg") or path.endswith(".jpg")):
            raise RuntimeError("非标准分片格式，回退 ffmpeg")
        segments.append(segment_url)

    if not segments:
        raise RuntimeError("manifest 中没有可下载分片")

    return {"segments": segments}


def aggressive_hls_download(
    manifest_url: str,
    output_path: Path,
    referer: Optional[str] = None,
    user_agent: Optional[str] = None,
    proxy: Optional[str] = None,
    progress_callback=None,
    should_cancel=None,
    segment_workers: int | None = None,
):
    manifest_resp = requests.get(
        manifest_url,
        headers=build_headers(referer, user_agent),
        proxies=build_proxies(proxy),
        timeout=30,
    )
    manifest_resp.raise_for_status()
    parsed = parse_simple_hls_manifest(manifest_resp.text, manifest_url)
    segments = parsed["segments"]
    total_segments = len(segments)

    if segment_workers is None:
        if total_segments >= 500:
            segment_workers = 16
        elif total_segments >= 240:
            segment_workers = 12
        elif total_segments >= 120:
            segment_workers = 8
        else:
            segment_workers = 6

    tmp_dir = output_path.parent / f".{output_path.stem}.parts"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    downloaded_bytes = 0
    start_time = time.time()
    session = requests.Session()
    session.headers.update(build_headers(referer, user_agent))
    session.proxies.update(build_proxies(proxy) or {})

    def fetch_one(index_url):
        index, seg_url = index_url
        last_error = None
        for attempt in range(1, 4):
            try:
                seg_resp = session.get(seg_url, timeout=45)
                seg_resp.raise_for_status()
                seg_path = tmp_dir / f"{index:06d}.ts"
                seg_path.write_bytes(seg_resp.content)
                return index, seg_path, len(seg_resp.content), attempt
            except Exception as exc:
                last_error = exc
                time.sleep(min(1.5 * attempt, 4))
        raise RuntimeError(f"分片 {index + 1} 重试 3 次仍失败: {last_error}")

    try:
        with ThreadPoolExecutor(max_workers=segment_workers, thread_name_prefix="mt-seg") as executor:
            futures = [executor.submit(fetch_one, item) for item in enumerate(segments)]
            for future in as_completed(futures):
                if should_cancel and should_cancel():
                    raise RuntimeError("下载已取消")
                _index, _seg_path, size, attempt = future.result()
                downloaded += 1
                downloaded_bytes += size
                if progress_callback:
                    elapsed = max(0.1, time.time() - start_time)
                    speed_mb = downloaded_bytes / 1024 / 1024 / elapsed
                    progress = max(8, min(95, int(downloaded / total_segments * 95)))
                    retry_note = f" · 重试 {attempt - 1} 次成功" if attempt > 1 else ""
                    progress_callback(progress, f"激进模式下载中… 并发 {segment_workers} · 分片 {downloaded}/{total_segments} · 已下载 {downloaded_bytes / 1024 / 1024:.1f}MB · 速度 {speed_mb:.2f} MB/s{retry_note}")

        concat_file = tmp_dir / "concat.txt"
        concat_file.write_text("".join([f"file '{(tmp_dir / f'{i:06d}.ts').as_posix()}'\n" for i in range(total_segments)]), encoding="utf-8")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy",
            "-bsf:a", "aac_adtstoasc",
            "-movflags", "+faststart",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or f"ffmpeg concat failed: {result.returncode}").strip()
            raise RuntimeError(detail[-4000:])
        if progress_callback:
            progress_callback(99, "激进模式收尾封装…")
    finally:
        session.close()
        for part in tmp_dir.glob("*"):
            try:
                part.unlink()
            except Exception:
                pass
        try:
            tmp_dir.rmdir()
        except Exception:
            pass


def normalize_filename(name: str) -> str:
    name = (name or "").strip() or "output.mp4"
    name = re.sub(r"[\\/:*?\"<>|]+", "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name if name.lower().endswith(".mp4") else f"{name}.mp4"


def build_media_proxy_url(proxy_prefix: str, target_url: str, referer: str | None = None, user_agent: str | None = None, proxy: str | None = None) -> str:
    parsed_target = urlparse(target_url)
    if parsed_target.netloc == "video.xchina.download" and parsed_target.path.startswith("/ts/"):
        target_url = parsed_target._replace(netloc="cdn.xchina.download").geturl()
        parsed_target = urlparse(target_url)
    filename = Path(parsed_target.path).name or "segment.bin"
    safe_name = quote(filename, safe='._-')
    params = [f"target={quote(target_url, safe=':/?&=%._-')}"]
    if referer:
        params.append(f"referer={quote(referer, safe=':/?&=%._-')}")
    if user_agent:
        params.append(f"user_agent={quote(user_agent, safe=':/?&=%._-')}")
    if proxy:
        params.append(f"proxy={quote(proxy, safe=':/?&=%._-')}")
    separator = '&' if '?' in proxy_prefix else '?'
    return f"{proxy_prefix}{safe_name}{separator}{'&'.join(params)}"


def rewrite_m3u8_manifest(manifest_text: str, manifest_url: str, proxy_prefix: str, referer: str | None = None, user_agent: str | None = None, proxy: str | None = None) -> str:
    lines = []
    for line in manifest_text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            if raw.startswith("#EXT-X-KEY") and 'URI="' in raw:
                line = re.sub(
                    r'URI="([^"]+)"',
                    lambda m: f'URI="{build_media_proxy_url(proxy_prefix, urljoin(manifest_url, m.group(1)), referer, user_agent, proxy)}"',
                    line,
                )
            lines.append(line)
            continue
        abs_url = urljoin(manifest_url, raw)
        lines.append(build_media_proxy_url(proxy_prefix, abs_url, referer, user_agent, proxy))
    return "\n".join(lines)
