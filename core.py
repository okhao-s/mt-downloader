import copy
import json
import logging
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urljoin, urlparse, urlsplit

import requests

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("mt")


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


class FlareSolverrClient:
    """轻量级 FlareSolverr API 客户端。"""

    @staticmethod
    def solve(url: str, referer: Optional[str] = None, user_agent: Optional[str] = None, proxy: Optional[str] = None) -> Optional[str]:
        """
        通过 flaresolverr 解析受 Cloudflare 保护的页面，返回 HTML 正文。
        失败返回 None。
        """
        enabled, flare_url, timeout = _resolve_flare_config()
        if not enabled or not flare_url:
            return None

        payload = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": 60000,
        }
        if referer:
            payload["referrer"] = referer
        if user_agent:
            payload["userAgent"] = user_agent
        if proxy:
            payload["proxy"] = {"url": proxy}

        try:
            resp = requests.post(
                f"{flare_url}/v1",
                json=payload,
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status")
            if status != "ok":
                return None
            solution = data.get("solution") or {}
            # 优先返回解析后的 HTML
            return solution.get("response") or solution.get("url")
        except Exception as exc:
            logger.debug(f"[flaresolverr] solve failed: {exc}")
            return None

    @staticmethod
    def is_cf_shield(html: str) -> bool:
        """
        检测 HTML 是否为 Cloudflare 挑战页。
        常见特征：cf-challenge、__cfduid、cf-ray、"Sorry, you have been blocked"
        """
        if not html:
            return False
        lower = html.lower()
        indicators = [
            "cloudflare", "cf-challenge", "__cfduid", "cf-ray",
            "sorry, you have been blocked", "see cloudflare performance & security solutions",
            "checking your browser before accessing", "ddos protection by cloudflare",
            "ray ID:", "cloudflare rings",
        ]
        return any(ind in lower for ind in indicators)


CONFIG_PATH = Path(os.getenv("APP_CONFIG_PATH", "/app/data/config.json"))
DEFAULT_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
INSTAGRAM_FALLBACK_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
X_GQL_BEARER = os.getenv("X_GQL_BEARER", "").strip() or "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
X_TWEET_RESULT_BY_REST_ID_QUERY = "sBoAB5nqJTOyR9sZ5qVLsw"
DEFAULT_PROXY_BYPASS_PLATFORMS = {"douyin", "bilibili"}
YTDLP_INFO_TIMEOUT = int(os.getenv("YTDLP_INFO_TIMEOUT", "45"))
YTDLP_SOCKET_TIMEOUT = int(os.getenv("YTDLP_SOCKET_TIMEOUT", "30"))
YTDLP_DOWNLOAD_TIMEOUT = int(os.getenv("YTDLP_DOWNLOAD_TIMEOUT", "1800"))
AGGRESSIVE_HLS_TIMEOUT = int(os.getenv("AGGRESSIVE_HLS_TIMEOUT", "1800"))
DISCOVER_STREAM_CACHE_TTL = max(1, int(os.getenv("DISCOVER_STREAM_CACHE_TTL", "15")))
_DISCOVER_STREAM_CACHE: dict[tuple, tuple[float, dict]] = {}
_DISCOVER_STREAM_CACHE_LOCK = threading.Lock()


def ensure_parent(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)


def _keep_sentinel(value: str) -> bool:
    return value == "__KEEP__"


def normalize_cookie_config(cfg: dict | None) -> dict:
    cfg = dict(cfg or {})
    xck = cfg.get("xck") or cfg.get("twitter_cookies_path") or "/app/data/cookies/twitter.cookies.txt"
    youtubeck = cfg.get("youtubeck") or cfg.get("youtube_cookies_path") or "/app/data/cookies/youtube.cookies.txt"
    bilibilick = cfg.get("bilibilick") or cfg.get("bilibili_cookies_path") or "/app/data/cookies/bilibili.cookies.txt"
    douyinck = cfg.get("douyinck") or cfg.get("douyin_cookies_path") or "/app/data/cookies/douyin.cookies.txt"
    cfg["xck"] = xck
    cfg["youtubeck"] = youtubeck
    cfg["bilibilick"] = bilibilick
    cfg["douyinck"] = douyinck
    cfg["twitter_cookies_path"] = xck
    cfg["youtube_cookies_path"] = youtubeck
    cfg["bilibili_cookies_path"] = bilibilick
    cfg["douyin_cookies_path"] = douyinck
    cfg["wecom_enabled"] = bool(cfg.get("wecom_enabled", False))
    cfg["wecom_corp_id"] = str(cfg.get("wecom_corp_id") or "")
    cfg["wecom_agent_id"] = str(cfg.get("wecom_agent_id") or "")
    # Preserve __KEEP__ sentinel — don't overwrite real secrets with empty string
    secret = str(cfg.get("wecom_secret") or "")
    cfg["wecom_secret"] = secret if not _keep_sentinel(secret) else cfg.get("wecom_secret", "")
    token = str(cfg.get("wecom_token") or "")
    cfg["wecom_token"] = token if not _keep_sentinel(token) else cfg.get("wecom_token", "")
    aes_key = str(cfg.get("wecom_encoding_aes_key") or "")
    cfg["wecom_encoding_aes_key"] = aes_key if not _keep_sentinel(aes_key) else cfg.get("wecom_encoding_aes_key", "")
    cfg["wecom_callback_url"] = str(cfg.get("wecom_callback_url") or "")
    # Remove deprecated config keys (if any)
    if "wecom_forward_token" in cfg:
        logger.warning("Config key 'wecom_forward_token' is deprecated and will be ignored. Please remove it from config.json.")
        cfg.pop("wecom_forward_token", None)
    return cfg


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return normalize_cookie_config(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    return normalize_cookie_config({
        "default_proxy": "",
        "auto_retry_enabled": False,
        "auto_retry_delay_seconds": 30,
        "auto_retry_max_attempts": 2,
        "xck": "/app/data/cookies/twitter.cookies.txt",
        "youtubeck": "/app/data/cookies/youtube.cookies.txt",
        "bilibilick": "/app/data/cookies/bilibili.cookies.txt",
        "douyinck": "/app/data/cookies/douyin.cookies.txt",
        "wecom_enabled": False,
        "wecom_corp_id": "",
        "wecom_agent_id": "",
        "wecom_secret": "",
        "wecom_token": "",
        "wecom_encoding_aes_key": "",
        "wecom_callback_url": "",
    })


def save_config(cfg: dict):
    ensure_parent(CONFIG_PATH)
    normalized = normalize_cookie_config(cfg)
    CONFIG_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")


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


def route_proxy_for_url(url: Optional[str], proxy: Optional[str]) -> Optional[str]:
    normalized_proxy = (proxy or "").strip()
    if not normalized_proxy:
        return None
    platform = detect_platform(url)
    if platform in DEFAULT_PROXY_BYPASS_PLATFORMS:
        return None
    return normalized_proxy


def is_m3u8_url(url: str) -> bool:
    parsed = urlparse(url)
    path = (parsed.path or "").lower()
    return path.endswith(".m3u8") or ".m3u8?" in url.lower() or "m3u8" in path


def is_direct_media_url(url: str) -> bool:
    parsed = urlparse(url)
    path = (parsed.path or "").lower()
    return any(path.endswith(ext) for ext in (".mp4", ".m4v", ".mov", ".webm", ".mkv", ".flv", ".avi"))


def is_direct_image_url(url: str) -> bool:
    parsed = urlparse(url)
    path = (parsed.path or "").lower()
    if any(path.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")):
        return True
    query = parsed.query or ""
    if "format=" in query and "pbs.twimg.com/media/" in url:
        return True
    return False


def detect_platform(url: Optional[str]) -> str:
    value = str(url or "").lower()
    if "x.com/" in value or "twitter.com/" in value:
        return "x"
    if "youtube.com/" in value or "youtu.be/" in value:
        return "youtube"
    if "bilibili.com/" in value or "b23.tv/" in value:
        return "bilibili"
    if any(token in value for token in [
        "instagram.com/",
        "instagr.am/",
        "cdninstagram.com/",
    ]):
        return "instagram"
    if any(token in value for token in [
        "douyin.com/",
        "iesdouyin.com/",
        "v.douyin.com/",
        "aweme.snssdk.com/aweme/v1/play",
        "/aweme/v1/play/",
        "/aweme/v1/playwm/",
        "douyinvod.com/",
        ".zjcdn.com/",
        "douyincdn.com/",
        "byteimg.com/",
        "douyinpic.com/",
    ]):
        return "douyin"
    if "xchina" in value:
        return "xchina"
    return "generic"


def prefers_best_stream(url: Optional[str]) -> bool:
    return detect_platform(url) in {"x", "youtube", "bilibili", "douyin", "instagram"}


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


def is_probably_audio_only_format(fmt: dict) -> bool:
    if not isinstance(fmt, dict):
        return False
    width = fmt.get("width")
    height = fmt.get("height")
    resolution = str(fmt.get("resolution") or "").lower()
    vcodec = str(fmt.get("vcodec") or "").lower()
    acodec = str(fmt.get("acodec") or "").lower()
    format_note = str(fmt.get("format_note") or "").lower()
    format_id = str(fmt.get("format_id") or "").lower()
    ext = str(fmt.get("ext") or "").lower()
    url = str(fmt.get("url") or "").lower()

    if width or height:
        return False
    if vcodec and vcodec not in {"none", "null", "unknown"}:
        return False
    if resolution == "audio only":
        return True
    if "audio" in format_note or "audio" in format_id:
        return True
    if "/mp4a/" in url:
        return True
    if acodec and acodec not in {"none", "null", "unknown"} and ext in {"m4a", "mp3", "aac"}:
        return True
    return False


def extract_title_from_html(html: str) -> Optional[str]:
    meta_candidates = []
    other_candidates = []

    generic_markers = [
        "想爱爱就上有爱爱",
        "uaa.com｜有爱爱",
        "在线观看 | UAA视频",
        "有爱爱为您提供优质的成人内容",
    ]

    failure_title_markers = [
        "javascript is not available",
        "please enable javascript",
        "something went wrong, but don’t fret",
        "something went wrong, but don't fret",
        "x.com",
        "twitter",
    ]

    suffix_patterns = [
        r"\s*[|｜]\s*51吃瓜网.*$",
        r"\s*[|｜]\s*UAA视频\s*$",
        r"\s*[|｜]\s*有爱爱\s*$",
        r"\s*[|｜]\s*抖音\s*$",
        r"\s*[|｜]\s*西瓜视频\s*$",
        r"\s*[-—–]\s*YouTube\s*$",
        r"\s*[-—–]\s*Bilibili\s*$",
    ]

    def collect_meta_title_candidates(source_html: str) -> list[str]:
        candidates = []
        for tag in re.findall(r"<meta\b[^>]*>", source_html, re.IGNORECASE):
            attrs = {
                key.lower(): value
                for key, _, value in re.findall(
                    r"([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*([\"'])(.*?)\2",
                    tag,
                    re.IGNORECASE | re.DOTALL,
                )
            }
            meta_key = (attrs.get("property") or attrs.get("name") or "").strip().lower()
            if meta_key in {"og:title", "twitter:title", "title"}:
                content = (attrs.get("content") or "").strip()
                if content:
                    candidates.append(content)
        return candidates

    def clean_title(raw: str) -> str:
        title = unescape(re.sub(r"<[^>]+>", " ", raw or ""))
        title = title.replace("\u200b", " ").replace("\xa0", " ")
        title = re.sub(r"[\r\n\t]+", " ", title)
        title = re.sub(r"\s+", " ", title).strip(" \t\r\n-_|｜")
        for suffix_pat in suffix_patterns:
            title = re.sub(suffix_pat, "", title, flags=re.IGNORECASE).strip(" \t\r\n-_|｜")
        return title

    def is_noise_title(title: str) -> bool:
        normalized = re.sub(r"\s+", " ", str(title or "")).strip().lower()
        if not normalized:
            return True
        return any(marker in normalized for marker in failure_title_markers)

    for match in collect_meta_title_candidates(html):
        title = clean_title(match)
        if title:
            meta_candidates.append(title)

    for match in re.findall(r'<h1[^>]*>(.*?)</h1>', html, re.IGNORECASE | re.DOTALL):
        title = clean_title(match)
        if title:
            other_candidates.append(title)

    for match in re.findall(r'<title>(.*?)</title>', html, re.IGNORECASE | re.DOTALL):
        title = clean_title(match)
        if title:
            other_candidates.append(title)

    candidates = meta_candidates + other_candidates
    if not candidates:
        return None

    non_generic = [
        title for title in candidates
        if not any(marker in title for marker in generic_markers) and not is_noise_title(title)
    ]
    if meta_candidates:
        preferred = [
            title for title in dedupe_keep_order(meta_candidates)
            if not any(marker in title for marker in generic_markers) and not is_noise_title(title)
        ]
        if preferred:
            preferred.sort(key=lambda x: (len(x) >= 4, len(x)), reverse=True)
            return preferred[0]
    pool = dedupe_keep_order(non_generic or [title for title in candidates if not is_noise_title(title)] or candidates)
    pool.sort(key=lambda x: (len(x) >= 4, len(x)), reverse=True)
    chosen = pool[0]
    return None if is_noise_title(chosen) else chosen


def extract_xchina_author(html: str) -> Optional[str]:
    # 从 xchina 视频页面提取作者名（模特）
    # 优先匹配 <a class="model-item">作者</a>
    m = re.search(r'<a[^>]*class=["\'][^"\']*model-item[^"\']*["\'][^>]*>([^<]+)</a>', html, re.IGNORECASE)
    if m:
        author = m.group(1).strip()
        if author:
            return author
    # 后备：匹配 <div class="model-item">作者</div>
    m = re.search(r'<div class="model-item"[^>]*>([^<]+)</div>', html, re.IGNORECASE)
    if m:
        author = m.group(1).strip()
        if author:
            return author
    return None


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
    html = ""
    try:
        resp = requests.get(url, headers=headers, proxies=proxies, timeout=(10, 30))
        resp.raise_for_status()
        html = resp.text or ""
        if html.strip():
            # 检测到 CF 盾 → 回退 flaresolverr
            if FlareSolverrClient.is_cf_shield(html):
                logger.debug(f"[fetch_webpage_html] CF shield detected, retrying via flaresolverr: {url}")
                cf_html = FlareSolverrClient.solve(url, referer, user_agent, proxy)
                if cf_html:
                    return cf_html
                logger.debug("[fetch_webpage_html] flaresolverr fallback failed, returning original HTML")
            return html
    except Exception:
        pass

    # requests 失败 → 尝试 curl
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
        curl_html = proc.stdout
        # curl 也检测 CF 盾
        if FlareSolverrClient.is_cf_shield(curl_html):
            logger.debug(f"[fetch_webpage_html] CF shield detected (curl), retrying via flaresolverr: {url}")
            cf_html = FlareSolverrClient.solve(url, referer, user_agent, proxy)
            if cf_html:
                return cf_html
        return curl_html
    return html


def extract_twitter_fallback_streams(html: str) -> list[str]:
    patterns = [
        # video.twimg.com (传统视频域)
        r'https?://video\.twimg\.com/[^"\'\s>]+\.(?:m3u8|mp4)(?:\?[^"\'\s>]*)?',
        r'https?:\\/\\/video\.twimg\.com\\/.*?\.(?:m3u8|mp4)(?:[^"\\]*)?',
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
        # Match both video.twimg.com and pbs.twimg.com
        for url in re.findall(r'https?:\\/\\/(?:video|pbs)\.twimg\.com\\/.*?(?:m3u8|mp4)(?:[^"\\]*)?', block, re.IGNORECASE):
            found.append(url.replace('\\/', '/'))

    # 过滤有效视频域
    cleaned = []
    for candidate in found:
        if not isinstance(candidate, str):
            continue
        if 'video.twimg.com/' in candidate or 'pbs.twimg.com/' in candidate:
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
        for match in re.findall(pat, html, re.IGNORECASE | re.DOTALL):
            title = clean_text(match)
            if title:
                return title
    return None


def normalize_douyin_share_url(url: str) -> str:
    match = re.search(r'/video/(\d+)', url)
    if match:
        return f'https://www.iesdouyin.com/share/video/{match.group(1)}/'
    modal = re.search(r'[?&]modal_id=(\d+)', url)
    if modal:
        return f'https://www.iesdouyin.com/share/video/{modal.group(1)}/'
    return url


def probe_webpage(url: str, referer: Optional[str] = None, user_agent: Optional[str] = None, proxy: Optional[str] = None) -> dict:
    effective_ua = user_agent
    effective_url = url
    is_douyin = detect_platform(url) == 'douyin'
    if is_douyin:
        effective_url = normalize_douyin_share_url(url)
        if not effective_ua:
            effective_ua = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'
    html = fetch_webpage_html(effective_url, referer, effective_ua, proxy)
    streams = extract_m3u8_from_html(html)
    stream_options = [{"url": s, "source": "html"} for s in streams]
    title = extract_title_from_html(html)
    author = None
    if 'xchina' in effective_url:
        author = extract_xchina_author(html)
    if is_douyin:
        dy_streams, dy_options = extract_douyin_share_streams(html)
        streams = dedupe_keep_order(streams + dy_streams)
        stream_options = dedupe_stream_options(stream_options + dy_options)
        title = extract_douyin_title_from_html(html) or title
    return {
        "streams": streams,
        "stream_options": stream_options,
        "title": title,
        "author": author,
    }


def parse_netscape_cookies(cookies_path: Optional[str]) -> dict:
    cookies = {}
    if not cookies_path:
        return cookies
    path = Path(cookies_path)
    if not path.exists():
        return cookies
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line or line.startswith('#'):
            continue
        parts = line.split('\t')
        if len(parts) >= 7:
            cookies[parts[5]] = parts[6]
    return cookies


def dig_first(value, predicate):
    if predicate(value):
        return value
    if isinstance(value, dict):
        for item in value.values():
            found = dig_first(item, predicate)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = dig_first(item, predicate)
            if found is not None:
                return found
    return None


def extract_x_images_from_graphql_payload(payload: dict) -> dict:
    result = {"title": None, "thumbnail": None, "images": [], "image_options": []}

    legacy = dig_first(payload, lambda x: isinstance(x, dict) and isinstance(x.get('extended_entities'), dict)) or {}
    extended = legacy.get('extended_entities') or {}
    media_list = extended.get('media') or []

    for media in media_list:
        if not isinstance(media, dict):
            continue
        media_type = str(media.get('type') or '').lower()
        image_url = media.get('media_url_https') or media.get('media_url')
        if media_type != 'photo' or not isinstance(image_url, str):
            continue
        result['thumbnail'] = result['thumbnail'] or image_url
        result['images'].append(image_url)
        result['image_options'].append({
            'url': image_url,
            'source': 'x-graphql-photo',
            'type': media_type,
            'width': media.get('original_info', {}).get('width') if isinstance(media.get('original_info'), dict) else None,
            'height': media.get('original_info', {}).get('height') if isinstance(media.get('original_info'), dict) else None,
        })

    note_tweet = dig_first(payload, lambda x: isinstance(x, dict) and (x.get('full_text') or x.get('text')))
    if isinstance(note_tweet, dict):
        result['title'] = note_tweet.get('full_text') or note_tweet.get('text')

    result['images'] = dedupe_keep_order(result['images'])
    return result


def extract_x_streams_from_graphql_payload(payload: dict) -> dict:
    result = {"title": None, "thumbnail": None, "author": None, "streams": [], "stream_options": [], "media_entries": []}

    # 提取作者/用户名
    core_result = dig_first(payload, lambda x: isinstance(x, dict) and isinstance(x.get('core'), dict) and isinstance(x.get('core').get('user_results'), dict)) or {}
    user_results = (core_result.get('core') or {}).get('user_results') or {}
    user_info = user_results.get('result') or {}
    legacy_user = user_info.get('legacy') or {}
    if legacy_user.get('name'):
        result['author'] = legacy_user['name']
    elif legacy_user.get('screen_name'):
        result['author'] = f"@{legacy_user['screen_name']}"

    legacy = dig_first(payload, lambda x: isinstance(x, dict) and isinstance(x.get('extended_entities'), dict)) or {}
    extended = legacy.get('extended_entities') or {}
    media_list = extended.get('media') or []

    for media_index, media in enumerate(media_list):
        if not isinstance(media, dict):
            continue
        media_type = str(media.get('type') or '').lower()
        if media_type not in {'video', 'animated_gif'}:
            continue
        result["thumbnail"] = result["thumbnail"] or media.get('media_url_https') or media.get('media_url')
        video_info = media.get('video_info') or {}
        variants = video_info.get('variants') or []
        media_options = []
        for variant in variants:
            variant_url = variant.get('url')
            if not isinstance(variant_url, str):
                continue
            if '.m3u8' not in variant_url and '.mp4' not in variant_url:
                continue
            bitrate = variant.get('bitrate')
            width = variant.get('width')
            height = variant.get('height')
            if (not width or not height) and isinstance(variant_url, str):
                size_match = re.search(r'/vid/[^/]+/(\d+)x(\d+)/', variant_url)
                if size_match:
                    width = int(size_match.group(1))
                    height = int(size_match.group(2))
            option = build_stream_option(variant_url, {
                'tbr': (float(bitrate) / 1000.0) if bitrate else None,
                'width': width,
                'height': height,
            }, source='x-graphql')
            media_options.append(option)

        if not media_options:
            continue

        media_info = {
            'streams': [item['url'] for item in media_options],
            'stream_options': media_options,
        }
        best_url = choose_best_stream_url(media_info)
        best_option = next((item for item in media_options if item.get('url') == best_url), media_options[0])
        media_entry = {
            'media_index': len(result['media_entries']),
            'tweet_media_index': media_index,
            'media_key': media.get('media_key') or media.get('id_str') or media.get('id'),
            'thumbnail': media.get('media_url_https') or media.get('media_url'),
            'streams': [item['url'] for item in media_options],
            'stream_options': dedupe_stream_options(media_options),
            'best_stream_url': best_option['url'],
            'best_stream_option': best_option,
        }
        result['media_entries'].append(media_entry)
        result['streams'].append(best_option['url'])
        result['stream_options'].append(best_option)

    note_tweet = dig_first(payload, lambda x: isinstance(x, dict) and (x.get('full_text') or x.get('text')))
    if isinstance(note_tweet, dict):
        result['title'] = note_tweet.get('full_text') or note_tweet.get('text')

    result['streams'] = dedupe_keep_order(result['streams'])
    result['stream_options'] = dedupe_stream_options(result['stream_options'])
    return result


def fetch_x_graphql_tweet_result(rest_id: str, cookies_path: Optional[str] = None, user_agent: Optional[str] = None, proxy: Optional[str] = None) -> dict:
    cookies = parse_netscape_cookies(cookies_path)
    ct0 = cookies.get('ct0')
    auth_token = cookies.get('auth_token')
    if not ct0 or not auth_token:
        raise RuntimeError('缺少 X 登录 cookies（ct0/auth_token）')

    variables = {
        'tweetId': str(rest_id),
        'withCommunity': False,
        'includePromotedContent': False,
        'withVoice': True,
    }
    features = {
        'responsive_web_graphql_exclude_directive_enabled': True,
        'verified_phone_label_enabled': False,
        'creator_subscriptions_tweet_preview_api_enabled': True,
        'responsive_web_graphql_timeline_navigation_enabled': True,
        'responsive_web_graphql_skip_user_profile_image_extensions_enabled': False,
        'premium_content_api_read_enabled': True,
        'communities_web_enable_tweet_community_results_fetch': True,
        'c9s_tweet_anatomy_moderator_badge_enabled': True,
        'responsive_web_grok_analyze_button_fetch_trends_enabled': False,
        'responsive_web_grok_analyze_post_followups_enabled': True,
        'responsive_web_jetfuel_frame': False,
        'responsive_web_grok_share_attachment_enabled': True,
        'responsive_web_grok_annotations_enabled': True,
        'articles_preview_enabled': True,
        'responsive_web_edit_tweet_api_enabled': True,
        'graphql_is_translatable_rweb_tweet_is_translatable_enabled': True,
        'view_counts_everywhere_api_enabled': True,
        'longform_notetweets_consumption_enabled': True,
        'responsive_web_twitter_article_tweet_consumption_enabled': True,
        'tweet_awards_web_tipping_enabled': False,
        'responsive_web_grok_show_grok_translated_post': False,
        'responsive_web_grok_analysis_button_from_backend': True,
        'creator_subscriptions_quote_tweet_preview_enabled': False,
        'freedom_of_speech_not_reach_fetch_enabled': True,
        'standardized_nudges_misinfo': True,
        'tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled': True,
        'longform_notetweets_rich_text_read_enabled': True,
        'longform_notetweets_inline_media_enabled': True,
        'responsive_web_media_download_video_enabled': False,
        'responsive_web_enhance_cards_enabled': False,
    }
    field_toggles = {
        'withArticleRichContentState': True,
        'withArticlePlainText': False,
        'withGrokAnalyze': False,
        'withDisallowedReplyControls': False,
    }
    endpoint = f'https://x.com/i/api/graphql/{X_TWEET_RESULT_BY_REST_ID_QUERY}/TweetResultByRestId'
    headers = {
        'authorization': X_GQL_BEARER,
        'x-csrf-token': ct0,
        'x-twitter-active-user': 'yes',
        'x-twitter-auth-type': 'OAuth2Session',
        'x-twitter-client-language': 'zh-cn',
        'user-agent': user_agent or DEFAULT_UA,
        'accept': '*/*',
        'referer': f'https://x.com/i/status/{rest_id}',
    }
    proxies = build_proxies(proxy)
    response = requests.get(
        endpoint,
        params={
            'variables': json.dumps(variables, separators=(',', ':')),
            'features': json.dumps(features, separators=(',', ':')),
            'fieldToggles': json.dumps(field_toggles, separators=(',', ':')),
        },
        headers=headers,
        cookies={
            'auth_token': auth_token,
            'ct0': ct0,
        },
        proxies=proxies,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def extract_info_with_ytdlp(url: str, referer: Optional[str] = None, user_agent: Optional[str] = None, proxy: Optional[str] = None, cookies_path: Optional[str] = None) -> dict:
    cmd = [
        "yt-dlp",
        "--ignore-config",
        "-J",
        "--no-warnings",
        "--skip-download",
        "--socket-timeout",
        str(YTDLP_SOCKET_TIMEOUT),
    ]
    # Auto-set Referer for xchina if not provided
    effective_referer = referer
    if 'xchina' in (url or '').lower():
        effective_referer = effective_referer or 'https://www.xchina.co/'
    if effective_referer:
        cmd += ["--add-header", f"Referer:{effective_referer}"]
    if user_agent:
        cmd += ["--add-header", f"User-Agent:{user_agent}"]
    if proxy:
        cmd += ["--proxy", proxy]
    if cookies_path and Path(cookies_path).exists():
        cmd += ["--cookies", cookies_path]
    cmd.append(url)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=YTDLP_INFO_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"yt-dlp 探测超时（>{YTDLP_INFO_TIMEOUT}s）") from exc
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "yt-dlp failed")
    return json.loads(proc.stdout)


def extract_info_with_ytdlp_flare(url: str, referer: Optional[str] = None, user_agent: Optional[str] = None, proxy: Optional[str] = None, cookies_path: Optional[str] = None) -> Optional[dict]:
    """
    yt-dlp 探测的 flaresolverr 增强版。
    当 yt-dlp 失败且疑似 CF 盾时，先用 flaresolverr 解析页面获得 cookies，
    然后注入到 yt-dlp 的 --cookies 参数中重试。
    """
    # 先正常尝试
    try:
        return extract_info_with_ytdlp(url, referer, user_agent, proxy, cookies_path)
    except Exception as exc:
        error_text = str(exc).lower()
        # 检查是否是 CF 相关问题
        if not any(kw in error_text for kw in ["cloudflare", "cf-", "challenge", "blocked", "captcha", "access denied", "http error 403"]):
            raise  # 不是 CF 问题，直接抛异常

        # 通过 flaresolverr 获取有效 cookies
        logger.debug(f"[ytdlp-flare] CF detected ({error_text[:100]}), trying flaresolverr: {url}")
        enabled, flare_url, timeout = _resolve_flare_config()
        if not enabled or not flare_url:
            raise

        try:
            resp = requests.post(
                f"{flare_url}/v1",
                json={"cmd": "request.get", "url": url, "maxTimeout": 60000},
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") != "ok":
                raise RuntimeError(f"flaresolverr status: {data.get('message', 'unknown')}")
        except Exception as e:
            raise RuntimeError(f"flaresolverr failed: {e}") from e

        # 从 flaresolverr 响应中提取 cookies
        solution = data.get("solution") or {}
        cookies_text = ""
        # flaresolverr 可能返回 cookies 列表
        for cookie in solution.get("cookies") or []:
            name = cookie.get("name", "")
            value = cookie.get("value", "")
            domain = cookie.get("domain", "")
            # Netscape 格式要求 domain 以 . 开头
            if not domain.startswith("."):
                domain = "." + domain
            path = cookie.get("path", "/")
            expires = cookie.get("expires", 0)
            # flaresolverr 的 expires 可能是秒级时间戳，转为 Unix timestamp
            if expires and expires < 10000000000:
                expires = expires
            cookies_text += f"{domain}\tTRUE\t{path}\tFALSE\t{expires}\t{name}\t{value}\n"

        if not cookies_text:
            # 后备：从 HTML 中尝试提取 Set-Cookie
            html = solution.get("response") or ""
            for m in re.finditer(r'Set-Cookie:\s*([^;]+)', html, re.IGNORECASE):
                cookies_text += f".\tTRUE\t/\tFALSE\t0\t{m.group(1).split('=')[0].strip()}\t{m.group(1).strip()}\n"

        if not cookies_text:
            raise RuntimeError("flaresolverr returned no cookies")

        # 写入临时 cookies 文件
        tmp_cookies = Path("/tmp") / f"flare_{uuid.uuid4().hex[:8]}.txt"
        tmp_cookies.write_text("# Netscape HTTP Cookie File\n" + cookies_text, encoding="utf-8")
        try:
            return extract_info_with_ytdlp(url, referer, user_agent, proxy, str(tmp_cookies))
        finally:
            tmp_cookies.unlink(missing_ok=True)


def should_retry_youtube_without_cookies(error_text: str) -> bool:
    text = str(error_text or "").lower()
    return (
        "requested format is not available" in text
        or "sign in to confirm you're not a bot" in text
        or "use --cookies-from-browser or --cookies for the authentication" in text
    )


def should_hint_bilibili_cookies(error_text: str) -> bool:
    text = str(error_text or "").lower()
    return (
        "412" in text
        or "precondition failed" in text
        or "risk control" in text
        or "风控" in text
    )


def download_with_ytdlp(
    url: str,
    output_path: Path,
    referer: Optional[str] = None,
    user_agent: Optional[str] = None,
    proxy: Optional[str] = None,
    cookies_path: Optional[str] = None,
    progress_callback=None,
    should_cancel=None,
    force_mp4: bool = False,
):
    output_path.parent.mkdir(parents=True, exist_ok=True)

    ytdlp_output = output_path
    if force_mp4 and output_path.suffix.lower() == '.mp4':
        ytdlp_output = output_path.with_suffix('')

    progress_re = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")
    size_re = re.compile(r"\[download\]\s+\d+(?:\.\d+)?%\s+of\s+([^\s]+)")
    speed_re = re.compile(r"at\s+([^\s]+)")
    speed_unit_re = re.compile(r"([\d.]+)\s*(B|KiB|MiB|GiB)/s")
    eta_re = re.compile(r"ETA\s+([0-9:]+)")

    def run_once(active_cookies_path: Optional[str]):
        cmd = [
            "yt-dlp",
            "--ignore-config",
            "--newline",
            "--progress",
            "--no-part",
            "--restrict-filenames",
            "--socket-timeout",
            str(YTDLP_SOCKET_TIMEOUT),
            "-o",
            str(ytdlp_output),
        ]
        if force_mp4:
            cmd += ["--merge-output-format", "mp4", "--recode-video", "mp4"]
        # Auto-set Referer for xchina if not provided
        effective_referer = referer
        if 'xchina' in (url or '').lower():
            effective_referer = effective_referer or 'https://www.xchina.co/'
        if effective_referer:
            cmd += ["--add-header", f"Referer:{effective_referer}"]
        if user_agent:
            cmd += ["--add-header", f"User-Agent:{user_agent}"]
        if proxy:
            cmd += ["--proxy", proxy]
        if active_cookies_path and Path(active_cookies_path).exists():
            cmd += ["--cookies", active_cookies_path]
        cmd.append(url)

        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        lines = []
        last_progress = 8

        try:
            if process.stdout is not None:
                for raw_line in process.stdout:
                    if should_cancel and should_cancel():
                        process.terminate()
                        raise RuntimeError("下载已取消")
                    line = raw_line.rstrip()
                    if line:
                        lines.append(line)
                    match = progress_re.search(line)
                    if match and progress_callback:
                        pct = max(8, min(99, int(float(match.group(1)))))
                        last_progress = pct
                        parts = []
                        size_match = size_re.search(line)
                        speed_match = speed_re.search(line)
                        eta_match = eta_re.search(line)
                        if size_match:
                            parts.append(f"总大小 {size_match.group(1)}")
                        if speed_match:
                            raw_speed = speed_match.group(1)
                            # 统一转为 MB/s
                            m = speed_unit_re.match(raw_speed)
                            if m:
                                val = float(m.group(1))
                                unit = m.group(2)
                                if unit == "KiB":
                                    val /= 1024.0
                                elif unit == "GiB":
                                    val *= 1024.0
                                parts.append(f"速度 {val:.2f} MB/s")
                            else:
                                parts.append(f"速度 {raw_speed}")
                        if eta_match:
                            parts.append(f"剩余 {eta_match.group(1)}")
                        status = f"已下载 {match.group(1)}%"
                        if parts:
                            status += " · " + " · ".join(parts)
                        progress_callback(pct, status)
                    elif progress_callback and line:
                        lower_line = line.lower()
                        if "destination:" in lower_line:
                            progress_callback(max(last_progress, 8), "已开始下载视频")
                        elif "merging formats into" in lower_line or "recoding video to" in lower_line:
                            progress_callback(99, "正在合并并转成 MP4")
        finally:
            try:
                returncode = process.wait(timeout=YTDLP_DOWNLOAD_TIMEOUT)
            except subprocess.TimeoutExpired:
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except (subprocess.TimeoutExpired, OSError):
                    process.kill()
                    process.wait()
                raise RuntimeError(f"yt-dlp 下载超时（>{YTDLP_DOWNLOAD_TIMEOUT}s）")

        if returncode != 0:
            detail = "\n".join(lines[-80:]).strip() or f"yt-dlp exited with code {returncode}"
            raise RuntimeError(detail[-4000:])

    is_youtube = detect_platform(url) == "youtube"
    try:
        run_once(cookies_path)
    except Exception as exc:
        if is_youtube and cookies_path and Path(cookies_path).exists() and should_retry_youtube_without_cookies(str(exc)):
            if progress_callback:
                progress_callback(8, "YouTube cookies 可能失效，正在切换无 cookies 重试…")
            run_once(None)
        else:
            raise

    if progress_callback:
        progress_callback(100, "yt-dlp 下载完成")


def build_stream_option(url: str, meta: Optional[dict] = None, source: str = "unknown") -> dict:
    meta = meta or {}
    filesize = meta.get("filesize") or meta.get("filesize_approx")
    width = meta.get("width")
    height = meta.get("height")
    format_note = meta.get("format_note") or meta.get("format_id")
    duration = meta.get("duration")
    tbr = meta.get("tbr")
    acodec = meta.get("acodec")
    vcodec = meta.get("vcodec")
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
        "acodec": acodec,
        "vcodec": vcodec,
    }


def choose_best_stream_url(info: dict) -> Optional[str]:
    streams = info.get("streams") or []
    options = info.get("stream_options") or []
    if not streams:
        return None

    best_stream = streams[0]
    best_score = -1
    for stream in streams:
        option = next((item for item in options if item.get("url") == stream), {})
        width = int(option.get("width") or 0)
        height = int(option.get("height") or 0)
        pixels = width * height
        tbr = float(option.get("tbr") or 0)
        filesize = float(option.get("filesize") or option.get("filesize_approx") or 0)
        acodec = str(option.get("acodec") or "").lower()
        has_audio = int(acodec not in {"", "none", "null", "unknown"})
        score = has_audio * 10_000_000_000_000_000 + pixels * 1_000_000 + tbr * 1_000 + filesize
        if score > best_score:
            best_score = score
            best_stream = stream
    return best_stream


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

    source_url = str(info.get("source_url") or "")
    if prefers_best_stream(source_url):
        return choose_best_stream_url(info)
    return streams[0] if streams else None


def extract_youtube_streams(meta: dict) -> tuple[list[str], list[dict]]:
    streams = []
    options = []
    for fmt in meta.get('formats', []) or []:
        fmt_url = fmt.get('url')
        if not isinstance(fmt_url, str):
            continue
        vcodec = str(fmt.get('vcodec') or 'none')
        if vcodec == 'none':
            continue
        width = fmt.get('width')
        height = fmt.get('height')
        ext = str(fmt.get('ext') or '')
        protocol = str(fmt.get('protocol') or '')
        if not width and not height and '.m3u8' not in fmt_url:
            continue
        if ext not in {'mp4', 'webm', 'm4v'} and '.m3u8' not in fmt_url and protocol not in {'https', 'http', 'm3u8_native', 'm3u8'}:
            continue
        streams.append(fmt_url)
        options.append(build_stream_option(fmt_url, fmt, source='yt-dlp-youtube'))
    return dedupe_keep_order(streams), dedupe_stream_options(options)


def extract_bilibili_streams(meta: dict) -> tuple[list[str], list[dict]]:
    streams = []
    options = []
    for fmt in meta.get('formats', []) or []:
        fmt_url = fmt.get('url')
        if not isinstance(fmt_url, str):
            continue
        vcodec = str(fmt.get('vcodec') or 'none')
        if vcodec == 'none':
            continue
        width = fmt.get('width')
        height = fmt.get('height')
        ext = str(fmt.get('ext') or '')
        protocol = str(fmt.get('protocol') or '')
        if not width and not height and '.m3u8' not in fmt_url:
            continue
        if ext not in {'mp4', 'flv', 'm4v', 'webm'} and '.m3u8' not in fmt_url and protocol not in {'https', 'http', 'm3u8_native', 'm3u8'}:
            continue
        streams.append(fmt_url)
        options.append(build_stream_option(fmt_url, fmt, source='yt-dlp-bilibili'))
    return dedupe_keep_order(streams), dedupe_stream_options(options)


def extract_douyin_streams(meta: dict) -> tuple[list[str], list[dict]]:
    streams = []
    options = []
    for fmt in meta.get('formats', []) or []:
        fmt_url = fmt.get('url')
        if not isinstance(fmt_url, str):
            continue
        vcodec = str(fmt.get('vcodec') or 'none')
        if vcodec == 'none':
            continue
        width = fmt.get('width')
        height = fmt.get('height')
        ext = str(fmt.get('ext') or '')
        protocol = str(fmt.get('protocol') or '')
        if not width and not height and '.m3u8' not in fmt_url:
            continue
        if ext not in {'mp4', 'flv', 'm4v', 'webm'} and '.m3u8' not in fmt_url and protocol not in {'https', 'http', 'm3u8_native', 'm3u8'}:
            continue
        streams.append(fmt_url)
        options.append(build_stream_option(fmt_url, fmt, source='yt-dlp-douyin'))
    return dedupe_keep_order(streams), dedupe_stream_options(options)


def extract_generic_ytdlp_streams(meta: dict) -> tuple[list[str], list[dict]]:
    streams = []
    options = []
    direct = meta.get("url")
    if isinstance(direct, str) and ".m3u8" in direct:
        streams.append(direct)
        options.append(build_stream_option(direct, meta, source="yt-dlp-direct"))
    for fmt in meta.get("formats", []) or []:
        fmt_url = fmt.get("url")
        if not isinstance(fmt_url, str) or ".m3u8" not in fmt_url:
            continue
        if is_probably_audio_only_format(fmt):
            continue
        streams.append(fmt_url)
        options.append(build_stream_option(fmt_url, fmt, source="yt-dlp-format"))
    return dedupe_keep_order(streams), dedupe_stream_options(options)


def is_instagram_image_candidate(url: str | None) -> bool:
    value = str(url or '').lower()
    if not value:
        return False
    # Exclude static assets and non-media resources
    if 'static.cdninstagram.com' in value or '/rsrc.php/' in value:
        return False
    return any(token in value for token in ('cdninstagram.com/', 'instagram.f', 'scontent-')) and any(ext in value for ext in ('.jpg', '.jpeg', '.png', '.webp'))


def normalize_instagram_media_url(url: str | None) -> Optional[str]:
    value = str(url or '').replace('\\/', '/').strip()
    return value or None


def extract_instagram_images(meta: dict) -> tuple[list[str], list[dict], list[dict]]:
    # Support Instagram carousel: multiple entries each with thumbnails
    entries = meta.get('entries')
    if isinstance(entries, list) and entries:
        all_images: list[str] = []
        all_options: list[dict] = []
        media_entries: list[dict] = []
        seen_bases = set()
        for entry_index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            # Collect candidate image URLs from entry thumbnails and global thumbnail
            thumbnails = entry.get('thumbnails') or []
            url_candidates = []
            for item in thumbnails:
                if isinstance(item, dict):
                    url = item.get('url')
                    if url:
                        width = item.get('width')
                        height = item.get('height')
                        url_candidates.append((url, width, height, 'yt-dlp-instagram-entry'))
            # Entry-level thumbnail fallback
            if entry.get('thumbnail'):
                url_candidates.append((entry.get('thumbnail'), None, None, 'yt-dlp-instagram-entry'))
            # Deduplicate and normalize per entry, add to global lists
            for url, width, height, source in url_candidates:
                normalized = normalize_instagram_media_url(url)
                if not is_instagram_image_candidate(normalized):
                    continue
                parsed = urlsplit(normalized)
                base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                if base in seen_bases:
                    continue
                seen_bases.add(base)
                option = build_stream_option(normalized, {'width': width, 'height': height}, source=source)
                all_images.append(normalized)
                all_options.append(option)
                media_entries.append({
                    'media_index': len(media_entries),
                    'entry_index': entry_index,
                    'media_key': entry.get('id') or entry.get('display_id') or entry.get('webpage_url'),
                    'thumbnail': normalized,
                    'media_type': 'image',
                    'streams': [],
                    'stream_options': [],
                    'best_stream_url': None,
                    'best_stream_option': None,
                    'images': [normalized],
                    'image_options': [option],
                })
        if all_images:
            return all_images, all_options, media_entries

    # Fallback: single best image from top-level metadata
    candidates = []
    thumbnails = meta.get('thumbnails') or []
    for item in thumbnails:
        if isinstance(item, dict):
            url = item.get('url')
            if url:
                candidates.append((url, item.get('width'), item.get('height'), 'yt-dlp-instagram-thumbnail'))
    if meta.get('thumbnail'):
        candidates.append((meta.get('thumbnail'), None, None, 'yt-dlp-instagram-thumbnail'))
    if not candidates:
        return [], [], []
    best_url = None
    best_option = None
    best_area = -1
    for url, width, height, source in candidates:
        normalized = normalize_instagram_media_url(url)
        if not is_instagram_image_candidate(normalized):
            continue
        area = (int(width or 0) * int(height or 0))
        if area > best_area:
            best_area = area
            best_url = normalized
            best_option = build_stream_option(normalized, {'width': width, 'height': height}, source=source)
    if not best_url:
        return [], [], []
    media_entry = {
        'media_index': 0,
        'entry_index': 0,
        'media_key': meta.get('id') or meta.get('display_id') or meta.get('webpage_url'),
        'thumbnail': best_url,
        'media_type': 'image',
        'streams': [],
        'stream_options': [],
        'best_stream_url': None,
        'best_stream_option': None,
        'images': [best_url],
        'image_options': [best_option],
    }
    return [best_url], [best_option], [media_entry]


def extract_instagram_images_from_html(html: str) -> dict:
    result = {
        'title': extract_title_from_html(html),
        'thumbnail': None,
        'streams': [],
        'stream_options': [],
        'images': [],
        'image_options': [],
        'media_entries': [],
    }

    seen_bases = set()

    def push(image_url: str | None, source: str = 'instagram-meta-image'):
        normalized = normalize_instagram_media_url(image_url)
        if not is_instagram_image_candidate(normalized):
            return
        # Deduplicate by base URL (without query/fragment) to discard size variants and duplicates
        parsed = urlsplit(normalized)
        base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if base in seen_bases:
            return
        seen_bases.add(base)
        result['images'].append(normalized)
        option = {'url': normalized, 'source': source, 'type': 'image'}
        result['image_options'].append(option)
        result['thumbnail'] = result['thumbnail'] or normalized
        result['media_entries'].append({
            'media_index': len(result['media_entries']),
            'entry_index': len(result['media_entries']),
            'media_key': None,
            'thumbnail': normalized,
            'media_type': 'image',
            'streams': [],
            'stream_options': [],
            'best_stream_url': None,
            'best_stream_option': None,
            'images': [normalized],
            'image_options': [option],
        })

    for tag in re.findall(r'<meta\b[^>]*>', html, re.IGNORECASE):
        attrs = {}
        for key, _, value in re.findall(r"([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(['\"])(.*?)\2", tag, re.IGNORECASE | re.DOTALL):
            attrs[key.lower()] = value
        meta_key = (attrs.get('property') or attrs.get('name') or '').strip().lower()
        if meta_key in {'og:image', 'twitter:image'}:
            push(attrs.get('content'), source='instagram-meta-image')

    if not result['images']:
        for candidate in re.findall(r'https?:\\/\\/[^"\'\s<>]+', html, re.IGNORECASE):
            push(candidate.replace('\\/', '/'), source='instagram-html-raw-image')
        for candidate in re.findall(r'https?://[^"\'\s<>]+', html, re.IGNORECASE):
            push(candidate, source='instagram-html-raw-image')

    result['images'] = dedupe_keep_order(result['images'])
    result['image_options'] = dedupe_stream_options(result['image_options'])
    return result


def extract_x_images(meta: dict) -> tuple[list[str], list[dict]]:
    images = []
    options = []

    for entry in meta.get("thumbnails", []) or []:
        image_url = entry.get("url")
        if not isinstance(image_url, str) or not is_direct_image_url(image_url):
            continue
        image_id = str(entry.get("id") or "").lower()
        preference = float(entry.get("preference") or 0)
        if image_id and not any(token in image_id for token in {"orig", "large", "4096x4096", "2048x2048", "large jpg", "medium"}):
            if preference < 0:
                continue
        images.append(image_url)
        options.append({
            "url": image_url,
            "source": "yt-dlp-x-thumbnail",
            "type": "image",
            "width": entry.get("width"),
            "height": entry.get("height"),
            "preference": preference,
            "id": entry.get("id"),
        })

    return dedupe_keep_order(images), dedupe_stream_options(options)


def extract_x_streams(meta: dict) -> tuple[list[str], list[dict], list[dict]]:
    streams = []
    options = []
    media_entries = []

    entries = meta.get("entries") or []
    if isinstance(entries, list) and entries:
        for media_index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            entry_streams, entry_options, nested_entries = extract_x_streams(entry)
            chosen_streams = entry_streams
            chosen_options = entry_options
            if nested_entries:
                chosen_streams = [item.get("best_stream_url") for item in nested_entries if item.get("best_stream_url")]
                chosen_options = [item.get("best_stream_option") for item in nested_entries if isinstance(item.get("best_stream_option"), dict)]
                for nested in nested_entries:
                    item = dict(nested)
                    item["media_index"] = len(media_entries)
                    media_entries.append(item)
            elif chosen_streams:
                best_url = choose_best_stream_url({"streams": entry_streams, "stream_options": entry_options})
                best_option = next((item for item in entry_options if item.get("url") == best_url), entry_options[0])
                media_entries.append({
                    "media_index": len(media_entries),
                    "tweet_media_index": media_index,
                    "media_key": entry.get("id") or entry.get("display_id") or entry.get("webpage_url"),
                    "thumbnail": entry.get("thumbnail"),
                    "streams": entry_streams,
                    "stream_options": dedupe_stream_options(entry_options),
                    "best_stream_url": best_option.get("url"),
                    "best_stream_option": best_option,
                })
            streams.extend([s for s in chosen_streams if isinstance(s, str)])
            options.extend([opt for opt in chosen_options if isinstance(opt, dict)])
        return dedupe_keep_order(streams), dedupe_stream_options(options), media_entries

    direct = meta.get("url")
    if isinstance(direct, str) and direct and (".mp4" in direct or ".m3u8" in direct):
        if not is_probably_audio_only_format(meta):
            streams.append(direct)
            options.append(build_stream_option(direct, meta, source="yt-dlp-x-direct"))

    for fmt in meta.get("formats", []) or []:
        fmt_url = fmt.get("url")
        if not isinstance(fmt_url, str):
            continue
        if ".mp4" not in fmt_url and ".m3u8" not in fmt_url:
            continue
        if is_probably_audio_only_format(fmt):
            continue

        vcodec = str(fmt.get("vcodec") or "none").lower()
        if vcodec in {"none", "null", "unknown"}:
            continue

        # 过滤 video-only 格式（acodec 为 none/null/unknown），确保下载的视频有音频
        acodec = str(fmt.get("acodec") or "none").lower()
        if acodec in {"none", "null", "unknown"}:
            continue

        width = fmt.get("width")
        height = fmt.get("height")
        if (not width or not height) and isinstance(fmt_url, str):
            size_match = re.search(r"/vid/[^/]+/(\d+)x(\d+)/", fmt_url)
            if size_match:
                width = int(size_match.group(1))
                height = int(size_match.group(2))
                fmt = {**fmt, "width": width, "height": height}

        streams.append(fmt_url)
        options.append(build_stream_option(fmt_url, fmt, source="yt-dlp-x"))

    streams = dedupe_keep_order(streams)
    options = dedupe_stream_options(options)
    if streams:
        best_url = choose_best_stream_url({"streams": streams, "stream_options": options})
        best_option = next((item for item in options if item.get("url") == best_url), options[0])
        media_entries.append({
            "media_index": 0,
            "tweet_media_index": 0,
            "media_key": meta.get("id") or meta.get("display_id") or meta.get("webpage_url"),
            "thumbnail": meta.get("thumbnail"),
            "streams": streams,
            "stream_options": options,
            "best_stream_url": best_option.get("url"),
            "best_stream_option": best_option,
        })
        return [best_option.get("url")], [best_option], media_entries

    return streams, options, media_entries


def extract_platform_streams(platform: str, meta: dict) -> tuple[list[str], list[dict], list[dict]]:
    direct = meta.get("url")
    if platform == "x":
        return extract_x_streams(meta)
    if platform == "youtube":
        streams, options = extract_youtube_streams(meta)
        if isinstance(direct, str) and direct and direct not in streams and ('.googlevideo.com/' in direct or '.m3u8' in direct):
            streams.append(direct)
            options.append(build_stream_option(direct, meta, source="yt-dlp-youtube-direct"))
        return dedupe_keep_order(streams), dedupe_stream_options(options), []
    if platform == "bilibili":
        streams, options = extract_bilibili_streams(meta)
        if isinstance(direct, str) and direct and direct not in streams:
            streams.append(direct)
            options.append(build_stream_option(direct, meta, source="yt-dlp-bilibili-direct"))
        return dedupe_keep_order(streams), dedupe_stream_options(options), []
    if platform == "douyin":
        streams, options = extract_douyin_streams(meta)
        if isinstance(direct, str) and direct and direct not in streams:
            streams.append(direct)
            options.append(build_stream_option(direct, meta, source="yt-dlp-douyin-direct"))
        return dedupe_keep_order(streams), dedupe_stream_options(options), []
    if platform == "instagram":
        _, _, media_entries = extract_instagram_images(meta)
        return [], [], media_entries
    streams, options = extract_generic_ytdlp_streams(meta)
    return streams, options, []


def extract_x_status_id(url: str) -> Optional[str]:
    match = re.search(r'/status/(\d+)', url)
    return match.group(1) if match else None


def apply_stream_results(info: dict, streams: list[str], options: list[dict], selected_url: Optional[str] = None, selected_index: Optional[int] = None, extractor: Optional[str] = None):
    if not streams:
        return info
    info["streams"] = dedupe_keep_order((info.get("streams") or []) + streams)
    info["stream_options"] = dedupe_stream_options((info.get("stream_options") or []) + options)
    info["resolved_url"] = choose_stream_url(info, selected_url, selected_index)
    info["is_m3u8"] = True
    if extractor:
        info["extractor"] = extractor
    elif not info.get("extractor"):
        info["extractor"] = "yt-dlp"
    return info


def try_x_fallback_streams(url: str, info: dict, referer: Optional[str] = None, user_agent: Optional[str] = None, proxy: Optional[str] = None, cookies_path: Optional[str] = None) -> tuple[list[str], list[dict], list[str], list[dict], Optional[str]]:
    extra_streams = []
    extra_options = []
    extra_images = []
    extra_image_options = []
    extractor = None

    try:
        html = fetch_webpage_html(url, referer, user_agent, proxy)
        fallback_streams = [s for s in extract_twitter_fallback_streams(html) if ".m3u8" in s or ".mp4" in s]
        if fallback_streams:
            extra_streams.extend(fallback_streams)
            extra_options.extend([build_stream_option(s, source="twitter-fallback") for s in fallback_streams])
            if not info.get("title"):
                info["title"] = extract_title_from_html(html)
            return extra_streams, extra_options, extra_images, extra_image_options, extractor
    except Exception as html_exc:
        info["errors"].append(f"x-html fallback 失败：{html_exc}")

    rest_id = extract_x_status_id(url)
    if rest_id:
        try:
            payload = fetch_x_graphql_tweet_result(rest_id, cookies_path, user_agent, proxy)
            gql_info = extract_x_streams_from_graphql_payload(payload)
            gql_streams = gql_info.get('streams') or []
            gql_options = gql_info.get('stream_options') or []
            gql_media_entries = gql_info.get('media_entries') or []
            gql_images_info = extract_x_images_from_graphql_payload(payload)
            gql_images = gql_images_info.get('images') or []
            gql_image_options = gql_images_info.get('image_options') or []
            if gql_streams:
                extra_streams.extend(gql_streams)
                extra_options.extend(gql_options)
                if gql_media_entries:
                    info['media_entries'] = gql_media_entries
                info['title'] = info.get('title') or gql_info.get('title')
                info['thumbnail'] = info.get('thumbnail') or gql_info.get('thumbnail')
                info['author'] = info.get('author') or gql_info.get('author')
                extractor = 'x-graphql'
            if gql_images:
                extra_images.extend(gql_images)
                extra_image_options.extend(gql_image_options)
                info['title'] = info.get('title') or gql_images_info.get('title')
                info['thumbnail'] = info.get('thumbnail') or gql_images_info.get('thumbnail')
                info['author'] = info.get('author') or gql_info.get('author')
                extractor = extractor or 'x-graphql'
        except Exception as gql_exc:
            info['errors'].append(f"x-graphql fallback 失败：{gql_exc}")

    return extra_streams, extra_options, extra_images, extra_image_options, extractor


def _build_discover_stream_cache_key(
    url: str,
    referer: Optional[str],
    user_agent: Optional[str],
    proxy: Optional[str],
    selected_url: Optional[str],
    selected_index: Optional[int],
    cookies_path: Optional[str],
) -> tuple:
    cookie_mtime = None
    if cookies_path:
        try:
            cookie_mtime = Path(cookies_path).stat().st_mtime
        except Exception:
            cookie_mtime = None
    return (
        url,
        referer or "",
        user_agent or "",
        proxy or "",
        selected_url or "",
        selected_index,
        cookies_path or "",
        cookie_mtime,
    )


def _discover_stream_uncached(
    url: str,
    referer: Optional[str] = None,
    user_agent: Optional[str] = None,
    proxy: Optional[str] = None,
    selected_url: Optional[str] = None,
    selected_index: Optional[int] = None,
    cookies_path: Optional[str] = None,
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
        "images": [],
        "image_options": [],
        "media_type": "video",
        "errors": [],
        "media_entries": [],
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

    if is_direct_media_url(url):
        info.update({
            "resolved_url": url,
            "is_m3u8": False,
            "extractor": "direct-media",
            "streams": [url],
            "stream_options": [build_stream_option(url, source="direct-media")],
        })
        return info

    if is_direct_image_url(url):
        info.update({
            "resolved_url": url,
            "is_m3u8": False,
            "extractor": "direct-image",
            "streams": [],
            "stream_options": [],
            "images": [url],
            "image_options": [{"url": url, "source": "direct-image", "type": "image"}],
            "media_type": "image",
        })
        return info

    try:
        page = probe_webpage(url, referer, user_agent, proxy)
        streams = page.get("streams") or []
        if page.get("title"):
            info["title"] = page["title"]
        if page.get("author"):
            info["author"] = page["author"]
        if streams:
            apply_stream_results(
                info,
                streams,
                page.get("stream_options") or [],
                selected_url,
                selected_index,
                extractor="html",
            )
    except Exception as exc:
        info["errors"].append(f"html 探测失败：{exc}")

    platform = detect_platform(url)
    is_youtube = platform == "youtube"
    try:
        meta = extract_info_with_ytdlp_flare(url, referer, user_agent, proxy, cookies_path)
    except Exception as exc:
        if is_youtube and cookies_path and Path(cookies_path).exists() and should_retry_youtube_without_cookies(str(exc)):
            info["errors"].append(f"yt-dlp 探测失败（带 cookies）：{exc}")
            try:
                meta = extract_info_with_ytdlp_flare(url, referer, user_agent, proxy, None)
            except Exception as exc2:
                info["errors"].append(f"yt-dlp 无 cookies 重试失败：{exc2}")
                meta = None
        else:
            info["errors"].append(f"yt-dlp 探测失败：{exc}")
            meta = None

    if meta is not None:
        info["title"] = meta.get("title") or info.get("title")
        info["thumbnail"] = meta.get("thumbnail")
        # 从 yt-dlp 元数据提取作者/上传者
        uploader = meta.get("uploader") or meta.get("channel") or meta.get("creator") or None
        if uploader:
            info["author"] = uploader

        extra_streams, extra_options, media_entries = extract_platform_streams(platform, meta)
        if platform == "x":
            extra_images, extra_image_options = extract_x_images(meta)
        elif platform == "instagram":
            extra_images, extra_image_options, media_entries_from_images = extract_instagram_images(meta)
            if media_entries_from_images:
                media_entries = media_entries_from_images
        else:
            extra_images, extra_image_options = [], []
        if media_entries:
            info["media_entries"] = media_entries
    else:
        extra_streams, extra_options, media_entries = [], [], []
        extra_images, extra_image_options = [], []

    if platform == "x" and not extra_streams:
        fallback_streams, fallback_options, fallback_images, fallback_image_options, fallback_extractor = try_x_fallback_streams(
            url,
            info,
            referer,
            user_agent,
            proxy,
            cookies_path,
        )
        extra_streams.extend(fallback_streams)
        extra_options.extend(fallback_options)
        extra_images.extend(fallback_images)
        extra_image_options.extend(fallback_image_options)
        if fallback_extractor:
            info["extractor"] = fallback_extractor

    if platform == "instagram" and not extra_streams and not extra_images:
        try:
            html = fetch_webpage_html(url, referer, user_agent or INSTAGRAM_FALLBACK_UA, proxy)
            fallback = extract_instagram_images_from_html(html)
            extra_images.extend(fallback.get('images') or [])
            extra_image_options.extend(fallback.get('image_options') or [])
            if fallback.get('media_entries'):
                info['media_entries'] = fallback.get('media_entries') or []
            if fallback.get('title') and not info.get('title'):
                info['title'] = fallback.get('title')
            if fallback.get('thumbnail') and not info.get('thumbnail'):
                info['thumbnail'] = fallback.get('thumbnail')
            if fallback.get('images'):
                info['extractor'] = 'instagram-html'
        except Exception as exc:
            info['errors'].append(f'instagram-html fallback 失败：{exc}')

    if extra_streams:
        apply_stream_results(
            info,
            extra_streams,
            extra_options,
            selected_url,
            selected_index,
            extractor=info.get("extractor") or "yt-dlp",
        )

    if extra_images:
        info["images"] = dedupe_keep_order((info.get("images") or []) + extra_images)
        info["image_options"] = dedupe_stream_options((info.get("image_options") or []) + extra_image_options)
        if info["images"] and not info.get("thumbnail"):
            info["thumbnail"] = info["images"][0]
        if not info.get("extractor"):
            info["extractor"] = "yt-dlp"

    if not info.get("resolved_url") and info.get("streams"):
        info["resolved_url"] = choose_stream_url(info, selected_url, selected_index)
        info["is_m3u8"] = True
        info["extractor"] = info.get("extractor") or "html"

    if not info.get("stream_options") and info.get("streams"):
        info["stream_options"] = [build_stream_option(s, source="fallback") for s in info["streams"]]

    if info.get("images") and not info.get("streams"):
        info["media_type"] = "image"
    else:
        info["media_type"] = "video"

    return info


def discover_stream(
    url: str,
    referer: Optional[str] = None,
    user_agent: Optional[str] = None,
    proxy: Optional[str] = None,
    selected_url: Optional[str] = None,
    selected_index: Optional[int] = None,
    cookies_path: Optional[str] = None,
) -> dict:
    cache_key = _build_discover_stream_cache_key(
        url,
        referer,
        user_agent,
        proxy,
        selected_url,
        selected_index,
        cookies_path,
    )
    now = time.time()
    with _DISCOVER_STREAM_CACHE_LOCK:
        cached = _DISCOVER_STREAM_CACHE.get(cache_key)
        if cached and cached[0] > now:
            return copy.deepcopy(cached[1])
        expired_keys = [key for key, value in _DISCOVER_STREAM_CACHE.items() if value[0] <= now]
        for key in expired_keys:
            _DISCOVER_STREAM_CACHE.pop(key, None)

    info = _discover_stream_uncached(
        url,
        referer,
        user_agent,
        proxy,
        selected_url,
        selected_index,
        cookies_path,
    )
    with _DISCOVER_STREAM_CACHE_LOCK:
        _DISCOVER_STREAM_CACHE[cache_key] = (now + DISCOVER_STREAM_CACHE_TTL, copy.deepcopy(info))
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
    effective_referer = referer
    # Auto-set Referer for platforms that require it
    if 'xchina' in (stream_url or '').lower():
        effective_referer = effective_referer or 'https://www.xchina.co/'
    header_lines = []
    if user_agent:
        header_lines.append(f"User-Agent: {user_agent}")
    if effective_referer:
        header_lines.append(f"Referer: {effective_referer}")

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

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    progress_lines = []
    stats = {
        "out_time_ms": 0,
        "total_size": 0,
        "speed": "",
        "bitrate": "",
    }
    start_time = time.time()
    last_update_time = time.time()
    _SLIDING_TIMEOUT = int(os.getenv("FFMPEG_SLIDING_TIMEOUT", "180"))  # 3 分钟无更新则超时

    def emit_progress():
        if not progress_callback:
            return
        out_time_ms = int(stats.get("out_time_ms") or 0)
        total_size = int(stats.get("total_size") or 0)
        elapsed_s = max(0.1, time.time() - start_time)
        # Progress based on video time: every 30s of video = 1% progress
        # This ensures the bar moves visibly even for long videos
        pseudo_progress = max(8, min(95, 8 + out_time_ms // 30_000_000))
        parts = [f"视频进度 {out_time_ms / 1000000:.1f}s"]
        if total_size > 0:
            parts.append(f"已下载 {total_size / 1024 / 1024:.1f}MB")
        if total_size > 0 and elapsed_s > 0:
            speed_mb = total_size / 1024 / 1024 / elapsed_s
            parts.append(f"下载速度 {speed_mb:.2f} MB/s")
        progress_callback(pseudo_progress, "正在下载… " + " · ".join(parts))


    try:
        if process.stdout is not None:
            for line in process.stdout:
                if should_cancel and should_cancel():
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
                    last_update_time = time.time()
                    emit_progress()
                elif line == "progress=end":
                    if progress_callback:
                        progress_callback(99, "正在收尾封装…")
        # 滑动超时：3分钟内没有进度更新才判定超时
        while True:
            ret = process.poll()
            if ret is not None:
                returncode = ret
                break
            if time.time() - last_update_time > _SLIDING_TIMEOUT:
                raise RuntimeError("ffmpeg 下载超时（3分钟无进度更新）")
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                continue
    except KeyboardInterrupt:
        process.terminate()
        process.wait()
        raise
    finally:
        # 确保进程已回收，避免僵尸
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    if returncode != 0:
        detail = "\n".join(progress_lines[-80:]).strip()
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
    effective_referer = referer
    # Auto-set Referer for platforms that require it
    if 'xchina' in (manifest_url or '').lower():
        effective_referer = effective_referer or 'https://www.xchina.co/'
    manifest_resp = requests.get(
        manifest_url,
        headers=build_headers(effective_referer, user_agent),
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
    session.headers.update(build_headers(effective_referer, user_agent))
    session.proxies.update(build_proxies(proxy) or {})

    def fetch_one(index_url):
        index, seg_url = index_url
        # 总超时检查（每个分片下载前）
        elapsed = time.time() - start_time
        if elapsed > AGGRESSIVE_HLS_TIMEOUT:
            raise RuntimeError(f"激进模式总超时（>{AGGRESSIVE_HLS_TIMEOUT}s），已下载 {downloaded}/{total_segments} 分片")
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
                # 重试前再次检查总超时
                if time.time() - start_time > AGGRESSIVE_HLS_TIMEOUT:
                    raise RuntimeError(f"激进模式总超时（>{AGGRESSIVE_HLS_TIMEOUT}s），已下载 {downloaded}/{total_segments} 分片")
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

        # 合并前再次检查总超时
        if time.time() - start_time > AGGRESSIVE_HLS_TIMEOUT:
            raise RuntimeError(f"激进模式总超时（>{AGGRESSIVE_HLS_TIMEOUT}s），已下载 {downloaded}/{total_segments} 分片")

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
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
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


def normalize_filename(name: str, max_bytes: int = 150) -> str:
    """
    标准化文件名，限制总长度（含扩展名）不超过 max_bytes 字节。
    默认 150 字节，为冲突后缀（如 " (1)"）留出空间。
    """
    raw = str(name or "").strip()
    if not raw:
        return "output.mp4"

    candidate = raw.replace("\u3000", " ")
    candidate = re.sub(r"\s+", " ", candidate).strip()

    suffix_match = re.search(r"(\.[A-Za-z0-9]{1,10})\s*$", candidate)
    suffix = suffix_match.group(1).lower() if suffix_match else ".mp4"
    stem = candidate[:-len(suffix)].strip() if suffix_match else candidate

    suffix = re.sub(r"[^A-Za-z0-9.]", "", suffix or "")
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,10}", suffix or ""):
        suffix = ".mp4"

    stem = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", stem)
    stem = re.sub(r"\s+", " ", stem)
    stem = re.sub(r"_+", "_", stem)
    stem = stem.strip(" ._")
    stem = re.sub(r"\.{2,}", ".", stem)

    # 预留空间给冲突后缀 " (N)"（最多 5 字符，如 " (999)"）
    reserved_for_suffix = 5
    max_stem_bytes = max(1, max_bytes - len(suffix.encode("utf-8")) - reserved_for_suffix)
    while stem and len(stem.encode("utf-8")) > max_stem_bytes:
        stem = stem[:-1].rstrip(" ._")

    if not stem:
        stem = "output"

    return f"{stem}{suffix.lower()}"


def build_media_proxy_url(proxy_prefix: str, target_url: str, referer: str | None = None, user_agent: str | None = None, proxy: str | None = None) -> str:
    parsed_target = urlparse(target_url)
    if parsed_target.netloc == "video.xchina.download" and parsed_target.path.startswith("/ts/"):
        target_url = parsed_target._replace(netloc="cdn.xchina.download").geturl()
        parsed_target = urlparse(target_url)
    # Also handle upload.xchina.io CDN
    if parsed_target.netloc == "upload.xchina.io":
        # Keep as-is, but ensure Referer is set
        pass
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
