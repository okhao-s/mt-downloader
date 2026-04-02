import types

import app
import wecom


def reset_jobs():
    with app.jobs_lock:
        app.jobs.clear()


def sample_job(job_id="job1", status="queued"):
    now = app.iso_now()
    status_text = {
        "queued": "排队中",
        "downloading": "开始下载",
        "done": "下载完成",
        "failed": "下载失败",
    }.get(status, status)
    progress = {"queued": 0, "downloading": 8, "done": 100, "failed": 100}.get(status, 0)
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
        "status_text": status_text,
        "progress": progress,
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


def test_started_notify_marks_only_after_success():
    reset_jobs()
    job = sample_job("started-ok", status="downloading")
    app.add_job(job)

    calls = []
    app.send_wecom_text = lambda to_user, content: calls.append((to_user, content)) or {"msgid": "1"}

    app.notify_wecom_job_started(job.copy())

    stored = next(j for j in app.jobs if j["id"] == "started-ok")
    assert len(calls) == 1
    assert "开始下载" in calls[0][1]
    assert stored["wecom_started_notified"] is True
    assert stored["wecom_started_notified_at"]


def test_done_notify_marks_only_after_success():
    reset_jobs()
    job = sample_job("done-ok", status="done")
    app.add_job(job)

    calls = []
    app.send_wecom_text = lambda to_user, content: calls.append((to_user, content)) or {"msgid": "1"}

    app.notify_wecom_job_done(job.copy())

    stored = next(j for j in app.jobs if j["id"] == "done-ok")
    assert len(calls) == 1
    assert "下载完成" in calls[0][1]
    assert stored["wecom_done_notified"] is True
    assert stored["wecom_done_notified_at"]


def test_failed_notify_marks_only_after_success():
    reset_jobs()
    job = sample_job("failed-ok", status="failed")
    job["error"] = "磁盘已满"
    app.add_job(job)

    calls = []
    app.send_wecom_text = lambda to_user, content: calls.append((to_user, content)) or {"msgid": "1"}

    app.notify_wecom_job_failed(job.copy())

    stored = next(j for j in app.jobs if j["id"] == "failed-ok")
    assert len(calls) == 1
    assert "下载失败" in calls[0][1]
    assert "磁盘已满" in calls[0][1]
    assert stored["wecom_failed_notified"] is True
    assert stored["wecom_failed_notified_at"]


def test_started_notify_failure_does_not_mark_notified():
    reset_jobs()
    job = sample_job("started-fail", status="downloading")
    app.add_job(job)

    def boom(to_user, content):
        raise RuntimeError("send down")

    app.send_wecom_text = boom
    app.notify_wecom_job_started(job.copy())

    stored = next(j for j in app.jobs if j["id"] == "started-fail")
    assert stored["wecom_started_notified"] is False
    assert stored["wecom_started_notified_at"] is None


def test_update_job_triggers_started_done_failed_once_each():
    reset_jobs()
    sent = []
    app.send_wecom_text = lambda to_user, content: sent.append(content) or {"msgid": str(len(sent))}

    job = sample_job("status-flow", status="queued")
    app.add_job(job)

    app.update_job("status-flow", status="downloading", progress=8, status_text="开始下载", started_at=app.iso_now())
    app.update_job("status-flow", status="downloading", progress=20, status_text="已下载 20%")
    app.update_job("status-flow", status="done", progress=100, status_text="下载完成", finished_at=app.iso_now())
    app.update_job("status-flow", status="done", progress=100, status_text="下载完成", finished_at=app.iso_now())

    stored = next(j for j in app.jobs if j["id"] == "status-flow")
    assert len(sent) == 2
    assert "开始下载" in sent[0]
    assert "下载完成" in sent[1]
    assert stored["wecom_started_notified"] is True
    assert stored["wecom_done_notified"] is True
    assert stored["wecom_failed_notified"] is False


def test_update_job_triggers_failed_notification_once():
    reset_jobs()
    sent = []
    app.send_wecom_text = lambda to_user, content: sent.append(content) or {"msgid": str(len(sent))}

    job = sample_job("status-fail", status="queued")
    app.add_job(job)

    app.update_job("status-fail", status="failed", progress=100, status_text="下载失败", error="源站超时", finished_at=app.iso_now())
    app.update_job("status-fail", status="failed", progress=100, status_text="下载失败", error="源站超时", finished_at=app.iso_now())

    stored = next(j for j in app.jobs if j["id"] == "status-fail")
    assert len(sent) == 1
    assert "下载失败" in sent[0]
    assert "源站超时" in sent[0]
    assert stored["wecom_failed_notified"] is True


def test_create_download_job_has_only_started_done_failed_flags_and_no_created_trigger():
    reset_jobs()

    old_discover_stream = app.discover_stream
    old_choose_stream_url = app.choose_stream_url
    old_route_proxy_for_url = app.route_proxy_for_url
    old_resolve_request_proxy = app.resolve_request_proxy
    old_resolve_site_cookies_path = app.resolve_site_cookies_path
    old_load_config = app.load_config
    old_download_executor = app.download_executor
    old_trigger = app.trigger_wecom_notification_async

    class DummyExecutor:
        def submit(self, *args, **kwargs):
            return None

    calls = []

    try:
        app.discover_stream = lambda *args, **kwargs: {"title": "标题", "streams": [{"url": "https://example.com/video.mp4"}], "extractor": "test"}
        app.choose_stream_url = lambda info, stream_url, stream_index: "https://example.com/video.mp4"
        app.route_proxy_for_url = lambda *args, **kwargs: ""
        app.resolve_request_proxy = lambda *args, **kwargs: ""
        app.resolve_site_cookies_path = lambda *args, **kwargs: None
        app.load_config = lambda: {}
        app.download_executor = DummyExecutor()
        app.trigger_wecom_notification_async = lambda kind, job=None, job_id=None: calls.append((kind, (job or {}).get("id"), job_id))

        payload = app.DownloadPayload(url="https://example.com/post/2", wecom_to_user="zhangsan")
        job = app.create_download_job(payload)

        assert "wecom_created_notified" not in job
        assert job["wecom_started_notified"] is False
        assert job["wecom_done_notified"] is False
        assert job["wecom_failed_notified"] is False
        assert calls == []
    finally:
        app.discover_stream = old_discover_stream
        app.choose_stream_url = old_choose_stream_url
        app.route_proxy_for_url = old_route_proxy_for_url
        app.resolve_request_proxy = old_resolve_request_proxy
        app.resolve_site_cookies_path = old_resolve_site_cookies_path
        app.load_config = old_load_config
        app.download_executor = old_download_executor
        app.trigger_wecom_notification_async = old_trigger


def test_wecom_client_error_details_for_invalid_touser():
    client = wecom.WeComClient("ww123456", 1000002, "secret")
    client.get_access_token = types.MethodType(lambda self, force_refresh=False: "token123", client)

    class FakeResp:
        text = '{"errcode":60111,"errmsg":"user not found"}'

        def raise_for_status(self):
            return None

        def json(self):
            return {"errcode": 60111, "errmsg": "user not found"}

    old_post = wecom.requests.post
    wecom.requests.post = lambda *args, **kwargs: FakeResp()
    try:
        try:
            client.send_text("zhangsan", "hello")
            raise AssertionError("expected send_text to fail")
        except RuntimeError as exc:
            text = str(exc)
            assert "touser=zha***san" in text
            assert "errcode=60111" in text
            assert "请检查 touser" in text
    finally:
        wecom.requests.post = old_post


if __name__ == "__main__":
    tests = [
        test_started_notify_marks_only_after_success,
        test_done_notify_marks_only_after_success,
        test_failed_notify_marks_only_after_success,
        test_started_notify_failure_does_not_mark_notified,
        test_update_job_triggers_started_done_failed_once_each,
        test_update_job_triggers_failed_notification_once,
        test_create_download_job_has_only_started_done_failed_flags_and_no_created_trigger,
        test_wecom_client_error_details_for_invalid_touser,
    ]
    for test in tests:
        reset_jobs()
        test()
        print(f"PASS: {test.__name__}")
    print("ALL PASS")
