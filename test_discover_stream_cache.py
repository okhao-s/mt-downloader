import core


def test_discover_stream_uses_short_ttl_cache(monkeypatch):
    calls = {"count": 0}

    def fake_probe(*args, **kwargs):
        calls["count"] += 1
        return {
            "title": "cached title",
            "streams": ["https://example.com/test.m3u8"],
            "stream_options": [core.build_stream_option("https://example.com/test.m3u8", source="html")],
        }

    def fake_ytdlp(*args, **kwargs):
        raise RuntimeError("skip ytdlp")

    monkeypatch.setattr(core, "probe_webpage", fake_probe)
    monkeypatch.setattr(core, "extract_info_with_ytdlp", fake_ytdlp)
    core._DISCOVER_STREAM_CACHE.clear()

    first = core.discover_stream("https://example.com/post/1")
    second = core.discover_stream("https://example.com/post/1")

    assert calls["count"] == 1
    assert first["resolved_url"] == "https://example.com/test.m3u8"
    assert second["resolved_url"] == "https://example.com/test.m3u8"
    assert first is not second
