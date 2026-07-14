import copy
import json
import os
import uuid
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urljoin, urlparse

import requests


# ── FlareSolverr Client ──────────────────────────────────────────────
# 当 requests/curl 遇到 Cloudflare 盾时，回退到 flaresolverr 跳盾解析。

_FLARESOLVERR_URL = os.getenv("FLARESOLVERR_URL", "http://localhost:8191").rstrip("/")
_FLARESOLVERR_ENABLED = os.getenv("FLARESOLVERR_ENABLED", "true").lower() in ("1", "true", "yes")
_FLARESOLVERR_TIMEOUT = int(os.getenv("FLARESOLVERR_TIMEOUT", "60"))


def _resolve_flare_config():
    """从配置文件读取 flaresolverr 设置，优先于环境变量。"""
    try:
        cfg = load_config()
        enabled = cfg.get("flaresolverr_enabled")
        if enabled is None:
            enabled = _FLARESOLVERR_ENABLED
        elif isinstance(enabled, str):
            enabled = enabled.lower() in ("1", "true", "yes")
        url = cfg.get("flaresolverr_url") or os.getenv("FLARESOLVERR_URL", "http://localhost:8191")
        timeout = cfg.get("flaresolverr_timeout") or _FLARESOLVERR_TIMEOUT
        return bool(enabled), url.rstrip("/") if url else "", int(timeout)
    except Exception:
        return _FLARESOLVERR_ENABLED, _FLARESOLVERR_URL, _FLARESOLVERR_TIMEOUT


def extract_twitter_fallback_streams(html: str) -> list[str]:
    patterns = [
        # video.twimg.com (传统视频域)
        r'https?://video\.twimg\.com/[^"\'\s>]+\.(?:m3u8|mp4)(?:\?[^"\'\s>]*)?',
        r'https?:\\/\\/video\.twimg\.com\\/.*?\.(?:m3u8|mp4)(?:[^"\'\s>]*)?',
        # pbs.twimg.com 的视频（e.g., /ext_tw_video/.../pu/vid/.../abcd.mp4）
        r'https?://pbs\.twimg\.com/ext_tw_video/[^"\'\s>]+\.mp4(?:\?[^"\'\s>]*)?',
        r'https?:\\/\\/pbs\.twimg\.com\\/ext_tw_video\\/.*?\.mp4(?:[^"\\]*)?',
        # 通用 playbackUrl
        r'"playbackUrl"\s*:\s*"(https?:\\/\\/video\.twimg\.com\\/.*?(?:m3u8|mp4)(?:[^"\\]*)?)"',
        # variants 块
        r'"video_info".*?"variants"\s*:\s*\[(.*?)\]',
    ]
    found = []
    for pat in patterns[:4]:
        for match in re.findall(pat, html, re.IGNORECASE):
            candidate = match if isinstance(match, str) else match[0]
            candidate = candidate.replace('\\/', '/')
            found.append(candidate)

    variants_blocks = re.findall(patterns[4], html, re.IGNORECASE | re.DOTALL)
    for block in variants_blocks:
        for url in re.findall(r'https?:\\/\\/video\.twimg\\.com\\/.*?(?:m3u8|mp4)(?:[^"\\]*)?', block, re.IGNORECASE):
            found.append(url.replace('\\/', '/'))

    # 过滤有效视频域
    cleaned = []
    for candidate in found:
        if not isinstance(candidate, str):
            continue
        if 'video.twimg.com/' in candidate or 'pbs.twimg.com/ext_tw_video/' in candidate:
            cleaned.append(candidate)
    return dedupe_keep_order(cleaned)


def extract_douyin_share_streams(html: str) -> tuple[list[str], list[dict]]:
    found = []
    options = []
    for match in re.finditer(r'"play_addr"\s*:\s*\{.*?"url_list"\s*:\s*\[(.*?)\]', html, re.IGNORECASE | re.DOTALL):
        block = match.group(1)
        urls = re.findall(r'"(https:\\u002F\\u002F[^"]+)"', block)
        for raw in urls:
            playwm = raw.replace('\\u002F', '/').replace('\\u0026', '&')
            play = playwm.replace('/playwm/', '/play/') if '/playwm/' in playwm else playwm
            preferred = play or playwm
            source = 'douyin-mobile-play' if preferred == play else 'douyin-mobile-playwm'
            if preferred and preferred not in found:
                found.append(preferred)
                options.append(build_stream_option(preferred, source=source))
    return dedupe_keep_order(found), dedupe_stream_options(options)


def extract_douyin_title_from_html(html: str) -> Optional[str]:
    patterns = [
        r'"desc"\s*:\s*"((?:\\.|[^"\\])+)"',
        r'"share_info"\s*:\s*\{.*?"share_desc"\s*:\s*"((?:\\.|[^"\\])+)"',
    ]

    def clean_text(raw: str) -> str:
        try:
            text = json.loads(f'"{raw}"')
        except Exception:
            text = raw.encode('utf-8', 'ignore').decode('unicode_escape', 'ignore')
        text = re.sub(r'\s+', ' ', str(text or '')).strip()
        return text

    for pat in patterns:
        m = re.search(pat, html, re.IGNORECASE | re.DOTALL)
        if m:
            raw = m.group(1)
            text = clean_text(raw)
            if text:
                return text
    return None
