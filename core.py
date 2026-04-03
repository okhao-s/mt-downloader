import copy
import json
import os
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

CONFIG_PATH = Path(os.getenv("APP_CONFIG_PATH", "/app/data/config.json"))
DEFAULT_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
X_GQL_BEARER = "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
X_TWEET_RESULT_BY_REST_ID_QUERY = "sBoAB5nqJTOyR9sZ5qVLsw"
DEFAULT_PROXY_BYPASS_PLATFORMS = {"douyin", "bilibili"}
YTDLP_INFO_TIMEOUT = int(os.getenv("YTDLP_INFO_TIMEOUT", "45"))
YTDLP_SOCKET_TIMEOUT = int(os.getenv("YTDLP_SOCKET_TIMEOUT", "30"))
DISCOVER_STREAM_CACHE_TTL = max(1, int(os.getenv("DISCOVER_STREAM_CACHE_TTL", "15")))
_DISCOVER_STREAM_CACHE: dict[tuple, tuple[float, dict]] = {}
_DISCOVER_STREAM_CACHE_LOCK = threading.Lock()


def ensure_parent(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)


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
    cfg["wecom_secret"] = str(cfg.get("wecom_secret") or "")
    cfg["wecom_token"] = str(cfg.get("wecom_token") or "")
    cfg["wecom_encoding_aes_key"] = str(cfg.get("wecom_encoding_aes_key") or "")
    cfg["wecom_callback_url"] = str(cfg.get("wecom_callback_url") or "")
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


def detect_platform(url: Optional[str]) -> str:
    value = str(url or "").lower()
    if "x.com/" in value or "twitter.com/" in value:
        return "x"
    if "youtube.com/" in value or "youtu.be/" in value:
        return "youtube"
    if "bilibili.com/" in value or "b23.tv/" in value:
        return "bilibili"
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
    return "generic"


def prefers_best_stream(url: Optional[str]) -> bool:
    return detect_platform(url) in {"x", "youtube", "bilibili", "douyin"}


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
            return html
    except Exception:
        pass

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


def extract_twitter_fallback_streams(html: str) -> list[str]:
    patterns = [
        r'https?://video\.twimg\.com/[^"\'\s>]+\.(?:m3u8|mp4)(?:\?[^"\'\s>]*)?',
        r'https?:\\/\\/video\.twimg\.com\\/.*?\.(?:m3u8|mp4)(?:[^"\'\s>]*)?',
        r'"playbackUrl"\s*:\s*"(https?:\\/\\/video\.twimg\.com\\/.*?(?:m3u8|mp4)(?:[^"\\]*)?)"',
        r'"video_info".*?"variants"\s*:\s*\[(.*?)\]',
    ]
    found = []
    for pat in patterns[:3]:
        for match in re.findall(pat, html, re.IGNORECASE):
            candidate = match if isinstance(match, str) else match[0]
            candidate = candidate.replace('\\/', '/')
            found.append(candidate)

    variants_blocks = re.findall(patterns[3], html, re.IGNORECASE | re.DOTALL)
    for block in variants_blocks:
        for url in re.findall(r'https?:\\/\\/video\.twimg\.com\\/.*?(?:m3u8|mp4)(?:[^"\\]*)?', block, re.IGNORECASE):
            found.append(url.replace('\\/', '/'))

    cleaned = []
    for candidate in found:
        if not isinstance(candidate, str):
            continue
        if 'video.twimg.com/' in candidate or 'video.twimg.com/' in candidate:
            cleaned.append(candidate.replace('video.twimg.com/', 'video.twimg.com/'))
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
    if is_douyin:
        dy_streams, dy_options = extract_douyin_share_streams(html)
        streams = dedupe_keep_order(streams + dy_streams)
        stream_options = dedupe_stream_options(stream_options + dy_options)
        title = extract_douyin_title_from_html(html) or title
    return {
        "streams": streams,
        "stream_options": stream_options,
        "title": title,
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


def extract_x_streams_from_graphql_payload(payload: dict) -> dict:
    result = {"title": None, "thumbnail": None, "streams": [], "stream_options": []}

    legacy = dig_first(payload, lambda x: isinstance(x, dict) and isinstance(x.get('extended_entities'), dict)) or {}
    extended = legacy.get('extended_entities') or {}
    media_list = extended.get('media') or []
    media_best_options = []

    for media in media_list:
        if not isinstance(media, dict):
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
        media_best_options.append(best_option)

    note_tweet = dig_first(payload, lambda x: isinstance(x, dict) and (x.get('full_text') or x.get('text')))
    if isinstance(note_tweet, dict):
        result['title'] = note_tweet.get('full_text') or note_tweet.get('text')

    if media_best_options:
        best_overall = next((item for item in media_best_options if item.get('url') == choose_best_stream_url({'streams': [x['url'] for x in media_best_options], 'stream_options': media_best_options})), media_best_options[0])
        result['streams'] = [best_overall['url']]
        result['stream_options'] = [best_overall]
    else:
        result['streams'] = []
        result['stream_options'] = []

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
    if referer:
        cmd += ["--add-header", f"Referer:{referer}"]
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
        if referer:
            cmd += ["--add-header", f"Referer:{referer}"]
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
                            parts.append(f"速度 {speed_match.group(1)}")
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
            returncode = process.wait()

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
        score = pixels * 1_000_000 + tbr * 1_000 + filesize
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


def extract_platform_streams(platform: str, meta: dict) -> tuple[list[str], list[dict]]:
    direct = meta.get("url")
    if platform == "youtube":
        streams, options = extract_youtube_streams(meta)
        if isinstance(direct, str) and direct and direct not in streams and ('.googlevideo.com/' in direct or '.m3u8' in direct):
            streams.append(direct)
            options.append(build_stream_option(direct, meta, source="yt-dlp-youtube-direct"))
        return dedupe_keep_order(streams), dedupe_stream_options(options)
    if platform == "bilibili":
        streams, options = extract_bilibili_streams(meta)
        if isinstance(direct, str) and direct and direct not in streams:
            streams.append(direct)
            options.append(build_stream_option(direct, meta, source="yt-dlp-bilibili-direct"))
        return dedupe_keep_order(streams), dedupe_stream_options(options)
    if platform == "douyin":
        streams, options = extract_douyin_streams(meta)
        if isinstance(direct, str) and direct and direct not in streams:
            streams.append(direct)
            options.append(build_stream_option(direct, meta, source="yt-dlp-douyin-direct"))
        return dedupe_keep_order(streams), dedupe_stream_options(options)
    return extract_generic_ytdlp_streams(meta)


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


def try_x_fallback_streams(url: str, info: dict, referer: Optional[str] = None, user_agent: Optional[str] = None, proxy: Optional[str] = None, cookies_path: Optional[str] = None) -> tuple[list[str], list[dict], Optional[str]]:
    extra_streams = []
    extra_options = []
    extractor = None

    try:
        html = fetch_webpage_html(url, referer, user_agent, proxy)
        fallback_streams = [s for s in extract_twitter_fallback_streams(html) if ".m3u8" in s or ".mp4" in s]
        if fallback_streams:
            extra_streams.extend(fallback_streams)
            extra_options.extend([build_stream_option(s, source="twitter-fallback") for s in fallback_streams])
            if not info.get("title"):
                info["title"] = extract_title_from_html(html)
            return extra_streams, extra_options, extractor
    except Exception as html_exc:
        info["errors"].append(f"x-html fallback 失败：{html_exc}")

    rest_id = extract_x_status_id(url)
    if rest_id:
        try:
            payload = fetch_x_graphql_tweet_result(rest_id, cookies_path, user_agent, proxy)
            gql_info = extract_x_streams_from_graphql_payload(payload)
            gql_streams = gql_info.get('streams') or []
            gql_options = gql_info.get('stream_options') or []
            if gql_streams:
                extra_streams.extend(gql_streams)
                extra_options.extend(gql_options)
                info['title'] = info.get('title') or gql_info.get('title')
                info['thumbnail'] = info.get('thumbnail') or gql_info.get('thumbnail')
                extractor = 'x-graphql'
        except Exception as gql_exc:
            info['errors'].append(f"x-graphql fallback 失败：{gql_exc}")

    return extra_streams, extra_options, extractor


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

    if is_direct_media_url(url):
        info.update({
            "resolved_url": url,
            "is_m3u8": False,
            "extractor": "direct-media",
            "streams": [url],
            "stream_options": [build_stream_option(url, source="direct-media")],
        })
        return info

    try:
        page = probe_webpage(url, referer, user_agent, proxy)
        streams = page.get("streams") or []
        if page.get("title"):
            info["title"] = page["title"]
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
        meta = extract_info_with_ytdlp(url, referer, user_agent, proxy, cookies_path)
    except Exception as exc:
        if is_youtube and cookies_path and Path(cookies_path).exists() and should_retry_youtube_without_cookies(str(exc)):
            info["errors"].append(f"yt-dlp 探测失败（带 cookies）：{exc}")
            meta = extract_info_with_ytdlp(url, referer, user_agent, proxy, None)
        else:
            info["errors"].append(f"yt-dlp 探测失败：{exc}")
            meta = None

    if meta is not None:
        info["title"] = meta.get("title") or info.get("title")
        info["thumbnail"] = meta.get("thumbnail")

        extra_streams, extra_options = extract_platform_streams(platform, meta)

        if platform == "x" and not extra_streams:
            fallback_streams, fallback_options, fallback_extractor = try_x_fallback_streams(
                url,
                info,
                referer,
                user_agent,
                proxy,
                cookies_path,
            )
            extra_streams.extend(fallback_streams)
            extra_options.extend(fallback_options)
            if fallback_extractor:
                info["extractor"] = fallback_extractor

        if extra_streams:
            apply_stream_results(
                info,
                extra_streams,
                extra_options,
                selected_url,
                selected_index,
                extractor=info.get("extractor") or "yt-dlp",
            )

    if not info.get("resolved_url") and info.get("streams"):
        info["resolved_url"] = choose_stream_url(info, selected_url, selected_index)
        info["is_m3u8"] = True
        info["extractor"] = info.get("extractor") or "html"

    if not info.get("stream_options") and info.get("streams"):
        info["stream_options"] = [build_stream_option(s, source="fallback") for s in info["streams"]]

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

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    progress_lines = []
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
        pseudo_progress = max(8, min(95, 8 + out_time_ms // 15_000_000))
        parts = [f"视频进度 {out_time_ms / 1000000:.1f}s"]
        if total_size > 0:
            parts.append(f"已下载 {total_size / 1024 / 1024:.1f}MB")
        if speed and speed != "N/A":
            parts.append(f"下载速度 {speed}")
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
    finally:
        returncode = process.wait()

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

    max_name_bytes = 240
    suffix_bytes = len(suffix.encode("utf-8"))
    max_stem_bytes = max(1, max_name_bytes - suffix_bytes)
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
