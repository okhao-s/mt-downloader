import types

import app
import wecom


def reset_jobs():
    with app.jobs_lock:
        app.jobs.clear()


def sample_job(job_id="job1", status="queued"):
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
        "status_text": "排队中" if status == "queued" else "下载完成",
        "progress": 0 if status == "queued" else 100,
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
        "wecom_created_notifying": False,
        "wecom_created_retry_scheduled": False,
        "wecom_started_notified": False,
        "wecom_started_notified_at": None,
        "wecom_started_notifying": False,
        "wecom_started_retry_scheduled": False,
        "wecom_completion_notified": False,
        "wecom_completion_notified_at": None,
        "wecom_completion_notifying": False,
        "wecom_completion_retry_scheduled": False,
    }


def test_created_notify_marks_only_after_success():
    reset_jobs()
    job = sample_job("created-ok", status="queued")
    app.add_job(job)

    calls = []
    app.send_wecom_text = lambda to_user, content: calls.append((to_user, content)) or {"msgid": "1"}

    app.notify_wecom_job_created(job.copy())

    stored = next(j for j in app.jobs if j["id"] == "created-ok")
    assert len(calls) == 1
    assert stored["wecom_created_notified"] is True
    assert stored["wecom_created_notified_at"]


def test_created_notify_failure_does_not_mark_notified():
    reset_jobs()
    job = sample_job("created-fail", status="queued")
    app.add_job(job)

    def boom(to_user, content):
        raise RuntimeError("send down")

    app.send_wecom_text = boom
    app.notify_wecom_job_created(job.copy())

    stored = next(j for j in app.jobs if j["id"] == "created-fail")
    assert stored["wecom_created_notified"] is False
    assert stored["wecom_created_notified_at"] is None


def test_started_and_completion_notifications_are_disabled():
    reset_jobs()
    sent = []
    app.send_wecom_text = lambda to_user, content: sent.append(content) or {"msgid": str(len(sent))}

    started_job = sample_job("started-off", status="downloading")
    started_job["wecom_created_notified"] = True
    started_job["wecom_created_notified_at"] = app.iso_now()
    app.add_job(started_job)
    app.notify_wecom_job_started(started_job.copy())

    done_job = sample_job("done-off", status="done")
    done_job["wecom_created_notified"] = True
    done_job["wecom_created_notified_at"] = app.iso_now()
    app.add_job(done_job)
    app.notify_wecom_job_completion(done_job.copy())

    started_stored = next(j for j in app.jobs if j["id"] == "started-off")
    done_stored = next(j for j in app.jobs if j["id"] == "done-off")
    assert sent == []
    assert started_stored["wecom_started_notified"] is False
    assert done_stored["wecom_completion_notified"] is False


def test_update_job_does_not_trigger_started_or_completion_notifications():
    reset_jobs()
    sent = []
    app.send_wecom_text = lambda to_user, content: sent.append(content) or {"msgid": str(len(sent))}

    job = sample_job("status-flow-off", status="queued")
    job["wecom_created_notified"] = True
    job["wecom_created_notified_at"] = app.iso_now()
    app.add_job(job)

    app.update_job("status-flow-off", status="downloading", progress=8, status_text="开始下载", started_at=app.iso_now())
    app.update_job("status-flow-off", status="done", progress=100, status_text="下载完成", finished_at=app.iso_now())

    stored = next(j for j in app.jobs if j["id"] == "status-flow-off")
    assert sent == []
    assert stored["wecom_started_notified"] is False
    assert stored["wecom_completion_notified"] is False


def test_schedule_wecom_notification_retry_only_accepts_created():
    reset_jobs()
    job = sample_job("retry-dedupe", status="done")
    app.add_job(job)

    calls = []
    old_thread = app.threading.Thread

    class FakeThread:
        def __init__(self, target=None, name=None, daemon=None):
            self.target = target
            self.name = name
            self.daemon = daemon

        def start(self):
            calls.append(self.name)

    try:
        app.threading.Thread = FakeThread
        app.schedule_wecom_notification_retry("retry-dedupe", "completion", delays=(0.01,))
        app.schedule_wecom_notification_retry("retry-dedupe", "started", delays=(0.01,))
        app.schedule_wecom_notification_retry("retry-dedupe", "created", delays=(0.01,))
        stored = next(j for j in app.jobs if j["id"] == "retry-dedupe")
        assert calls == ["wecom-retry-created-retry-"]
        assert stored["wecom_created_retry_scheduled"] is True
        assert stored["wecom_started_retry_scheduled"] is False
        assert stored["wecom_completion_retry_scheduled"] is False
    finally:
        app.threading.Thread = old_thread



def test_create_download_job_does_not_premark_created_notified():
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

    try:
        app.discover_stream = lambda *args, **kwargs: {"title": "标题", "streams": [{"url": "https://example.com/video.mp4"}], "extractor": "test"}
        app.choose_stream_url = lambda info, stream_url, stream_index: "https://example.com/video.mp4"
        app.route_proxy_for_url = lambda *args, **kwargs: ""
        app.resolve_request_proxy = lambda *args, **kwargs: ""
        app.resolve_site_cookies_path = lambda *args, **kwargs: None
        app.load_config = lambda: {}
        app.download_executor = DummyExecutor()
        app.trigger_wecom_notification_async = lambda *args, **kwargs: None

        payload = app.DownloadPayload(url="https://example.com/post/1", wecom_to_user="zhangsan")
        job = app.create_download_job(payload)

        assert job["wecom_created_notified"] is False
        assert job["wecom_created_notified_at"] is None
        assert job["wecom_created_retry_scheduled"] is False
    finally:
        app.discover_stream = old_discover_stream
        app.choose_stream_url = old_choose_stream_url
        app.route_proxy_for_url = old_route_proxy_for_url
        app.resolve_request_proxy = old_resolve_request_proxy
        app.resolve_site_cookies_path = old_resolve_site_cookies_path
        app.load_config = old_load_config
        app.download_executor = old_download_executor
        app.trigger_wecom_notification_async = old_trigger


def test_create_download_job_triggers_created_notification_async():
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

        assert calls == [("created", job["id"], None)]
    finally:
        app.discover_stream = old_discover_stream
        app.choose_stream_url = old_choose_stream_url
        app.route_proxy_for_url = old_route_proxy_for_url
        app.resolve_request_proxy = old_resolve_request_proxy
        app.resolve_site_cookies_path = old_resolve_site_cookies_path
        app.load_config = old_load_config
        app.download_executor = old_download_executor
        app.trigger_wecom_notification_async = old_trigger


def test_created_retry_backfills_after_job_left_done():
    reset_jobs()
    sent = []
    old_delays = app.WECOM_NOTIFY_RETRY_DELAYS

    def fake_send(to_user, content):
        sent.append(content)
        return {"msgid": str(len(sent))}

    try:
        app.WECOM_NOTIFY_RETRY_DELAYS = (0.01,)
        app.send_wecom_text = fake_send
        job = sample_job("created-backfill-done", status="done")
        app.add_job(job)

        app.schedule_wecom_notification_retry(job["id"], "created")
        app.time.sleep(0.05)

        stored = next(j for j in app.jobs if j["id"] == "created-backfill-done")
        assert len(sent) == 1
        assert "收到任务" in sent[0]
        assert stored["wecom_created_notified"] is True
        assert stored["wecom_created_retry_scheduled"] is False
    finally:
        app.WECOM_NOTIFY_RETRY_DELAYS = old_delays


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
        test_created_notify_marks_only_after_success,
        test_created_notify_failure_does_not_mark_notified,
        test_started_and_completion_notifications_are_disabled,
        test_update_job_does_not_trigger_started_or_completion_notifications,
        test_schedule_wecom_notification_retry_only_accepts_created,
        test_create_download_job_does_not_premark_created_notified,
        test_create_download_job_triggers_created_notification_async,
        test_created_retry_backfills_after_job_left_done,
        test_wecom_client_error_details_for_invalid_touser,
    ]
    for test in tests:
        reset_jobs()
        test()
        print(f"PASS: {test.__name__}")
    print("ALL PASS")
