import core


def test_x_fallback_runs_even_if_ytdlp_fails(monkeypatch):
    def fake_probe(*args, **kwargs):
        return {
            "title": None,
            "streams": [],
            "stream_options": [],
        }

    def fake_ytdlp(*args, **kwargs):
        raise RuntimeError("No video could be found in this tweet")

    def fake_x_fallback(url, info, referer=None, user_agent=None, proxy=None, cookies_path=None):
        return ["https://video.twimg.com/test/master.m3u8"], [core.build_stream_option("https://video.twimg.com/test/master.m3u8", source="twitter-fallback")], None

    monkeypatch.setattr(core, "probe_webpage", fake_probe)
    monkeypatch.setattr(core, "extract_info_with_ytdlp", fake_ytdlp)
    monkeypatch.setattr(core, "try_x_fallback_streams", fake_x_fallback)
    core._DISCOVER_STREAM_CACHE.clear()

    info = core.discover_stream("https://x.com/i/status/123")

    assert info["resolved_url"] == "https://video.twimg.com/test/master.m3u8"
    assert info["streams"] == ["https://video.twimg.com/test/master.m3u8"]
    assert any("yt-dlp 探测失败" in err for err in info["errors"])
