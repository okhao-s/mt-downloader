import os
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from urllib3.exceptions import InsecureRequestWarning

from core import (
    aggressive_hls_download,
    build_headers,
    build_proxies,
    choose_stream_url,
    discover_stream,
    ffmpeg_download,
    load_config,
    normalize_filename,
    rewrite_m3u8_manifest,
    save_config,
)

app = FastAPI(title="M3U8 Downloader")
BASE_DIR = Path(__file__).parent
DOWNLOAD_DIR = Path("/downloads")
DATA_DIR = Path("/app/data")
INTERNAL_BASE_URL = os.getenv("INTERNAL_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

jobs: list[dict] = []
jobs_lock = threading.Lock()
MAX_CONCURRENT_DOWNLOADS = 3
download_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_DOWNLOADS, thread_name_prefix="mt-download")


def iso_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def add_job(job: dict):
    with jobs_lock:
        jobs.append(job)


def update_job(job_id: str, **updates):
    with jobs_lock:
        for job in jobs:
            if job.get("id") == job_id:
                job.update(updates)
                return job
    return None


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


def safe_requests_get(target: str, referer: str | None = None, user_agent: str | None = None, proxy: str | None = None, timeout: int = 60):
    headers = build_headers(referer, user_agent)
    proxies = build_proxies(proxy)
    try:
        return requests.get(target, headers=headers, proxies=proxies, timeout=timeout)
    except requests.exceptions.SSLError:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", InsecureRequestWarning)
            return requests.get(target, headers=headers, proxies=proxies, timeout=timeout, verify=False)


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


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    cfg = load_config()
    recent = list_recent_jobs(20)
    return templates.TemplateResponse(request, "index.html", {"config": cfg, "jobs": recent})


@app.post("/api/parse")
def parse_url(payload: ParsePayload):
    proxy = payload.proxy or load_config().get("default_proxy") or None
    info = discover_stream(
        payload.url,
        payload.referer,
        payload.user_agent,
        proxy,
        payload.stream_url,
        payload.stream_index,
    )
    if not info.get("resolved_url"):
        detail = "未解析到 m3u8 流"
        if info.get("errors"):
            detail = "解析失败：\n" + "\n".join(info["errors"][-2:])
        raise HTTPException(status_code=404, detail=detail)
    info["preview_url"] = f"/api/preview.m3u8?url={payload.url}"
    info["stream_count"] = len(info.get("streams") or [])
    return info


def run_download_job(
    job_id: str,
    preview_url: str,
    output_path: Path,
    aggressive: bool = True,
    stream_url: str | None = None,
    referer: str | None = None,
    user_agent: str | None = None,
    proxy: str | None = None,
):
    active_slot = min(MAX_CONCURRENT_DOWNLOADS, count_active_jobs() + 1)
    update_job(job_id, status="downloading", started_at=iso_now(), progress=8, status_text=f"正在拉取视频流… 自动模式 · 并发槽 {active_slot}/{MAX_CONCURRENT_DOWNLOADS}")

    def on_progress(progress: int, status_text: str):
        update_job(job_id, status="downloading", progress=progress, status_text=status_text)

    def should_cancel() -> bool:
        with jobs_lock:
            for job in jobs:
                if job.get("id") == job_id:
                    return bool(job.get("cancel_requested"))
        return False

    try:
        if aggressive and stream_url:
            try:
                aggressive_hls_download(stream_url, output_path, referer=referer, user_agent=user_agent, proxy=proxy, progress_callback=on_progress, should_cancel=should_cancel)
            except Exception as exc:
                if should_cancel():
                    raise RuntimeError("下载已取消")
                update_job(job_id, status="downloading", progress=6, status_text=f"自动回退 ffmpeg 直连流… {exc}")
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
    if payload.stream_url:
        info = {
            "source_url": payload.url,
            "resolved_url": payload.stream_url,
            "title": None,
            "thumbnail": None,
            "is_m3u8": True,
            "extractor": "selected-stream",
            "streams": [payload.stream_url],
            "stream_options": [],
        }
    else:
        info = discover_stream(
            payload.url,
            payload.referer,
            payload.user_agent,
            proxy,
            payload.stream_url,
            payload.stream_index,
        )
    download_dir = get_download_dir()
    stream_url = payload.stream_url or choose_stream_url(info, payload.stream_url, payload.stream_index)
    if not stream_url:
        raise HTTPException(status_code=404, detail="未解析到 m3u8 流")

    suggested_name = payload.output or info.get("title") or f"video-{uuid4().hex[:8]}"
    output_name = allocate_output_name(suggested_name, download_dir=download_dir)
    output_path = download_dir / output_name

    preview_params = {"url": payload.url, "stream_url": stream_url}
    if payload.referer:
        preview_params["referer"] = payload.referer
    if payload.user_agent:
        preview_params["user_agent"] = payload.user_agent
    if proxy:
        preview_params["proxy"] = proxy
    if payload.stream_index is not None:
        preview_params["stream_index"] = str(payload.stream_index)

    preview_url = f"{INTERNAL_BASE_URL}/api/preview.m3u8"
    resp = requests.Request("GET", preview_url, params=preview_params).prepare()

    retry_count = 0
    if retry_of:
        with jobs_lock:
            source_job = next((item for item in jobs if item.get("id") == retry_of), None)
            retry_count = int(source_job.get("retry_count") or 0) + 1 if source_job else 1

    queued_ahead = count_queued_jobs()
    active_now = count_active_jobs()
    job = {
        "id": uuid4().hex[:10],
        "source_url": payload.url,
        "stream_url": stream_url,
        "stream_index": payload.stream_index,
        "output": output_name,
        "download_dir": str(download_dir),
        "created_at": iso_now(),
        "started_at": None,
        "finished_at": None,
        "proxy": proxy or "",
        "status": "queued",
        "status_text": f"任务已创建，排队中… 当前并行 {active_now}/{MAX_CONCURRENT_DOWNLOADS}" + (f"，前面还有 {queued_ahead} 个" if queued_ahead else ""),
        "progress": 0,
        "title": info.get("title"),
        "error": "",
        "retry_count": retry_count,
        "retry_of": retry_of or "",
        "retry_scheduled": False,
        "request_payload": payload.model_dump(),
    }
    add_job(job)

    download_executor.submit(
        run_download_job,
        job["id"],
        resp.url,
        output_path,
        True,
        stream_url,
        payload.referer,
        payload.user_agent,
        proxy,
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
    info = discover_stream(
        payload.url,
        payload.referer,
        payload.user_agent,
        proxy,
        payload.stream_url,
        payload.stream_index,
    )
    streams = info.get("streams") or []
    if not streams:
        raise HTTPException(status_code=404, detail="未解析到可下载视频")

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
        info = discover_stream(url, referer, user_agent, actual_proxy, stream_url, stream_index)
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
    return load_config()


@app.post("/api/config")
def set_config(payload: ConfigPayload):
    cfg = load_config()
    cfg["default_proxy"] = payload.default_proxy or ""
    cfg["auto_retry_enabled"] = bool(payload.auto_retry_enabled)
    cfg["auto_retry_delay_seconds"] = max(1, int(payload.auto_retry_delay_seconds or 30))
    cfg["auto_retry_max_attempts"] = max(0, int(payload.auto_retry_max_attempts or 0))
    save_config(cfg)
    return cfg
