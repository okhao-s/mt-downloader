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


def test_completion_notify_failure_does_not_mark_notified():
    reset_jobs()
    job = sample_job("done-fail", status="done")
    app.add_job(job)

    def boom(to_user, content):
        raise RuntimeError("send down")

    app.send_wecom_text = boom
    app.notify_wecom_job_completion(job.copy())

    stored = next(j for j in app.jobs if j["id"] == "done-fail")
    assert stored["wecom_completion_notified"] is False
    assert stored["wecom_completion_notified_at"] is None


def test_started_notify_marks_only_after_success():
    reset_jobs()
    job = sample_job("started-ok", status="downloading")
    job["status_text"] = "开始下载 · 当前下载槽位 1/2"
    job["wecom_created_notified"] = True
    job["wecom_created_notified_at"] = app.iso_now()
    app.add_job(job)

    sent = []
    app.send_wecom_text = lambda to_user, content: sent.append(content) or {"msgid": str(len(sent))}

    app.notify_wecom_job_started(job.copy())

    stored = next(j for j in app.jobs if j["id"] == "started-ok")
    assert len(sent) == 1
    assert "开始下载" in sent[0]
    assert stored["wecom_created_notified"] is True
    assert stored["wecom_started_notified"] is True
    assert stored["wecom_started_notified_at"]


def test_race_fast_done_sends_started_completion_once():
    reset_jobs()
    sent = []
    app.send_wecom_text = lambda to_user, content: sent.append((to_user, content)) or {"msgid": str(len(sent))}

    job = sample_job("racejob1", status="queued")
    job["wecom_created_notified"] = True
    job["wecom_created_notified_at"] = app.iso_now()
    app.add_job(job)
    app.update_job("racejob1", status="downloading", progress=8, status_text="开始下载", started_at=app.iso_now())
    app.update_job("racejob1", status="done", progress=100, status_text="下载完成", finished_at=app.iso_now())

    stored = next(j for j in app.jobs if j["id"] == "racejob1")
    assert len(sent) == 2
    assert "开始下载" in sent[0][1]
    assert "下载完成" in sent[1][1]
    assert stored["wecom_created_notified"] is True
    assert stored["wecom_started_notified"] is True
    assert stored["wecom_completion_notified"] is True


def test_fast_completion_waits_for_started_notification_order():
    reset_jobs()
    sent = []
    release_started = {"ok": False}

    def fake_send(to_user, content):
        is_started = "开始下载" in content
        if is_started:
            while not release_started["ok"]:
                app.time.sleep(0.01)
        sent.append(content)
        return {"msgid": str(len(sent))}

    app.send_wecom_text = fake_send
    job = sample_job("race-order", status="queued")
    job["wecom_created_notified"] = True
    job["wecom_created_notified_at"] = app.iso_now()
    app.add_job(job)

    starter = app.threading.Thread(
        target=lambda: app.update_job("race-order", status="downloading", progress=8, status_text="开始下载", started_at=app.iso_now()),
        daemon=True,
    )
    starter.start()
    app.time.sleep(0.05)
    releaser = app.threading.Thread(target=lambda: (app.time.sleep(0.2), release_started.__setitem__("ok", True)), daemon=True)
    releaser.start()
    app.update_job("race-order", status="done", progress=100, status_text="下载完成", finished_at=app.iso_now())
    starter.join(timeout=2)
    releaser.join(timeout=2)

    stored = next(j for j in app.jobs if j["id"] == "race-order")
    assert len(sent) == 2
    assert "开始下载" in sent[0]
    assert "下载完成" in sent[1]
    assert stored["wecom_created_notified"] is True
    assert stored["wecom_started_notified"] is True
    assert stored["wecom_completion_notified"] is True
    assert stored["wecom_created_notifying"] is False
    assert stored["wecom_started_notifying"] is False
    assert stored["wecom_completion_notifying"] is False


def test_completion_will_backfill_started_for_done_if_needed():
    reset_jobs()
    sent = []
    app.send_wecom_text = lambda to_user, content: sent.append(content) or {"msgid": str(len(sent))}

    job = sample_job("race-start-backfill", status="done")
    job["wecom_created_notified"] = True
    job["wecom_created_notified_at"] = app.iso_now()
    app.add_job(job)
    app.notify_wecom_job_completion(job.copy())

    stored = next(j for j in app.jobs if j["id"] == "race-start-backfill")
    assert len(sent) == 2
    assert "开始下载" in sent[0]
    assert "下载完成" in sent[1]
    assert stored["wecom_started_notified"] is True
    assert stored["wecom_completion_notified"] is True


def test_failed_and_cancelled_do_not_send_completion_notifications():
    reset_jobs()
    sent = []
    app.send_wecom_text = lambda to_user, content: sent.append(content) or {"msgid": str(len(sent))}

    failed_job = sample_job("failed-job", status="failed")
    failed_job["wecom_created_notified"] = True
    failed_job["wecom_created_notified_at"] = app.iso_now()
    app.add_job(failed_job)
    app.notify_wecom_job_completion(failed_job.copy())

    cancelled_job = sample_job("cancelled-job", status="cancelled")
    cancelled_job["wecom_created_notified"] = True
    cancelled_job["wecom_created_notified_at"] = app.iso_now()
    app.add_job(cancelled_job)
    app.notify_wecom_job_completion(cancelled_job.copy())

    failed_stored = next(j for j in app.jobs if j["id"] == "failed-job")
    cancelled_stored = next(j for j in app.jobs if j["id"] == "cancelled-job")
    assert sent == []
    assert failed_stored["wecom_completion_notified"] is False
    assert cancelled_stored["wecom_completion_notified"] is False


def test_completion_retry_recovers_slow_done_after_transient_send_failure():
    reset_jobs()
    sent = []
    attempts = {"completion": 0}
    old_delays = app.WECOM_NOTIFY_RETRY_DELAYS
    app.WECOM_NOTIFY_RETRY_DELAYS = (0.01, 0.02)

    def fake_send(to_user, content):
        if "下载完成" in content:
            attempts["completion"] += 1
            if attempts["completion"] == 1:
                raise RuntimeError("transient completion failure")
        sent.append(content)
        return {"msgid": str(len(sent))}

    try:
        app.send_wecom_text = fake_send
        job = sample_job("slow-done-retry", status="done")
        job["wecom_created_notified"] = True
        job["wecom_created_notified_at"] = app.iso_now()
        job["wecom_started_notified"] = True
        job["wecom_started_notified_at"] = app.iso_now()
        app.add_job(job)

        app.notify_wecom_job_completion(job.copy())
        app.time.sleep(0.08)

        stored = next(j for j in app.jobs if j["id"] == "slow-done-retry")
        assert attempts["completion"] >= 2
        assert len([item for item in sent if "下载完成" in item]) == 1
        assert stored["wecom_completion_notified"] is True
        assert stored["wecom_completion_notifying"] is False
    finally:
        app.WECOM_NOTIFY_RETRY_DELAYS = old_delays


def test_fast_done_retry_keeps_three_notifications_only_once_each():
    reset_jobs()
    sent = []
    started_attempts = {"count": 0}
    old_delays = app.WECOM_NOTIFY_RETRY_DELAYS
    app.WECOM_NOTIFY_RETRY_DELAYS = (0.01, 0.02)

    def fake_send(to_user, content):
        if "开始下载" in content:
            started_attempts["count"] += 1
            if started_attempts["count"] == 1:
                raise RuntimeError("transient started failure")
        sent.append(content)
        return {"msgid": str(len(sent))}

    try:
        app.send_wecom_text = fake_send
        job = sample_job("fast-done-retry", status="queued")
        job["wecom_created_notified"] = True
        job["wecom_created_notified_at"] = app.iso_now()
        app.add_job(job)

        app.update_job("fast-done-retry", status="done", progress=100, status_text="下载完成", finished_at=app.iso_now())
        app.time.sleep(0.08)

        stored = next(j for j in app.jobs if j["id"] == "fast-done-retry")
        assert started_attempts["count"] >= 2
        assert len([item for item in sent if "开始下载" in item]) == 1
        assert len([item for item in sent if "下载完成" in item]) == 1
        assert "开始下载" in sent[0]
        assert "下载完成" in sent[1]
        assert stored["wecom_started_notified"] is True
        assert stored["wecom_completion_notified"] is True
        assert stored["wecom_started_notifying"] is False
        assert stored["wecom_completion_notifying"] is False
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


def test_schedule_wecom_notification_retry_deduplicates_same_job_and_kind():
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
        app.schedule_wecom_notification_retry("retry-dedupe", "completion", delays=(0.01,))
        stored = next(j for j in app.jobs if j["id"] == "retry-dedupe")
        assert calls == ["wecom-retry-completion-retry-"]
        assert stored["wecom_completion_retry_scheduled"] is True
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


if __name__ == "__main__":
    tests = [
        test_created_notify_marks_only_after_success,
        test_created_notify_failure_does_not_mark_notified,
        test_completion_notify_failure_does_not_mark_notified,
        test_started_notify_marks_only_after_success,
        test_race_fast_done_sends_started_completion_once,
        test_fast_completion_waits_for_started_notification_order,
        test_completion_will_backfill_started_for_done_if_needed,
        test_failed_and_cancelled_do_not_send_completion_notifications,
        test_completion_retry_recovers_slow_done_after_transient_send_failure,
        test_fast_done_retry_keeps_three_notifications_only_once_each,
        test_schedule_wecom_notification_retry_deduplicates_same_job_and_kind,
        test_create_download_job_does_not_premark_created_notified,
        test_wecom_client_error_details_for_invalid_touser,
    ]
    for test in tests:
        reset_jobs()
        test()
        print(f"PASS: {test.__name__}")
    print("ALL PASS")
