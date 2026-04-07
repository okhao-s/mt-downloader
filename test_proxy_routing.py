import app
import core


def test_route_proxy_for_url_bypasses_douyin_and_bilibili():
    proxy = "http://127.0.0.1:7890"
    assert core.route_proxy_for_url("https://www.douyin.com/video/123", proxy) is None
    assert core.route_proxy_for_url("https://www.bilibili.com/video/BV1xx", proxy) is None


def test_route_proxy_for_url_keeps_proxy_for_other_platforms():
    proxy = "http://127.0.0.1:7890"
    assert core.route_proxy_for_url("https://x.com/user/status/1", proxy) == proxy
    assert core.route_proxy_for_url("https://www.youtube.com/watch?v=demo", proxy) == proxy
    assert core.route_proxy_for_url("https://example.com/video", proxy) == proxy


def test_resolve_request_proxy_reuses_default_proxy_and_applies_routing_rules():
    cfg = {"default_proxy": "http://127.0.0.1:7890"}
    assert app.resolve_request_proxy("https://www.douyin.com/video/123", None, cfg) is None
    assert app.resolve_request_proxy("https://www.bilibili.com/video/BV1xx", None, cfg) is None
    assert app.resolve_request_proxy("https://x.com/user/status/1", None, cfg) == "http://127.0.0.1:7890"


def test_resolve_download_mode_uses_ytdlp_for_x_even_when_stream_url_exists():
    assert app.resolve_download_mode("x", None) == "ytdlp"
    assert app.resolve_download_mode("x", "https://video.twimg.com/test/master.m3u8") == "ytdlp"
    assert app.resolve_download_mode("x", "https://video.twimg.com/test/video.mp4") == "ytdlp"


if __name__ == "__main__":
    test_route_proxy_for_url_bypasses_douyin_and_bilibili()
    test_route_proxy_for_url_keeps_proxy_for_other_platforms()
    test_resolve_request_proxy_reuses_default_proxy_and_applies_routing_rules()
    test_resolve_download_mode_uses_ytdlp_for_x_even_when_stream_url_exists()
    print("PASS: test_proxy_routing")
