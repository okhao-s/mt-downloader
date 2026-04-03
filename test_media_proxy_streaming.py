from fastapi.testclient import TestClient

import app as appmod


class FakeStreamResponse:
    def __init__(self):
        self.status_code = 200
        self.headers = {
            "content-type": "video/mp2t",
            "content-length": "6",
            "accept-ranges": "bytes",
        }
        self.closed = False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=0):
        yield b"abc"
        yield b"def"

    def close(self):
        self.closed = True


def test_media_proxy_streams_response_without_buffering(monkeypatch):
    holder = {}

    def fake_get(*args, **kwargs):
        holder["stream"] = kwargs.get("stream") if "stream" in kwargs else (args[5] if len(args) > 5 else None)
        resp = FakeStreamResponse()
        holder["resp"] = resp
        return resp

    monkeypatch.setattr(appmod, "safe_requests_get", fake_get)
    client = TestClient(appmod.app)

    response = client.get("/api/media", params={"target": "https://example.com/seg.ts"})

    assert response.status_code == 200
    assert response.content == b"abcdef"
    assert response.headers["content-type"].startswith("video/mp2t")
    assert response.headers.get("accept-ranges") == "bytes"
    assert holder["stream"] is True
    assert holder["resp"].closed is True
