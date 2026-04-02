import app


def reset_jobs():
    with app.jobs_lock:
        app.jobs.clear()


def sample_job(job_id="job-delete", status="queued"):
    now = app.iso_now()
    return {
        "id": job_id,
        "source_url": "https://example.com/v.mp4",
        "stream_url": "https://example.com/v.mp4",
        "stream_index": 0,
        "output": "short.mp4",
        "download_dir": "/tmp",
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "finished_at": None,
        "proxy": "",
        "status": status,
        "status_text": "排队中",
        "progress": 0,
        "title": "超短视频",
        "platform": "douyin",
        "error": "",
        "retry_count": 0,
        "retry_of": "",
        "retry_scheduled": False,
        "deleted": False,
        "deleted_at": None,
        "download_via": "direct",
        "extractor": "test",
        "request_payload": {},
        "wecom_to_user": "",
        "wecom_started_notified": False,
        "wecom_started_notified_at": None,
        "wecom_done_notified": False,
        "wecom_done_notified_at": None,
        "wecom_failed_notified": False,
        "wecom_failed_notified_at": None,
    }


def test_deleted_queued_job_will_not_start():
    reset_jobs()
    job = sample_job()
    app.add_job(job)
    with app.jobs_lock:
        app.jobs[0]["deleted"] = True
        app.jobs[0]["cancel_requested"] = True

    app.run_download_job(
        job_id=job["id"],
        preview_url="https://example.com/preview",
        output_path=app.Path("/tmp/should-not-create.mp4"),
        aggressive=False,
        stream_url="https://example.com/v.mp4",
        download_via="direct",
        source_url=job["source_url"],
    )

    stored = next(j for j in app.jobs if j["id"] == job["id"])
    assert stored["status"] == "queued"
    assert stored["started_at"] is None


def test_list_recent_jobs_hides_deleted_items():
    reset_jobs()
    visible = sample_job("visible-job")
    hidden = sample_job("hidden-job")
    hidden["deleted"] = True
    hidden["deleted_at"] = app.iso_now()
    app.add_job(visible)
    app.add_job(hidden)

    recent = app.list_recent_jobs(10)
    ids = [item["id"] for item in recent]
    assert "visible-job" in ids
    assert "hidden-job" not in ids


if __name__ == "__main__":
    tests = [
        test_deleted_queued_job_will_not_start,
        test_list_recent_jobs_hides_deleted_items,
    ]
    for test in tests:
        reset_jobs()
        test()
        print(f"PASS: {test.__name__}")
    print("ALL PASS")
