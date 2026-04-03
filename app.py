import asyncio
import os
import re
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import uuid4

import requests
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, Response, StreamingResponse
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
    route_proxy_for_url,
    download_with_ytdlp,
    ffmpeg_download,
    is_direct_media_url,
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
APP_VERSION = os.getenv("APP_VERSION", "dev").strip() or "dev"
APP_COMMIT = os.getenv("APP_COMMIT", "unknown").strip() or "unknown"
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
PARSE_EXECUTOR_WORKERS = max(2, int(os.getenv("PARSE_EXECUTOR_WORKERS", "4")))
MEDIA_EXECUTOR_WORKERS = max(4, int(os.getenv("MEDIA_EXECUTOR_WORKERS", "12")))
parse_executor = ThreadPoolExecutor(max_workers=PARSE_EXECUTOR_WORKERS, thread_name_prefix="mt-parse")
media_executor = ThreadPoolExecutor(max_workers=MEDIA_EXECUTOR_WORKERS, thread_name_prefix="mt-media")
WECOM_FINAL_STATUSES = {"done", "failed"}
CONFIG_KEEP_SENTINEL = "__KEEP__"
WECOM_TITLE_MAX_LEN = 80
WECOM_FILE_MAX_LEN = 100
WECOM_STATUS_MAX_LEN = 120
WECOM_ERROR_MAX_LEN = 180
WECOM_URL_MAX_LEN = 96
WECOM_MESSAGE_MAX_LEN = 420


def clean_wecom_text(value: str | None) -> str:
    text = str(value or "")
    text = text.replace("\u200b", " ").replace("\xa0", " ")
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def truncate_wecom_text(value: str | None, limit: int, *, ellipsis: str = "…") -> str:
    text = clean_wecom_text(value)
    if not text or limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= len(ellipsis):
        return ellipsis[:limit]
    return text[: limit - len(ellipsis)].rstrip() + ellipsis


def shorten_wecom_url(url: str | None, limit: int = WECOM_URL_MAX_LEN) -> str:
    text = clean_wecom_text(url)
    if not text:
        return ""
    if len(text) <= limit:
        return text
    head = max(12, limit // 2 - 2)
    tail = max(12, limit - head - 1)
    if head + tail + 1 > limit:
        tail = max(1, limit - head - 1)
    return f"{text[:head].rstrip()}…{text[-tail:].lstrip()}"


def format_wecom_field(label: str, value: str | None, limit: int) -> str | None:
    text = truncate_wecom_text(value, limit)
    if not text:
        return None
    return f"{label}：{text}"


def compact_wecom_message(lines: list[str], max_len: int = WECOM_MESSAGE_MAX_LEN) -> str:
    filtered = [clean_wecom_text(line) for line in lines if clean_wecom_text(line)]
    if not filtered:
        return ""
    message = "\n".join(filtered)
    if len(message) <= max_len:
        return message
    compacted = []
    for line in filtered:
        if line.startswith("来源："):
            compacted.append(format_wecom_field("来源", line.split("：", 1)[1], 72) or "")
        elif line.startswith("原因："):
            compacted.append(format_wecom_field("原因", line.split("：", 1)[1], 120) or "")
        elif line.startswith("状态："):
            compacted.append(format_wecom_field("状态", line.split("：", 1)[1], 80) or "")
        else:
            compacted.append(line)
    compacted = [line for line in compacted if line]
    message = "\n".join(compacted)
    if len(message) <= max_len:
        return message
    for drop_prefix in ("来源：", "状态："):
        trimmed = [line for line in compacted if not line.startswith(drop_prefix)]
        candidate = "\n".join(trimmed)
        if candidate and len(candidate) <= max_len:
            return candidate
    return truncate_wecom_text(message, max_len)


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


def get_wecom_client(cfg: dict, *, api_base_url: str | None = None) -> WeComClient:
    if not is_wecom_ready(cfg):
        raise ValueError("企业微信配置不完整或未启用")
    return WeComClient(
        corp_id=str(cfg.get("wecom_corp_id") or ""),
        agent_id=str(cfg.get("wecom_agent_id") or "0"),
        secret=str(cfg.get("wecom_secret") or ""),
        api_base_url=api_base_url,
    )


def get_wecom_forward_url(cfg: dict | None = None) -> str:
    active_cfg = cfg or load_config()
    return str(active_cfg.get("wecom_forward_url") or os.getenv("WECOM_FORWARD_URL") or "").strip().rstrip("/")


def get_wecom_forward_token(cfg: dict | None = None) -> str:
    active_cfg = cfg or load_config()
    return str(active_cfg.get("wecom_forward_token") or os.getenv("WECOM_FORWARD_TOKEN") or "").strip()


def is_wecom_forward_enabled(cfg: dict | None = None) -> bool:
    return bool(get_wecom_forward_url(cfg))


def is_wecom_forward_proxy_url(forward_url: str | None) -> bool:
    value = str(forward_url or "").strip()
    if not value:
        return False
    try:
        parts = urlsplit(value)
    except Exception:
        return False
    if not parts.scheme or not parts.netloc:
        return False
    path = (parts.path or "").rstrip("/")
    return path in {"", "/cgi-bin/message/send", "/cgi-bin/gettoken"}


def build_wecom_forward_payload(job: dict, kind: str, to_user: str, content: str) -> dict:
    status = str(job.get("status") or "").strip().lower() or kind
    return {
        "kind": kind,
        "job_id": str(job.get("id") or "").strip(),
        "to_user": str(to_user or "").strip(),
        "content": str(content or "").strip(),
        "title": clean_wecom_text(job.get("title") or job.get("output")),
        "status": status,
        "error": clean_wecom_text(job.get("error") or ""),
        "source_url": clean_wecom_text(job.get("source_url") or ""),
        "platform": clean_wecom_text(job.get("platform") or ""),
        "output": clean_wecom_text(job.get("output") or ""),
        "status_text": clean_wecom_text(job.get("status_text") or ""),
    }


def send_wecom_forward_notification(job: dict, kind: str, to_user: str, content: str, cfg: dict | None = None) -> dict:
    active_cfg = cfg or load_config()
    forward_url = get_wecom_forward_url(active_cfg)
    if not forward_url:
        raise RuntimeError("WECOM_FORWARD_URL 未配置")

    if is_wecom_forward_proxy_url(forward_url):
        client = get_wecom_client(active_cfg, api_base_url=forward_url)
        result = client.send_text(to_user, content)
        data = {
            "ok": True,
            "route": "wxchat-proxy",
            "msgid": result.get("msgid"),
            "errcode": result.get("errcode", 0),
            "errmsg": result.get("errmsg", "ok"),
        }
        print(f"[wecom-forward] wxchat proxy ok: kind={kind} job_id={job.get('id')} to={to_user} msgid={data.get('msgid')}")
        return data

    headers = {}
    forward_token = get_wecom_forward_token(active_cfg)
    if forward_token:
        headers["X-Wecom-Forward-Token"] = forward_token
    payload = build_wecom_forward_payload(job, kind, to_user, content)
    resp = requests.post(
        forward_url,
        headers=headers or None,
        json=payload,
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok", True):
        raise RuntimeError(data.get("detail") or str(data))
    print(f"[wecom-forward] notify ok: kind={kind} job_id={payload['job_id']} to={to_user} msgid={data.get('msgid')}")
    return data


def send_wecom_job_notification(job: dict, kind: str, to_user: str, content: str) -> dict:
    cfg = load_config()
    if is_wecom_forward_enabled(cfg):
        return send_wecom_forward_notification(job, kind, to_user, content, cfg=cfg)
    return send_wecom_text(to_user, content)


def send_wecom_text(to_user: str, content: str) -> dict:
    target_user = str(to_user or "").strip()
    cfg = load_config()
    client = get_wecom_client(cfg)
    result = client.send_text(target_user, content)
    print(f"[wecom] send_text ok: to={target_user} msgid={result.get('msgid')}")
    return result


def trigger_wecom_notification_async(kind: str, job: dict | None = None, job_id: str | None = None):
    active_job_id = str(job_id or (job or {}).get("id") or "").strip()
    if not active_job_id or kind not in {"started", "done", "failed"}:
        return

    def worker():
        snapshot = get_job_snapshot(active_job_id) or dict(job or {})
        if not snapshot:
            return
        if kind == "started":
            notify_wecom_job_started(snapshot)
        elif kind == "done":
            notify_wecom_job_done(snapshot)
        else:
            notify_wecom_job_failed(snapshot)

    threading.Thread(target=worker, name=f"wecom-notify-{kind}-{active_job_id[:6]}", daemon=True).start()


def send_wecom_text_async(to_user: str, content: str):
    def worker():
        try:
            send_wecom_text(to_user, content)
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


def resolve_job_display_name(job: dict | None = None, platform: str | None = None, fallback: str | None = None) -> str:
    candidates = []
    if job:
        candidates.extend([
            job.get("title"),
            job.get("output"),
            job.get("source_url"),
        ])
    if fallback:
        candidates.append(fallback)
    for candidate in candidates:
        value = clean_wecom_text(candidate)
        if value:
            return value
    pretty = prettify_platform(platform or (job or {}).get("platform"))
    return f"{pretty} 任务"


def build_wecom_passive_ack(url: str, platform: str) -> str:
    prefix = build_wecom_prefix(platform)
    return f"{prefix} 收到任务，正在创建任务，请稍等。"


def enrich_config_view(cfg: dict) -> dict:
    cfg = dict(cfg or {})
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
    cfg["wecom_forward_enabled"] = is_wecom_forward_enabled(cfg)
    cfg["wecom_secret_masked"] = mask_secret(cfg.get("wecom_secret"))
    cfg["wecom_token_masked"] = mask_secret(cfg.get("wecom_token"))
    cfg["wecom_encoding_aes_key_masked"] = mask_secret(cfg.get("wecom_encoding_aes_key"), keep=4)
    cfg["wecom_forward_token_masked"] = mask_secret(cfg.get("wecom_forward_token"))
    return cfg


async def save_uploaded_cookie_file(file: UploadFile, target_path: Path, config_key: str, exists_key: str) -> dict:
    filename = (file.filename or "").lower()
    if not filename.endswith(".txt"):
        raise HTTPException(status_code=400, detail="只收浏览器导出的 cookies.txt")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="cookies 文件是空的")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(content)

    cfg = load_config()
    cfg[config_key] = str(target_path)
    save_config(cfg)

    return {
        "ok": True,
        "path": str(target_path),
        "size": len(content),
        exists_key: True,
    }


def build_wecom_job_status_feedback(job: dict, kind: str) -> str:
    prefix = build_wecom_prefix(job.get("platform"))
    title = clean_wecom_text(job.get("title"))
    output = clean_wecom_text(job.get("output"))
    display_name = resolve_job_display_name(job)
    status_line = {
        "started": "开始下载",
        "done": "下载完成",
        "failed": "下载失败",
    }.get(kind, "任务状态更新")
    lines = [f"{prefix} {status_line}"]
    if title and output and title != output:
        lines.append(format_wecom_field("标题", title, WECOM_TITLE_MAX_LEN) or "")
        lines.append(format_wecom_field("文件", output, WECOM_FILE_MAX_LEN) or "")
    else:
        lines.append(format_wecom_field("文件", display_name, WECOM_FILE_MAX_LEN) or f"文件：{prettify_platform(job.get('platform'))} 任务")
    if job.get("id"):
        lines.append(f"任务ID：{job.get('id')}")
    if kind == "started":
        status_text = clean_wecom_text(job.get("status_text"))
        if status_text:
            lines.append(format_wecom_field("状态", status_text, WECOM_STATUS_MAX_LEN) or "状态：任务已进入下载阶段")
        else:
            lines.append("状态：任务已进入下载阶段")
    elif kind == "failed":
        error = clean_wecom_text(job.get("error") or job.get("status_text") or "未知原因")
        lines.append(format_wecom_field("原因", error, WECOM_ERROR_MAX_LEN) or "原因：未知原因")
    source = shorten_wecom_url(job.get("source_url"))
    if source and kind == "failed" and not output:
        lines.append(f"来源：{source}")
    return compact_wecom_message(lines)


def build_wecom_job_started_feedback(job: dict) -> str:
    return build_wecom_job_status_feedback(job, "started")


def build_wecom_job_done_feedback(job: dict) -> str:
    return build_wecom_job_status_feedback(job, "done")


def build_wecom_job_failed_feedback(job: dict) -> str:
    return build_wecom_job_status_feedback(job, "failed")


def claim_wecom_notification(job_id: str | None, kind: str) -> dict | None:
    if not job_id:
        return None
    flag_key = f"wecom_{kind}_notified"
    inflight_key = f"wecom_{kind}_notifying"
    with jobs_lock:
        for job in jobs:
            if job.get("id") != job_id:
                continue
            if is_job_hidden(job):
                return None
            if job.get(flag_key) or job.get(inflight_key):
                return None
            job[inflight_key] = True
            job.setdefault("updated_at", iso_now())
            return job.copy()
    return None


def finish_wecom_notification(job_id: str | None, kind: str, success: bool):
    if not job_id:
        return
    flag_key = f"wecom_{kind}_notified"
    at_key = f"wecom_{kind}_notified_at"
    inflight_key = f"wecom_{kind}_notifying"
    with jobs_lock:
        for job in jobs:
            if job.get("id") != job_id:
                continue
            job[inflight_key] = False
            if success:
                job[flag_key] = True
                job[at_key] = iso_now()
                job["updated_at"] = iso_now()
            return


def get_job_snapshot(job_id: str | None) -> dict | None:
    if not job_id:
        return None
    with jobs_lock:
        for job in jobs:
            if job.get("id") == job_id:
                return job.copy()
    return None


def should_notify_wecom(job: dict | None, kind: str) -> bool:
    if not job or is_job_hidden(job):
        return False
    if not str(job.get("wecom_to_user") or "").strip():
        return False
    status = str(job.get("status") or "").strip().lower()
    if kind == "started":
        return status == "downloading" and not job.get("wecom_started_notified")
    if kind == "done":
        return status == "done" and not job.get("wecom_done_notified")
    if kind == "failed":
        return status == "failed" and not job.get("wecom_failed_notified")
    return False


def notify_wecom_job_status(job: dict, kind: str, feedback_builder):
    job_id = str(job.get("id") or "").strip()
    snapshot = get_job_snapshot(job_id) or job.copy()
    if not should_notify_wecom(snapshot, kind):
        return
    claimed_job = claim_wecom_notification(job_id, kind)
    if not claimed_job:
        return
    to_user = str(claimed_job.get("wecom_to_user") or "").strip()
    if not to_user:
        finish_wecom_notification(job_id, kind, success=False)
        return
    try:
        send_wecom_job_notification(claimed_job.copy(), kind, to_user, feedback_builder(claimed_job.copy()))
    except Exception as exc:
        route = "forward" if is_wecom_forward_enabled() else "direct"
        print(f"[wecom] job_{kind} notify failed: route={route} job_id={job_id} to={to_user} error={exc}")
        finish_wecom_notification(job_id, kind, success=False)
        return
    finish_wecom_notification(job_id, kind, success=True)


def notify_wecom_job_started(job: dict):
    notify_wecom_job_status(job, "started", build_wecom_job_started_feedback)


def notify_wecom_job_done(job: dict):
    notify_wecom_job_status(job, "done", build_wecom_job_done_feedback)


def notify_wecom_job_failed(job: dict):
    notify_wecom_job_status(job, "failed", build_wecom_job_failed_feedback)


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
        payload = DownloadPayload(url=url, wecom_to_user=from_user)
        create_download_job(payload)
    except Exception as exc:
        send_wecom_text_async(from_user, f"{build_wecom_prefix(platform)} 任务创建失败：{exc}")


def iso_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def add_job(job: dict):
    with jobs_lock:
        jobs.append(job)


def is_job_hidden(job: dict) -> bool:
    return bool(job.get("deleted"))


def update_job(job_id: str, **updates):
    notify_jobs: list[tuple[str, dict]] = []
    with jobs_lock:
        for job in jobs:
            if job.get("id") == job_id:
                previous_status = str(job.get("status") or "").strip().lower()
                updates.setdefault("updated_at", iso_now())
                job.update(updates)
                current_status = str(job.get("status") or "").strip().lower()
                if current_status != previous_status and job.get("wecom_to_user"):
                    if current_status == "downloading" and not job.get("wecom_started_notified"):
                        notify_jobs.append(("started", job.copy()))
                    elif current_status == "done" and not job.get("wecom_done_notified"):
                        notify_jobs.append(("done", job.copy()))
                    elif current_status == "failed" and not job.get("wecom_failed_notified"):
                        notify_jobs.append(("failed", job.copy()))
                result = job
                break
        else:
            return None
    for kind, notify_job in notify_jobs:
        trigger_wecom_notification_async(kind, job=notify_job)
    return result


def list_recent_jobs(limit: int = 50):
    with jobs_lock:
        visible_jobs = [job for job in jobs if not is_job_hidden(job)]
        return list(reversed(visible_jobs[-limit:]))


def count_active_jobs() -> int:
    with jobs_lock:
        return sum(1 for job in jobs if not is_job_hidden(job) and job.get("status") == "downloading")


def count_queued_jobs() -> int:
    with jobs_lock:
        return sum(1 for job in jobs if not is_job_hidden(job) and job.get("status") == "queued")


def remove_job(job_id: str):
    with jobs_lock:
        for index, job in enumerate(jobs):
            if job.get("id") == job_id:
                return jobs.pop(index)
    return None


def clear_history_jobs() -> int:
    with jobs_lock:
        keep = [job for job in jobs if not is_job_hidden(job) and job.get("status") in {"queued", "downloading"}]
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


async def run_in_executor(executor: ThreadPoolExecutor, func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    if kwargs:
        return await loop.run_in_executor(executor, lambda: func(*args, **kwargs))
    return await loop.run_in_executor(executor, func, *args)


def safe_requests_get(target: str, referer: str | None = None, user_agent: str | None = None, proxy: str | None = None, timeout: int = 60, stream: bool = False):
    headers = build_headers(referer, user_agent)
    proxies = build_proxies(proxy)
    try:
        return requests.get(target, headers=headers, proxies=proxies, timeout=timeout, stream=stream)
    except requests.exceptions.SSLError:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", InsecureRequestWarning)
            return requests.get(target, headers=headers, proxies=proxies, timeout=timeout, verify=False, stream=stream)


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


def resolve_request_proxy(url: str | None, requested_proxy: str | None = None, cfg: dict | None = None) -> str | None:
    active_cfg = cfg or load_config()
    configured_proxy = requested_proxy or active_cfg.get("default_proxy") or None
    return route_proxy_for_url(url, configured_proxy)


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
    wecom_to_user: str | None = None


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
    wecom_secret: str | None = CONFIG_KEEP_SENTINEL
    wecom_token: str | None = CONFIG_KEEP_SENTINEL
    wecom_encoding_aes_key: str | None = CONFIG_KEEP_SENTINEL
    wecom_callback_url: str | None = ""
    wecom_forward_url: str | None = ""
    wecom_forward_token: str | None = CONFIG_KEEP_SENTINEL


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
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
async def parse_url(payload: ParsePayload):
    cfg = load_config()
    input_url = normalize_input_url(payload.url)
    proxy = resolve_request_proxy(input_url, payload.proxy, cfg)
    if not input_url:
        raise HTTPException(status_code=400, detail="请提供有效链接")
    cookies_path = resolve_site_cookies_path(input_url, cfg)
    info = await run_in_executor(
        parse_executor,
        discover_stream,
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
    with jobs_lock:
        target_job = next((job for job in jobs if job.get("id") == job_id), None)
        if not target_job:
            print(f"[job] skip start: job missing job_id={job_id}")
            return
        if is_job_hidden(target_job) or target_job.get("cancel_requested"):
            print(f"[job] skip start: job deleted/cancelled before run job_id={job_id}")
            return

    active_slot = min(MAX_CONCURRENT_DOWNLOADS, count_active_jobs() + 1)
    updated = update_job(job_id, status="downloading", started_at=iso_now(), progress=8, status_text=f"开始下载 · 当前下载槽位 {active_slot}/{MAX_CONCURRENT_DOWNLOADS}")
    if not updated:
        print(f"[job] skip start: update_job missed job_id={job_id}")
        return

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
    input_url = normalize_input_url(payload.url)
    proxy = resolve_request_proxy(input_url, payload.proxy, cfg)
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
        "deleted": False,
        "deleted_at": None,
        "download_via": download_via,
        "extractor": extractor,
        "request_payload": payload.model_dump(),
        "wecom_to_user": str(payload.wecom_to_user or "").strip(),
        "wecom_started_notified": False,
        "wecom_started_notified_at": None,
        "wecom_started_notifying": False,
        "wecom_done_notified": False,
        "wecom_done_notified_at": None,
        "wecom_done_notifying": False,
        "wecom_failed_notified": False,
        "wecom_failed_notified_at": None,
        "wecom_failed_notifying": False,
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

    new_job = create_download_job(DownloadPayload(**payload, wecom_to_user=source_job.get("wecom_to_user")), retry_of=job_id)
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
async def download(request: Request, payload: DownloadPayload):
    return await run_in_executor(parse_executor, create_download_job, payload)


@app.post("/api/download/all")
async def download_all(request: Request, payload: BatchDownloadPayload):
    cfg = load_config()
    input_url = normalize_input_url(payload.url)
    proxy = resolve_request_proxy(input_url, payload.proxy, cfg)
    if not input_url:
        raise HTTPException(status_code=400, detail="请提供有效链接")
    cookies_path = resolve_site_cookies_path(input_url, cfg)
    info = await run_in_executor(
        parse_executor,
        discover_stream,
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
        job = await run_in_executor(parse_executor, create_download_job, job_payload)
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
        jobs_created.append(await run_in_executor(parse_executor, create_download_job, job_payload))
    return {"ok": True, "title": info.get("title") or base_title, "stream_count": len(streams), "jobs": jobs_created}


@app.get("/api/jobs")
async def list_jobs():
    return list_recent_jobs(50)


@app.post("/api/jobs/{job_id}/delete")
def delete_job(job_id: str):
    with jobs_lock:
        target = next((job for job in jobs if job.get("id") == job_id), None)
        if not target or is_job_hidden(target):
            raise HTTPException(status_code=404, detail="任务不存在")

        target["deleted"] = True
        target["deleted_at"] = iso_now()
        target["cancel_requested"] = True

        if target.get("status") == "downloading":
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
async def preview_m3u8(
    request: Request,
    url: str,
    referer: str | None = None,
    user_agent: str | None = None,
    proxy: str | None = None,
    stream_url: str | None = None,
    stream_index: int | None = None,
):
    cfg = load_config()
    actual_proxy = resolve_request_proxy(url, proxy, cfg)
    if stream_url:
        selected_stream = stream_url
    else:
        cookies_path = resolve_site_cookies_path(url, cfg)
        info = await run_in_executor(
            parse_executor,
            discover_stream,
            url,
            referer,
            user_agent,
            actual_proxy,
            stream_url,
            stream_index,
            cookies_path,
        )
        selected_stream = choose_stream_url(info, stream_url, stream_index)
    if not selected_stream:
        raise HTTPException(status_code=404, detail="未解析到 m3u8 流")

    resp = await run_in_executor(
        media_executor,
        safe_requests_get,
        selected_stream,
        referer,
        user_agent,
        actual_proxy,
        30,
    )
    resp.raise_for_status()
    proxy_prefix = f"{str(request.base_url)}api/media/"
    content = rewrite_m3u8_manifest(resp.text, selected_stream, proxy_prefix, referer, user_agent, actual_proxy)
    return Response(content=content, media_type="application/vnd.apple.mpegurl")


@app.get("/api/media")
@app.get("/api/media/{name:path}")
async def media_proxy(name: str = "", target: str = "", referer: str | None = None, user_agent: str | None = None, proxy: str | None = None):
    cfg = load_config()
    actual_proxy = resolve_request_proxy(target, proxy, cfg)
    response = None
    try:
        response = await run_in_executor(
            media_executor,
            safe_requests_get,
            target,
            referer,
            user_agent,
            actual_proxy,
            60,
            True,
        )
        response.raise_for_status()
    except Exception as exc:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        raise HTTPException(status_code=502, detail=f"媒体分片拉取失败：{exc}")

    headers = {}
    for key in ("content-length", "content-range", "accept-ranges", "cache-control", "etag", "last-modified"):
        value = response.headers.get(key)
        if value:
            headers[key] = value

    def iter_media():
        try:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if chunk:
                    yield chunk
        finally:
            response.close()

    return StreamingResponse(
        iter_media(),
        media_type=response.headers.get("content-type", "application/octet-stream"),
        headers=headers,
        status_code=response.status_code,
    )


@app.get("/api/config")
async def get_config():
    return enrich_config_view(load_config())


@app.get("/api/version")
async def get_version():
    return {"version": APP_VERSION, "commit": APP_COMMIT}


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

    secret = str(payload.wecom_secret or "").strip()
    token = str(payload.wecom_token or "").strip()
    aes_key = str(payload.wecom_encoding_aes_key or "").strip()
    if secret != CONFIG_KEEP_SENTINEL:
        cfg["wecom_secret"] = secret
    if token != CONFIG_KEEP_SENTINEL:
        cfg["wecom_token"] = token
    if aes_key != CONFIG_KEEP_SENTINEL:
        cfg["wecom_encoding_aes_key"] = aes_key

    cfg["wecom_callback_url"] = str(payload.wecom_callback_url or "").strip()
    cfg["wecom_forward_url"] = str(payload.wecom_forward_url or "").strip()

    forward_token = str(payload.wecom_forward_token or "").strip()
    if forward_token != CONFIG_KEEP_SENTINEL:
        cfg["wecom_forward_token"] = forward_token

    save_config(cfg)
    return enrich_config_view(cfg)


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
    return await save_uploaded_cookie_file(file, TWITTER_COOKIES_PATH, "twitter_cookies_path", "twitter_cookies_exists")


@app.post("/api/upload/youtube-cookies")
async def upload_youtube_cookies(file: UploadFile = File(...)):
    return await save_uploaded_cookie_file(file, YOUTUBE_COOKIES_PATH, "youtube_cookies_path", "youtube_cookies_exists")


@app.post("/api/upload/bilibili-cookies")
async def upload_bilibili_cookies(file: UploadFile = File(...)):
    return await save_uploaded_cookie_file(file, BILIBILI_COOKIES_PATH, "bilibili_cookies_path", "bilibili_cookies_exists")
