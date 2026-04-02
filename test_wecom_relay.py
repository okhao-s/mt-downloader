import app


def test_send_wecom_forward_notification_uses_custom_forward_url():
    captured = {}

    class DummyResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True, "msgid": "forward-msg-1"}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return DummyResp()

    old_post = app.requests.post
    app.requests.post = fake_post
    try:
        cfg = {
            "wecom_forward_url": "http://forward.test:18123/wecom/notify",
            "wecom_forward_token": "forward-token",
        }
        job = {
            "id": "job-forward-1",
            "title": "超短视频",
            "output": "short.mp4",
            "status": "done",
            "error": "",
            "source_url": "https://example.com/v.mp4",
            "platform": "douyin",
            "status_text": "下载完成",
        }

        result = app.send_wecom_forward_notification(job, "done", "zhangsan", "hello forward", cfg=cfg)
    finally:
        app.requests.post = old_post

    assert result["ok"] is True
    assert captured["url"] == "http://forward.test:18123/wecom/notify"
    assert captured["headers"]["X-Wecom-Forward-Token"] == "forward-token"
    assert captured["json"] == {
        "kind": "done",
        "job_id": "job-forward-1",
        "to_user": "zhangsan",
        "content": "hello forward",
        "title": "超短视频",
        "status": "done",
        "error": "",
        "source_url": "https://example.com/v.mp4",
        "platform": "douyin",
        "output": "short.mp4",
        "status_text": "下载完成",
    }
    assert captured["timeout"] == 20


if __name__ == "__main__":
    test_send_wecom_forward_notification_uses_custom_forward_url()
    print("PASS: test_send_wecom_forward_notification_uses_custom_forward_url")
