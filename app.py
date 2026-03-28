import os
import re
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import requests
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from urllib3.exceptions import InsecureRequestWarning

from core import (
    aggressive_hls_download,
    build_headers,
    build_proxies,
    choose_stream_url,
    detect_platform,
    discover_stream,
    download_with_ytdlp,
    ffmpeg_download,
    is_m3u8_url,
    load_config,
    normalize_filename,
    rewrite_m3u8_manifest,
    save_config,
    should_hint_bilibili_cookies,
)
from wecom import WeComClient, WeComCrypto, build_passive_text_reply

app = FastAPI(title="M3U8 Downloader")
BASE_DIR = Path(__file__).parent
DOWNLOAD_DIR = Path("/downloads")
DATA_DIR = Path("/app/data")
COOKIES_DIR = DATA_DIR / "cookies"
TWITTER_COOKIES_PATH = COOKIES_DIR / "twitter.cookies.txt"
YOUTUBE_COOKIES_PATH = COOKIES_DIR / "youtube.cookies.txt"
BILIBILI_COOKIES_PATH = COOKIES_DIR / "bilibili.cookies.txt"
DOUYIN_COOKIES_PATH = COOKIES_DIR / "douyin.cookies.txt"
DOUYIN_FRESH_COOKIES_PATH = COOKIES_DIR / "douyin.fresh.cookies.txt"
INTERNAL_BASE_URL = os.getenv("INTERNAL_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
COOKIES_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

jobs: list[dict] = []
jobs_lock = threading.Lock()
MAX_CONCURRENT_DOWNLOADS = 3
download_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_DOWNLOADS, thread_name_prefix="mt-download")
wecom_job_watchers: set[str] = set()
wecom_job_watchers_lock = threading.Lock()
WECOM_FINAL_STATUSES = {"done", "failed", "cancelled"}


def mask_secret(value: str | None, keep: int = 3) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    if len(raw) <= keep * 2:
        return "*" * len(raw)
    return f"{raw[:keep]}***{raw[-keep:]}"


def is_wecom_ready(cfg: dict) -> bool:
    return bool(
        cfg.get("wecom_enabled")
        and cfg.get("wecom_corp_id")
        and cfg.get("wecom_agent_id")
        and cfg.get("wecom_secret")
        and cfg.get("wecom_token")
        and cfg.get("wecom_encoding_aes_key")
    )


def get_wecom_crypto(cfg: dict) -> WeComCrypto:
    if not is_wecom_ready(cfg):
        raise ValueError("企业微信配置不完整或未启用")
    return WeComCrypto(
        token=str(cfg.get("wecom_token") or ""),
        encoding_aes_key=str(cfg.get("wecom_encoding_aes_key") or ""),
        corp_id=str(cfg.get("wecom_corp_id") or ""),
    )


def get_wecom_client(cfg: dict) -> WeComClient:
    if not is_wecom_ready(cfg):
        raise ValueError("企业微信配置不完整或未启用")
    return WeComClient(
        corp_id=str(cfg.get("wecom_corp_id") or ""),
        agent_id=str(cfg.get("wecom_agent_id") or "0"),
        secret=str(cfg.get("wecom_secret") or ""),
    )


def send_wecom_text_async(to_user: str, content: str):
    def worker():
        try:
            cfg = load_config()
            client = get_wecom_client(cfg)
            result = client.send_text(to_user, content)
            print(f"[wecom] send_text ok: to={to_user} msgid={result.get('msgid')}")
        except Exception as exc:
            print(f"[wecom] send_text failed: to={to_user} error={exc}")

    threading.Thread(target=worker, name=f"wecom-msg-{uuid4().hex[:6]}", daemon=True).start()


def build_wecom_prefix(platform: str | None) -> str:
    return f"[{prettify_platform(platform)}]"


def prettify_platform(platform: str | None) -> str:
    mapping = {
        "douyin": "Douyin",
        "youtube": "YouTube",
        "bilibili": "Bilibili",
        "x": "X/Twitter",
        "generic": "通用链接",
    }
    key = str(platform or "generic").strip().lower()
    return mapping.get(key, key or "通用链接")


def build_wecom_route_feedback(url: str, platform: str) -> str:
    prefix = build_wecom_prefix(platform)
    return f"{prefix} 已识别链接，开始解析并创建任务：\n{url}"


def build_wecom_passive_ack(url: str, platform: str) -> str:
    prefix = build_wecom_prefix(platform)
    return f"{prefix} 已收到链接，正在创建任务，请稍等。\n{url}"


def build_wecom_job_created_feedback(job: dict) -> str:
    prefix = build_wecom_prefix(job.get("platform"))
    title = str(job.get("title") or job.get("output") or "").strip()
    lines = [
        f"{prefix} 任务已创建并入队",
        f"任务ID：{job.get('id')}",
    ]
    if title:
        lines.append(f"标题：{title}")
    if job.get("output"):
        lines.append(f"文件：{job.get('output')}")
    lines.append(f"状态：{job.get('status_text') or '排队中'}")
    return "\n".join(lines)


def build_wecom_job_completion_feedback(job: dict) -> str:
    status = str(job.get("status") or "").strip().lower()
    prefix = build_wecom_prefix(job.get("platform"))
    title = str(job.get("title") or job.get("output") or "").strip()
    status_label = {
        "done": "下载完成",
        "failed": "下载失败",
        "cancelled": "任务已取消",
    }.get(status, f"任务状态：{status or 'unknown'}")
    lines = [f"{prefix} {status_label}"]
    if title:
        lines.append(f"标题：{title}")
    if job.get("output"):
        lines.append(f"文件：{job.get('output')}")
    if job.get("id"):
        lines.append(f"任务ID：{job.get('id')}")
    if status == "failed":
        error = str(job.get("error") or job.get("status_text") or "未知原因").strip()
        lines.append(f"原因：{error[:180]}")
    return "\n".join(lines)


def notify_wecom_job_completion(job: dict):
    status = str(job.get("status") or "").strip().lower()
    if status not in WECOM_FINAL_STATUSES:
        return
    to_user = str(job.get("wecom_to_user") or "").strip()
    if not to_user:
        return
    if job.get("wecom_completion_notified"):
        return
    sent = update_job(job.get("id"), wecom_completion_notified=True, wecom_completion_notified_at=iso_now())
    if not sent:
        return
    send_wecom_text_async(to_user, build_wecom_job_completion_feedback(sent.copy()))


def watch_wecom_job(job_id: str, to_user: str):
    def worker():
        try:
            while True:
                with jobs_lock:
                    job = next((item.copy() for item in jobs if item.get("id") == job_id), None)
                if not job:
                    return
                if not job.get("wecom_to_user"):
                    update_job(job_id, wecom_to_user=to_user)
                    job["wecom_to_user"] = to_user
                if str(job.get("status") or "").strip().lower() in WECOM_FINAL_STATUSES:
                    notify_wecom_job_completion(job)
                    return
                time.sleep(2)
        finally:
            with wecom_job_watchers_lock:
                wecom_job_watchers.discard(job_id)

    with wecom_job_watchers_lock:
        if job_id in wecom_job_watchers:
            return
        wecom_job_watchers.add(job_id)
    threading.Thread(target=worker, name=f"wecom-watch-{job_id}", daemon=True).start()



def handle_wecom_download_message(msg: dict):
    from_user = str(msg.get("FromUserName") or "").strip()
    content = str(msg.get("Content") or "").strip()
    if not from_user:
        return
    url = normalize_input_url(content)
    if not url or not re.search(r"https?://", url, re.IGNORECASE):
        send_wecom_text_async(from_user, "没识别到可下载链接。直接发文本链接就行，我会自动接单。")
        return
    platform = get_platform(url)
    try:
        payload = DownloadPayload(url=url)
        job = create_download_job(payload)
        update_job(str(job.get("id") or ""), wecom_to_user=from_user)
        with jobs_lock:
            current_job = next((item.copy() for item in jobs if item.get("id") == job.get("id")), job.copy())
        send_wecom_text_async(from_user, "\n".join([build_wecom_route_feedback(url, platform), build_wecom_job_created_feedback(current_job)]))
        watch_wecom_job(str(job.get("id") or ""), from_user)
    except Exception as exc:
        send_wecom_text_async(from_user, f"{build_wecom_prefix(platform)} 任务创建失败：{exc}")


def iso_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def add_job(job: dict):
    with jobs_lock:
        jobs.append(job)


def update_job(job_id: str, **updates):
    notify_job = None
    with jobs_lock:
        for job in jobs:
            if job.get("id") == job_id:
                updates.setdefault("updated_at", iso_now())
                job.update(updates)
                if str(job.get("status") or "").strip().lower() in WECOM_FINAL_STATUSES and job.get("wecom_to_user") and not job.get("wecom_completion_notified"):
                    notify_job = job.copy()
                result = job
                break
        else:
            return None
    if notify_job:
        notify_wecom_job_completion(notify_job)
    return result


def list_recent_jobs(limit: int = 50):
    with jobs_lock:
        return list(reversed(jobs[-limit:]))


def count_active_jobs() -> int:
    with jobs_lock:
        return sum(1 for job in jobs if job.get("status") == "downloading")


def count_queued_jobs() -> int:
    with jobs_lock:
        return sum(1 for job in jobs if job.get("status") == "queued")


def remove_job(job_id: str):
    with jobs_lock:
        for index, job in enumerate(jobs):
            if job.get("id") == job_id:
                return jobs.pop(index)
    return None


def clear_history_jobs() -> int:
    with jobs_lock:
        keep = [job for job in jobs if job.get("status") in {"queued", "downloading"}]
        removed = len(jobs) - len(keep)
        jobs[:] = keep
        return removed


def get_download_dir() -> Path:
    return DOWNLOAD_DIR


def get_download_subdir(url: str | None = None) -> Path:
    base_dir = get_download_dir()
    if is_youtube_url(url):
        target = base_dir / "youtube"
    elif is_bilibili_url(url):
        target = base_dir / "bilibili"
    elif is_douyin_url(url):
        target = base_dir / "douyin"
    elif is_x_url(url):
        target = base_dir / "x"
    else:
        target = base_dir / "m3u8"
    target.mkdir(parents=True, exist_ok=True)
    return target


def allocate_output_name(suggested_name: str, download_dir: Path | None = None) -> str:
    normalized = normalize_filename(suggested_name)
    candidate = Path(normalized)
    stem = candidate.stem or "output"
    suffix = candidate.suffix or ".mp4"
    target_dir = download_dir or DOWNLOAD_DIR

    with jobs_lock:
        reserved_names = {
            str(job.get("output") or "").strip()
            for job in jobs
            if str(job.get("output") or "").strip()
            and Path(str(job.get("download_dir") or target_dir)).resolve() == target_dir.resolve()
        }

    taken_names = {path.name for path in target_dir.iterdir() if path.is_file()}
    taken_names.update(reserved_names)

    final_name = f"{stem}{suffix}"
    index = 1
    while final_name in taken_names:
        final_name = f"{stem} ({index}){suffix}"
        index += 1
    return final_name


def build_suggested_output_name(display_title: str | None, fallback_prefix: str = "video") -> str:
    base_name = (display_title or "").strip() or f"{fallback_prefix}-{uuid4().hex[:8]}"
    return normalize_filename(base_name)


def safe_requests_get(target: str, referer: str | None = None, user_agent: str | None = None, proxy: str | None = None, timeout: int = 60):
    headers = build_headers(referer, user_agent)
    proxies = build_proxies(proxy)
    try:
        return requests.get(target, headers=headers, proxies=proxies, timeout=timeout)
    except requests.exceptions.SSLError:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", InsecureRequestWarning)
            return requests.get(target, headers=headers, proxies=proxies, timeout=timeout, verify=False)


def extract_first_url(text: str | None) -> str:
    value = str(text or '').strip()
    if not value:
        return ''
    match = re.search(r'https?://[^\s\u3000]+', value)
    if not match:
        return value
    candidate = match.group(0).strip().rstrip('，。；;,.!?！？”"”’\')）】>')
    return candidate


def normalize_input_url(text: str | None) -> str:
    candidate = extract_first_url(text).strip()
    if not candidate:
        return candidate
    if is_douyin_url(candidate) and "v.douyin.com/" in candidate:
        return candidate
    return candidate


def schedule_retry(job_id: str, delay_seconds: int):
    delay_seconds = max(1, int(delay_seconds or 0))

    def delayed_retry():
        time.sleep(delay_seconds)
        with jobs_lock:
            job = next((item for item in jobs if item.get("id") == job_id), None)
            if not job or job.get("status") != "failed":
                return
            job["retry_scheduled"] = False
        try:
            retry_job(job_id)
        except Exception:
            update_job(job_id, retry_scheduled=False)

    threading.Thread(target=delayed_retry, name=f"mt-retry-{job_id}", daemon=True).start()


def should_use_site_cookies(target_url: str | None, cookies_path: str | None) -> bool:
    return bool(cookies_path and Path(cookies_path).exists() and get_platform(target_url) in {"x", "youtube", "bilibili", "douyin"})


def resolve_download_mode(platform: str, stream_url: str | None) -> str:
    if platform in {"youtube", "bilibili"}:
        return "ytdlp"
    if platform == "douyin":
        if stream_url and not is_m3u8_url(stream_url):
            return "direct"
        return "ytdlp"
    if platform == "x" and not stream_url:
        return "ytdlp"
    if stream_url and not is_m3u8_url(stream_url):
        return "direct"
    return "hls"


def build_preview_url(
    source_url: str,
    stream_url: str | None,
    referer: str | None = None,
    user_agent: str | None = None,
    proxy: str | None = None,
    stream_index: int | None = None,
) -> str:
    preview_url = f"{INTERNAL_BASE_URL}/api/preview.m3u8"
    if not stream_url:
        return preview_url

    preview_params = {"url": source_url, "stream_url": stream_url}
    if referer:
        preview_params["referer"] = referer
    if user_agent:
        preview_params["user_agent"] = user_agent
    if proxy:
        preview_params["proxy"] = proxy
    if stream_index is not None:
        preview_params["stream_index"] = str(stream_index)
    return requests.Request("GET", preview_url, params=preview_params).prepare().url


class ParsePayload(BaseModel):
    url: str
    referer: str | None = None
    user_agent: str | None = None
    proxy: str | None = None
    stream_url: str | None = None
    stream_index: int | None = None


class DownloadPayload(ParsePayload):
    output: str | None = None


class BatchDownloadPayload(ParsePayload):
    output: str | None = None
    download_all_streams: bool = True


class ConfigPayload(BaseModel):
    default_proxy: str | None = ""
    auto_retry_enabled: bool = False
    auto_retry_delay_seconds: int = 30
    auto_retry_max_attempts: int = 2
    xck: str | None = str(TWITTER_COOKIES_PATH)
    youtubeck: str | None = str(YOUTUBE_COOKIES_PATH)
    bilibilick: str | None = str(BILIBILI_COOKIES_PATH)
    douyinck: str | None = str(DOUYIN_COOKIES_PATH)
    twitter_cookies_path: str | None = None
    youtube_cookies_path: str | None = None
    bilibili_cookies_path: str | None = None
    douyin_cookies_path: str | None = None
    wecom_enabled: bool = False
    wecom_corp_id: str | None = ""
    wecom_agent_id: str | None = ""
    wecom_secret: str | None = ""
    wecom_token: str | None = ""
    wecom_encoding_aes_key: str | None = ""
    wecom_callback_url: str | None = ""


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    cfg = load_config()
    recent = list_recent_jobs(20)
    return templates.TemplateResponse(request, "index.html", {"config": cfg, "jobs": recent})


def get_platform(url: str | None) -> str:
    return detect_platform(url)


def is_x_url(url: str | None) -> bool:
    return get_platform(url) == "x"


def is_youtube_url(url: str | None) -> bool:
    return get_platform(url) == "youtube"


def is_bilibili_url(url: str | None) -> bool:
    return get_platform(url) == "bilibili"


def is_douyin_url(url: str | None) -> bool:
    return get_platform(url) == "douyin"


def resolve_site_cookies_path(url: str | None, cfg: dict) -> str | None:
    if is_x_url(url):
        return cfg.get('xck') or cfg.get('twitter_cookies_path') or str(TWITTER_COOKIES_PATH)
    if is_youtube_url(url):
        return cfg.get('youtubeck') or cfg.get('youtube_cookies_path') or str(YOUTUBE_COOKIES_PATH)
    if is_bilibili_url(url):
        return cfg.get('bilibilick') or cfg.get('bilibili_cookies_path') or str(BILIBILI_COOKIES_PATH)
    if is_douyin_url(url):
        configured = cfg.get('douyinck') or cfg.get('douyin_cookies_path') or str(DOUYIN_COOKIES_PATH)
        fresh_path = DOUYIN_FRESH_COOKIES_PATH
        configured_path = Path(str(configured))
        if fresh_path.exists():
            if not configured_path.exists():
                return str(fresh_path)
            try:
                if fresh_path.stat().st_mtime >= configured_path.stat().st_mtime:
                    return str(fresh_path)
            except Exception:
                return str(fresh_path)
        return str(configured_path)
    return cfg.get('xck') or cfg.get('twitter_cookies_path') or str(TWITTER_COOKIES_PATH)


@app.post("/api/parse")
def parse_url(payload: ParsePayload):
    cfg = load_config()
    proxy = payload.proxy or cfg.get("default_proxy") or None
    input_url = normalize_input_url(payload.url)
    if not input_url:
        raise HTTPException(status_code=400, detail="请提供有效链接")
    cookies_path = resolve_site_cookies_path(input_url, cfg)
    info = discover_stream(
        input_url,
        payload.referer,
        payload.user_agent,
        proxy,
        payload.stream_url,
        payload.stream_index,
        cookies_path,
    )
    if not info.get("resolved_url"):
        detail = "未解析到可下载视频"
        if info.get("errors"):
            detail = "解析失败：\n" + "\n".join(info["errors"][-2:])
            if get_platform(input_url) == "bilibili" and should_hint_bilibili_cookies(detail):
                detail += "\n建议：上传有效的 Bilibili cookies.txt 后重试（当前大概率被 412 风控拦了）"
        if get_platform(input_url) == "douyin" and "fresh cookies" in detail.lower():
            detail += "\n建议：重新在浏览器打开目标抖音链接，过完风控/验证码后，立刻导出最新 cookies.txt 覆盖 /app/data/cookies/douyin.fresh.cookies.txt 再重试"
        raise HTTPException(status_code=404, detail=detail)
    chosen_stream = choose_stream_url(info, payload.stream_url, payload.stream_index)
    preview_parts = [f"url={quote(input_url, safe='')}" ]
    if chosen_stream:
        preview_parts.append(f"stream_url={quote(chosen_stream, safe='')}")
    if payload.referer:
        preview_parts.append(f"referer={quote(payload.referer, safe='')}")
    if payload.user_agent:
        preview_parts.append(f"user_agent={quote(payload.user_agent, safe='')}")
    if proxy:
        preview_parts.append(f"proxy={quote(proxy, safe='')}")
    info["preview_url"] = "/api/preview.m3u8?" + "&".join(preview_parts)
    info["stream_count"] = len(info.get("streams") or [])
    fallback_prefix = get_platform(input_url) or "video"
    info["suggested_output"] = build_suggested_output_name(info.get("title"), fallback_prefix=fallback_prefix)
    return info


def direct_download(
    target_url: str,
    output_path: Path,
    referer: str | None = None,
    user_agent: str | None = None,
    proxy: str | None = None,
    progress_callback=None,
    should_cancel=None,
):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    effective_user_agent = user_agent
    effective_referer = referer
    if get_platform(target_url) == "douyin":
        effective_user_agent = effective_user_agent or "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
        effective_referer = effective_referer or "https://www.iesdouyin.com/"
    headers = build_headers(effective_referer, effective_user_agent)
    proxies = build_proxies(proxy)
    with requests.get(target_url, headers=headers, proxies=proxies, timeout=60, stream=True) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get('content-length') or 0)
        downloaded = 0
        with open(output_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=1024 * 256):
                if should_cancel and should_cancel():
                    raise RuntimeError('下载已取消')
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)
                if progress_callback:
                    percent = int(downloaded * 100 / total) if total > 0 else min(99, max(8, downloaded // (1024 * 1024)))
                    percent = max(8, min(99, percent))
                    mb = downloaded / 1024 / 1024
                    total_mb = total / 1024 / 1024 if total > 0 else None
                    status = f"已下载 {mb:.1f}MB"
                    if total_mb:
                        status += f" / {total_mb:.1f}MB"
                    progress_callback(percent, status)
    if progress_callback:
        progress_callback(100, '下载完成')


def run_download_job(
    job_id: str,
    preview_url: str,
    output_path: Path,
    aggressive: bool = True,
    stream_url: str | None = None,
    referer: str | None = None,
    user_agent: str | None = None,
    proxy: str | None = None,
    download_via: str = "hls",
    source_url: str | None = None,
):
    active_slot = min(MAX_CONCURRENT_DOWNLOADS, count_active_jobs() + 1)
    update_job(job_id, status="downloading", started_at=iso_now(), progress=8, status_text=f"开始下载 · 当前下载槽位 {active_slot}/{MAX_CONCURRENT_DOWNLOADS}")

    def on_progress(progress: int, status_text: str):
        update_job(job_id, status="downloading", progress=progress, status_text=status_text)

    def should_cancel() -> bool:
        with jobs_lock:
            for job in jobs:
                if job.get("id") == job_id:
                    return bool(job.get("cancel_requested"))
        return False

    try:
        if download_via == "ytdlp":
            target_url = source_url or stream_url or preview_url
            cfg = load_config()
            cookies_path = resolve_site_cookies_path(source_url or target_url, cfg)
            use_cookies = should_use_site_cookies(source_url or target_url, cookies_path)
            status_note = "（带 cookies）" if use_cookies else ""
            force_mp4 = get_platform(source_url or target_url) == "youtube"
            update_job(job_id, status="downloading", progress=8, status_text=f"开始抓取视频{status_note} · 当前下载槽位 {active_slot}/{MAX_CONCURRENT_DOWNLOADS}")
            download_with_ytdlp(target_url, output_path, referer=referer, user_agent=user_agent, proxy=proxy, cookies_path=cookies_path if use_cookies else None, progress_callback=on_progress, should_cancel=should_cancel, force_mp4=force_mp4)
        elif download_via == "direct":
            update_job(job_id, status="downloading", progress=8, status_text=f"开始直连下载 · 当前下载槽位 {active_slot}/{MAX_CONCURRENT_DOWNLOADS}")
            direct_download(stream_url or preview_url, output_path, referer=referer, user_agent=user_agent, proxy=proxy, progress_callback=on_progress, should_cancel=should_cancel)
        elif aggressive and stream_url:
            try:
                aggressive_hls_download(stream_url, output_path, referer=referer, user_agent=user_agent, proxy=proxy, progress_callback=on_progress, should_cancel=should_cancel)
            except Exception as exc:
                if should_cancel():
                    raise RuntimeError("下载已取消")
                update_job(job_id, status="downloading", progress=6, status_text=f"主方案失败，已切到兼容下载 · {exc}")
                ffmpeg_download(stream_url or preview_url, output_path, referer=referer, user_agent=user_agent, proxy=proxy, progress_callback=on_progress, should_cancel=should_cancel)
        else:
            ffmpeg_download(stream_url or preview_url, output_path, referer=referer, user_agent=user_agent, proxy=proxy, progress_callback=on_progress, should_cancel=should_cancel)
        if should_cancel():
            raise RuntimeError("下载已取消")
        update_job(job_id, status="done", progress=100, status_text="下载完成", finished_at=iso_now(), retry_scheduled=False)
    except Exception as exc:
        cancelled = "取消" in str(exc)
        if cancelled and output_path.exists() and output_path.is_file():
            output_path.unlink(missing_ok=True)
        updated = update_job(
            job_id,
            status="failed" if not cancelled else "cancelled",
            progress=100 if not cancelled else 0,
            status_text="下载失败" if not cancelled else "已取消",
            error="" if cancelled else str(exc),
            finished_at=iso_now(),
            retry_scheduled=False,
        )
        if not cancelled and updated:
            cfg = load_config()
            retry_enabled = bool(cfg.get("auto_retry_enabled"))
            retry_delay = max(1, int(cfg.get("auto_retry_delay_seconds") or 30))
            retry_max_attempts = max(0, int(cfg.get("auto_retry_max_attempts") or 0))
            current_retry_count = int(updated.get("retry_count") or 0)
            if retry_enabled and current_retry_count < retry_max_attempts and not updated.get("retry_scheduled"):
                update_job(job_id, retry_scheduled=True, status_text=f"下载失败，将在 {retry_delay}s 后自动重试…")
                schedule_retry(job_id, retry_delay)


def create_download_job(payload: DownloadPayload, retry_of: str | None = None):
    cfg = load_config()
    proxy = payload.proxy or cfg.get("default_proxy") or None
    input_url = normalize_input_url(payload.url)
    if not input_url:
        raise HTTPException(status_code=400, detail="请提供有效链接")
    if payload.stream_url:
        selected_is_m3u8 = is_m3u8_url(payload.stream_url)
        info = {
            "source_url": input_url,
            "resolved_url": payload.stream_url,
            "title": None,
            "thumbnail": None,
            "is_m3u8": selected_is_m3u8,
            "extractor": "selected-stream",
            "streams": [payload.stream_url],
            "stream_options": [],
        }
    else:
        cookies_path = resolve_site_cookies_path(input_url, cfg)
        info = discover_stream(
            input_url,
            payload.referer,
            payload.user_agent,
            proxy,
            payload.stream_url,
            payload.stream_index,
            cookies_path,
        )
    download_dir = get_download_subdir(input_url)
    stream_url = payload.stream_url or choose_stream_url(info, payload.stream_url, payload.stream_index)
    extractor = str(info.get("extractor") or "")
    platform = get_platform(input_url)
    x_url = platform == "x"
    youtube_url = platform == "youtube"
    bilibili_url = platform == "bilibili"
    use_ytdlp_fallback = (not stream_url and x_url) or youtube_url or bilibili_url or platform == "douyin"
    if not stream_url and not use_ytdlp_fallback:
        raise HTTPException(status_code=404, detail="未解析到可下载视频")

    suggested_name = payload.output or info.get("title") or f"video-{uuid4().hex[:8]}"
    output_name = allocate_output_name(suggested_name, download_dir=download_dir)
    output_path = download_dir / output_name

    resp_url = build_preview_url(
        input_url,
        stream_url,
        payload.referer,
        payload.user_agent,
        proxy,
        payload.stream_index,
    )

    retry_count = 0
    if retry_of:
        with jobs_lock:
            source_job = next((item for item in jobs if item.get("id") == retry_of), None)
            retry_count = int(source_job.get("retry_count") or 0) + 1 if source_job else 1

    queued_ahead = count_queued_jobs()
    active_now = count_active_jobs()
    download_via = resolve_download_mode(platform, stream_url)
    if download_via == "ytdlp" and not payload.output and x_url:
        base_name = info.get("title") or f"x-video-{uuid4().hex[:8]}"
        output_name = allocate_output_name(base_name, download_dir=download_dir)
        output_path = download_dir / output_name

    now = iso_now()
    job = {
        "id": uuid4().hex[:10],
        "source_url": input_url,
        "stream_url": stream_url,
        "stream_index": payload.stream_index,
        "output": output_name,
        "download_dir": str(download_dir),
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "finished_at": None,
        "proxy": proxy or "",
        "status": "queued",
        "status_text": f"排队中 · 当前下载槽位 {active_now}/{MAX_CONCURRENT_DOWNLOADS}" + (f"，前面还有 {queued_ahead} 个任务" if queued_ahead else ""),
        "progress": 0,
        "title": info.get("title"),
        "platform": platform,
        "error": "",
        "retry_count": retry_count,
        "retry_of": retry_of or "",
        "retry_scheduled": False,
        "download_via": download_via,
        "extractor": extractor,
        "request_payload": payload.model_dump(),
        "wecom_to_user": "",
        "wecom_completion_notified": False,
        "wecom_completion_notified_at": None,
    }
    add_job(job)

    download_executor.submit(
        run_download_job,
        job["id"],
        resp_url,
        output_path,
        True,
        stream_url,
        payload.referer,
        payload.user_agent,
        proxy,
        download_via,
        payload.url,
    )
    return job


def retry_job(job_id: str):
    with jobs_lock:
        source_job = next((item for item in jobs if item.get("id") == job_id), None)
        if not source_job:
            raise HTTPException(status_code=404, detail="任务不存在")
        if source_job.get("status") not in {"failed", "cancelled"}:
            raise HTTPException(status_code=400, detail="当前任务状态不支持重试")
        payload = dict(source_job.get("request_payload") or {})
        if not payload.get("url"):
            raise HTTPException(status_code=400, detail="原始任务缺少重试参数")
        source_job["retry_scheduled"] = False

    new_job = create_download_job(DownloadPayload(**payload), retry_of=job_id)
    update_job(
        job_id,
        status="retried",
        status_text=f"已发起重试，新任务：{new_job['output']}",
        error="",
        progress=100,
        finished_at=iso_now(),
        retried_by=new_job["id"],
    )
    return new_job


@app.post("/api/download")
def download(request: Request, payload: DownloadPayload):
    return create_download_job(payload)


@app.post("/api/download/all")
def download_all(request: Request, payload: BatchDownloadPayload):
    cfg = load_config()
    proxy = payload.proxy or cfg.get("default_proxy") or None
    input_url = normalize_input_url(payload.url)
    if not input_url:
        raise HTTPException(status_code=400, detail="请提供有效链接")
    cookies_path = resolve_site_cookies_path(input_url, cfg)
    info = discover_stream(
        input_url,
        payload.referer,
        payload.user_agent,
        proxy,
        payload.stream_url,
        payload.stream_index,
        cookies_path,
    )
    streams = info.get("streams") or []
    if not streams:
        raise HTTPException(status_code=404, detail="未解析到可下载视频")

    platform = get_platform(payload.url)
    if platform == "x":
        best_stream = choose_stream_url(info)
        if not best_stream:
            raise HTTPException(status_code=404, detail="未解析到可下载视频")
        best_index = next((i for i, s in enumerate(streams) if s == best_stream), 0)
        job_payload = DownloadPayload(
            url=payload.url,
            output=payload.output or info.get("title") or f"video-{uuid4().hex[:8]}",
            referer=payload.referer,
            user_agent=payload.user_agent,
            proxy=payload.proxy,
            stream_url=best_stream,
            stream_index=best_index,
        )
        job = create_download_job(job_payload)
        return {"ok": True, "title": info.get("title") or job_payload.output, "stream_count": 1, "jobs": [job]}

    jobs_created = []
    base_title = payload.output or info.get("title") or f"video-{uuid4().hex[:8]}"
    for index, stream in enumerate(streams):
        suffix_name = f"{base_title} - {index + 1}" if len(streams) > 1 else base_title
        job_payload = DownloadPayload(
            url=payload.url,
            output=suffix_name,
            referer=payload.referer,
            user_agent=payload.user_agent,
            proxy=payload.proxy,
            stream_url=stream,
            stream_index=index,
        )
        jobs_created.append(create_download_job(job_payload))
    return {"ok": True, "title": info.get("title") or base_title, "stream_count": len(streams), "jobs": jobs_created}


@app.get("/api/jobs")
def list_jobs():
    return list_recent_jobs(50)


@app.post("/api/jobs/{job_id}/delete")
def delete_job(job_id: str):
    with jobs_lock:
        target = next((job for job in jobs if job.get("id") == job_id), None)
        if not target:
            raise HTTPException(status_code=404, detail="任务不存在")

        if target.get("status") == "downloading":
            target["cancel_requested"] = True
            target["status_text"] = "已请求取消，等待任务停止…"
            return {"ok": True, "job_id": job_id, "cancelling": True}

    job = remove_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")

    deleted_file = False
    output_name = job.get("output") or ""
    target_dir = Path(str(job.get("download_dir") or DOWNLOAD_DIR))
    if output_name:
        output_path = target_dir / output_name
        if output_path.exists() and output_path.is_file():
            output_path.unlink(missing_ok=True)
            deleted_file = True

    return {"ok": True, "job_id": job_id, "deleted_file": deleted_file, "output": output_name}


@app.post("/api/jobs/{job_id}/retry")
def retry_job_api(job_id: str):
    job = retry_job(job_id)
    return {"ok": True, "job_id": job_id, "new_job": job}


@app.post("/api/jobs/clear-history")
def clear_history():
    removed = clear_history_jobs()
    return {"ok": True, "removed": removed}


@app.get("/api/preview.m3u8")
def preview_m3u8(
    request: Request,
    url: str,
    referer: str | None = None,
    user_agent: str | None = None,
    proxy: str | None = None,
    stream_url: str | None = None,
    stream_index: int | None = None,
):
    cfg = load_config()
    actual_proxy = proxy or cfg.get("default_proxy") or None
    if stream_url:
        selected_stream = stream_url
    else:
        cookies_path = resolve_site_cookies_path(url, cfg)
        info = discover_stream(url, referer, user_agent, actual_proxy, stream_url, stream_index, cookies_path)
        selected_stream = choose_stream_url(info, stream_url, stream_index)
    if not selected_stream:
        raise HTTPException(status_code=404, detail="未解析到 m3u8 流")

    resp = safe_requests_get(selected_stream, referer=referer, user_agent=user_agent, proxy=actual_proxy, timeout=30)
    resp.raise_for_status()
    proxy_prefix = f"{str(request.base_url)}api/media/"
    content = rewrite_m3u8_manifest(resp.text, selected_stream, proxy_prefix, referer, user_agent, actual_proxy)
    return Response(content=content, media_type="application/vnd.apple.mpegurl")


@app.get("/api/media")
@app.get("/api/media/{name:path}")
def media_proxy(name: str = "", target: str = "", referer: str | None = None, user_agent: str | None = None, proxy: str | None = None):
    cfg = load_config()
    actual_proxy = proxy or cfg.get("default_proxy") or None
    try:
        r = safe_requests_get(target, referer=referer, user_agent=user_agent, proxy=actual_proxy, timeout=60)
        r.raise_for_status()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"媒体分片拉取失败：{exc}")
    return Response(content=r.content, media_type=r.headers.get("content-type", "application/octet-stream"))


@app.get("/api/config")
def get_config():
    cfg = load_config()
    cfg.setdefault("xck", cfg.get("twitter_cookies_path") or str(TWITTER_COOKIES_PATH))
    cfg.setdefault("youtubeck", cfg.get("youtube_cookies_path") or str(YOUTUBE_COOKIES_PATH))
    cfg.setdefault("bilibilick", cfg.get("bilibili_cookies_path") or str(BILIBILI_COOKIES_PATH))
    cfg.setdefault("douyinck", cfg.get("douyin_cookies_path") or str(DOUYIN_COOKIES_PATH))
    cfg["twitter_cookies_path"] = cfg.get("xck") or str(TWITTER_COOKIES_PATH)
    cfg["youtube_cookies_path"] = cfg.get("youtubeck") or str(YOUTUBE_COOKIES_PATH)
    cfg["bilibili_cookies_path"] = cfg.get("bilibilick") or str(BILIBILI_COOKIES_PATH)
    cfg["douyin_cookies_path"] = cfg.get("douyinck") or str(DOUYIN_COOKIES_PATH)
    cfg["xck_exists"] = Path(str(cfg.get("xck") or TWITTER_COOKIES_PATH)).exists()
    cfg["youtubeck_exists"] = Path(str(cfg.get("youtubeck") or YOUTUBE_COOKIES_PATH)).exists()
    cfg["bilibilick_exists"] = Path(str(cfg.get("bilibilick") or BILIBILI_COOKIES_PATH)).exists()
    cfg["douyinck_exists"] = Path(str(cfg.get("douyinck") or DOUYIN_COOKIES_PATH)).exists()
    cfg["twitter_cookies_exists"] = cfg["xck_exists"]
    cfg["youtube_cookies_exists"] = cfg["youtubeck_exists"]
    cfg["bilibili_cookies_exists"] = cfg["bilibilick_exists"]
    cfg["douyin_cookies_exists"] = cfg["douyinck_exists"]
    cfg["wecom_ready"] = is_wecom_ready(cfg)
    cfg["wecom_secret_masked"] = mask_secret(cfg.get("wecom_secret"))
    cfg["wecom_token_masked"] = mask_secret(cfg.get("wecom_token"))
    cfg["wecom_encoding_aes_key_masked"] = mask_secret(cfg.get("wecom_encoding_aes_key"), keep=4)
    return cfg


@app.post("/api/config")
def set_config(payload: ConfigPayload):
    cfg = load_config()
    cfg["default_proxy"] = payload.default_proxy or ""
    cfg["auto_retry_enabled"] = bool(payload.auto_retry_enabled)
    cfg["auto_retry_delay_seconds"] = max(1, int(payload.auto_retry_delay_seconds or 30))
    cfg["auto_retry_max_attempts"] = max(0, int(payload.auto_retry_max_attempts or 0))
    cfg["xck"] = payload.xck or payload.twitter_cookies_path or cfg.get("xck") or str(TWITTER_COOKIES_PATH)
    cfg["youtubeck"] = payload.youtubeck or payload.youtube_cookies_path or cfg.get("youtubeck") or str(YOUTUBE_COOKIES_PATH)
    cfg["bilibilick"] = payload.bilibilick or payload.bilibili_cookies_path or cfg.get("bilibilick") or str(BILIBILI_COOKIES_PATH)
    cfg["douyinck"] = payload.douyinck or payload.douyin_cookies_path or cfg.get("douyinck") or str(DOUYIN_COOKIES_PATH)
    cfg["twitter_cookies_path"] = cfg["xck"]
    cfg["youtube_cookies_path"] = cfg["youtubeck"]
    cfg["bilibili_cookies_path"] = cfg["bilibilick"]
    cfg["douyin_cookies_path"] = cfg["douyinck"]
    cfg["wecom_enabled"] = bool(payload.wecom_enabled)
    cfg["wecom_corp_id"] = str(payload.wecom_corp_id or "").strip()
    cfg["wecom_agent_id"] = str(payload.wecom_agent_id or "").strip()
    cfg["wecom_secret"] = str(payload.wecom_secret or "").strip()
    cfg["wecom_token"] = str(payload.wecom_token or "").strip()
    cfg["wecom_encoding_aes_key"] = str(payload.wecom_encoding_aes_key or "").strip()
    cfg["wecom_callback_url"] = str(payload.wecom_callback_url or "").strip()
    save_config(cfg)
    cfg["xck_exists"] = Path(str(cfg.get("xck") or TWITTER_COOKIES_PATH)).exists()
    cfg["youtubeck_exists"] = Path(str(cfg.get("youtubeck") or YOUTUBE_COOKIES_PATH)).exists()
    cfg["bilibilick_exists"] = Path(str(cfg.get("bilibilick") or BILIBILI_COOKIES_PATH)).exists()
    cfg["douyinck_exists"] = Path(str(cfg.get("douyinck") or DOUYIN_COOKIES_PATH)).exists()
    cfg["twitter_cookies_exists"] = cfg["xck_exists"]
    cfg["youtube_cookies_exists"] = cfg["youtubeck_exists"]
    cfg["bilibili_cookies_exists"] = cfg["bilibilick_exists"]
    cfg["douyin_cookies_exists"] = cfg["douyinck_exists"]
    cfg["wecom_ready"] = is_wecom_ready(cfg)
    cfg["wecom_secret_masked"] = mask_secret(cfg.get("wecom_secret"))
    cfg["wecom_token_masked"] = mask_secret(cfg.get("wecom_token"))
    cfg["wecom_encoding_aes_key_masked"] = mask_secret(cfg.get("wecom_encoding_aes_key"), keep=4)
    return cfg


@app.get("/api/wecom/callback")
def wecom_callback_verify(msg_signature: str = "", timestamp: str = "", nonce: str = "", echostr: str = ""):
    cfg = load_config()
    try:
        crypto = get_wecom_crypto(cfg)
        plain = crypto.decrypt_echostr(msg_signature, timestamp, nonce, echostr)
        return PlainTextResponse(content=plain)
    except Exception as exc:
        raise HTTPException(status_code=403, detail=f"企业微信 URL 校验失败：{exc}")


@app.post("/api/wecom/callback")
async def wecom_callback_receive(request: Request, msg_signature: str = "", timestamp: str = "", nonce: str = ""):
    cfg = load_config()
    body = await request.body()
    try:
        crypto = get_wecom_crypto(cfg)
        msg = crypto.decrypt_message_xml(body.decode("utf-8"), msg_signature, timestamp, nonce)
    except Exception as exc:
        raise HTTPException(status_code=403, detail=f"企业微信消息解密失败：{exc}")

    msg_type = str(msg.get("MsgType") or "").strip().lower()
    event = str(msg.get("Event") or "").strip().lower()

    if msg_type == "text":
        from_user = str(msg.get("FromUserName") or "").strip()
        to_user = str(msg.get("ToUserName") or "").strip()
        content = str(msg.get("Content") or "").strip()
        url = normalize_input_url(content)
        platform = get_platform(url) if url and re.search(r"https?://", url, re.IGNORECASE) else "generic"
        ack = build_wecom_passive_ack(url, platform) if url and re.search(r"https?://", url, re.IGNORECASE) else "没识别到可下载链接。直接发文本链接就行，我会自动接单。"
        threading.Thread(target=handle_wecom_download_message, args=(msg,), name=f"wecom-job-{uuid4().hex[:6]}", daemon=True).start()
        passive_plain = build_passive_text_reply(to_user=from_user, from_user=to_user, content=ack)
        encrypted = crypto.encrypt(passive_plain, nonce=nonce, timestamp=timestamp)
        return Response(content=encrypted["xml"], media_type="application/xml")
    elif msg_type == "event":
        print(f"[wecom] event received: {event}")
    else:
        print(f"[wecom] unsupported msg type: {msg_type}")

    return PlainTextResponse(content="success")


@app.post("/api/upload/twitter-cookies")
async def upload_twitter_cookies(file: UploadFile = File(...)):
    filename = (file.filename or "").lower()
    if not filename.endswith(".txt"):
        raise HTTPException(status_code=400, detail="只收浏览器导出的 cookies.txt")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="cookies 文件是空的")
    TWITTER_COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    TWITTER_COOKIES_PATH.write_bytes(content)

    cfg = load_config()
    cfg["twitter_cookies_path"] = str(TWITTER_COOKIES_PATH)
    save_config(cfg)

    return {
        "ok": True,
        "path": str(TWITTER_COOKIES_PATH),
        "size": len(content),
        "twitter_cookies_exists": True,
    }


@app.post("/api/upload/youtube-cookies")
async def upload_youtube_cookies(file: UploadFile = File(...)):
    filename = (file.filename or "").lower()
    if not filename.endswith(".txt"):
        raise HTTPException(status_code=400, detail="只收浏览器导出的 cookies.txt")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="cookies 文件是空的")
    YOUTUBE_COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    YOUTUBE_COOKIES_PATH.write_bytes(content)

    cfg = load_config()
    cfg["youtube_cookies_path"] = str(YOUTUBE_COOKIES_PATH)
    save_config(cfg)

    return {
        "ok": True,
        "path": str(YOUTUBE_COOKIES_PATH),
        "size": len(content),
        "youtube_cookies_exists": True,
    }


@app.post("/api/upload/bilibili-cookies")
async def upload_bilibili_cookies(file: UploadFile = File(...)):
    filename = (file.filename or "").lower()
    if not filename.endswith(".txt"):
        raise HTTPException(status_code=400, detail="只收浏览器导出的 cookies.txt")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="cookies 文件是空的")
    BILIBILI_COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    BILIBILI_COOKIES_PATH.write_bytes(content)

    cfg = load_config()
    cfg["bilibili_cookies_path"] = str(BILIBILI_COOKIES_PATH)
    save_config(cfg)

    return {
        "ok": True,
        "path": str(BILIBILI_COOKIES_PATH),
        "size": len(content),
        "bilibili_cookies_exists": True,
    }
