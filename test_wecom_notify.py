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
        "wecom_started_notified": False,
        "wecom_started_notified_at": None,
        "wecom_started_notifying": False,
        "wecom_completion_notified": False,
        "wecom_completion_notified_at": None,
        "wecom_completion_notifying": False,
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
    app.add_job(job)

    sent = []
    app.send_wecom_text = lambda to_user, content: sent.append(content) or {"msgid": str(len(sent))}

    app.notify_wecom_job_started(job.copy())

    stored = next(j for j in app.jobs if j["id"] == "started-ok")
    assert len(sent) == 2
    assert "任务已创建" in sent[0]
    assert "开始下载" in sent[1]
    assert stored["wecom_created_notified"] is True
    assert stored["wecom_started_notified"] is True
    assert stored["wecom_started_notified_at"]


def test_race_fast_done_sends_created_started_completion_once():
    reset_jobs()
    sent = []
    app.send_wecom_text = lambda to_user, content: sent.append((to_user, content)) or {"msgid": str(len(sent))}

    job = sample_job("racejob1", status="queued")
    app.add_job(job)
    app.notify_wecom_job_created(job.copy())
    app.update_job("racejob1", status="downloading", progress=8, status_text="开始下载", started_at=app.iso_now())
    app.update_job("racejob1", status="done", progress=100, status_text="下载完成", finished_at=app.iso_now())

    stored = next(j for j in app.jobs if j["id"] == "racejob1")
    assert len(sent) == 3
    assert "任务已创建" in sent[0][1]
    assert "开始下载" in sent[1][1]
    assert "下载完成" in sent[2][1]
    assert stored["wecom_created_notified"] is True
    assert stored["wecom_started_notified"] is True
    assert stored["wecom_completion_notified"] is True


def test_fast_completion_waits_for_created_notification_order():
    reset_jobs()
    sent = []
    release_created = {"ok": False}

    def fake_send(to_user, content):
        is_created = "任务已创建" in content
        if is_created:
            while not release_created["ok"]:
                app.time.sleep(0.01)
        sent.append(content)
        return {"msgid": str(len(sent))}

    app.send_wecom_text = fake_send
    job = sample_job("race-order", status="queued")
    app.add_job(job)

    creator = app.threading.Thread(target=app.notify_wecom_job_created, args=(job.copy(),), daemon=True)
    creator.start()
    app.time.sleep(0.05)
    releaser = app.threading.Thread(target=lambda: (app.time.sleep(0.2), release_created.__setitem__("ok", True)), daemon=True)
    releaser.start()
    app.update_job("race-order", status="downloading", progress=8, status_text="开始下载", started_at=app.iso_now())
    app.update_job("race-order", status="done", progress=100, status_text="下载完成", finished_at=app.iso_now())
    creator.join(timeout=2)
    releaser.join(timeout=2)

    stored = next(j for j in app.jobs if j["id"] == "race-order")
    assert len(sent) == 3
    assert "任务已创建" in sent[0]
    assert "开始下载" in sent[1]
    assert "下载完成" in sent[2]
    assert stored["wecom_created_notified"] is True
    assert stored["wecom_started_notified"] is True
    assert stored["wecom_completion_notified"] is True
    assert stored["wecom_created_notifying"] is False
    assert stored["wecom_started_notifying"] is False
    assert stored["wecom_completion_notifying"] is False


def test_completion_will_backfill_created_if_needed():
    reset_jobs()
    sent = []
    app.send_wecom_text = lambda to_user, content: sent.append(content) or {"msgid": str(len(sent))}

    job = sample_job("race-backfill", status="done")
    app.add_job(job)
    app.notify_wecom_job_completion(job.copy())

    stored = next(j for j in app.jobs if j["id"] == "race-backfill")
    assert len(sent) == 3
    assert "任务已创建" in sent[0]
    assert "开始下载" in sent[1]
    assert "下载完成" in sent[2]
    assert stored["wecom_created_notified"] is True
    assert stored["wecom_started_notified"] is True
    assert stored["wecom_completion_notified"] is True


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
        test_completion_notify_failure_does_not_mark_notified,
        test_started_notify_marks_only_after_success,
        test_race_fast_done_sends_created_started_completion_once,
        test_fast_completion_waits_for_created_notification_order,
        test_completion_will_backfill_created_if_needed,
        test_completion_will_backfill_started_for_done_if_needed,
        test_wecom_client_error_details_for_invalid_touser,
    ]
    for test in tests:
        reset_jobs()
        test()
        print(f"PASS: {test.__name__}")
    print("ALL PASS")
