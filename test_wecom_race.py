import app

sent = []
app.send_wecom_text_async = lambda to_user, content: sent.append((to_user, content))

# reset in-memory jobs for isolated test
with app.jobs_lock:
    app.jobs.clear()

job = {
    "id": "racejob1",
    "source_url": "https://example.com/v.mp4",
    "stream_url": "https://example.com/v.mp4",
    "stream_index": 0,
    "output": "short.mp4",
    "download_dir": "/tmp",
    "created_at": app.iso_now(),
    "updated_at": app.iso_now(),
    "started_at": None,
    "finished_at": None,
    "proxy": "",
    "status": "queued",
    "status_text": "排队中",
    "progress": 0,
    "title": "超短视频",
    "platform": "douyin",
    "error": "",
    "retry_count": 0,
    "retry_of": "",
    "retry_scheduled": False,
    "download_via": "direct",
    "extractor": "test",
    "request_payload": {},
    "wecom_to_user": "zhangsan",
    "wecom_created_notified": False,
    "wecom_created_notified_at": None,
    "wecom_completion_notified": False,
    "wecom_completion_notified_at": None,
}

app.add_job(job)
# simulate real create point: immediately attach context + fire creation on visible queued job
app.notify_wecom_job_created(job.copy())
# simulate ultra-fast terminal transition right after create
app.update_job("racejob1", status="done", progress=100, status_text="下载完成", finished_at=app.iso_now())

print({
    "messages": sent,
    "job": next((j for j in app.jobs if j.get("id") == "racejob1"), None),
})
