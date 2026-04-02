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


def test_send_wecom_forward_notification_supports_wxchat_proxy_base_url():
    calls = []

    class DummyClient:
        def __init__(self, corp_id, agent_id, secret, api_base_url=None, **kwargs):
            calls.append(
                {
                    "corp_id": corp_id,
                    "agent_id": agent_id,
                    "secret": secret,
                    "api_base_url": api_base_url,
                    "kwargs": kwargs,
                }
            )

        def send_text(self, to_user, content):
            calls.append({"to_user": to_user, "content": content})
            return {"errcode": 0, "errmsg": "ok", "msgid": "proxy-msg-1"}

    old_client = app.WeComClient
    app.WeComClient = DummyClient
    try:
        cfg = {
            "wecom_forward_url": "http://82.158.91.5:3000",
            "wecom_corp_id": "ww123",
            "wecom_agent_id": "1000002",
            "wecom_secret": "secret-123",
        }
        job = {
            "id": "job-forward-2",
            "status": "done",
        }

        result = app.send_wecom_forward_notification(job, "done", "zhangsan", "hello wxchat", cfg=cfg)
    finally:
        app.WeComClient = old_client

    assert result["ok"] is True
    assert result["msgid"] == "proxy-msg-1"
    assert calls[0]["api_base_url"] == "http://82.158.91.5:3000"
    assert calls[0]["corp_id"] == "ww123"
    assert calls[0]["agent_id"] == "1000002"
    assert calls[0]["secret"] == "secret-123"
    assert calls[1] == {"to_user": "zhangsan", "content": "hello wxchat"}


if __name__ == "__main__":
    test_send_wecom_forward_notification_uses_custom_forward_url()
    print("PASS: test_send_wecom_forward_notification_uses_custom_forward_url")
    test_send_wecom_forward_notification_supports_wxchat_proxy_base_url()
    print("PASS: test_send_wecom_forward_notification_supports_wxchat_proxy_base_url")
