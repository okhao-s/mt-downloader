import core


class DummyResponse:
    def __init__(self, text="", json_data=None, status_code=200):
        self.text = text
        self._json = json_data
        self.status_code = status_code

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


def test_extract_m3u8_variants_parses_master_playlist():
    playlist = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=4724121,RESOLUTION=1920x1080,NAME=\"source\"
https://media-hls.doppiocdn.org/b-hls-20/228402241/228402241.m3u8?playlistType=lowLatency
#EXT-X-STREAM-INF:BANDWIDTH=1391104,RESOLUTION=854x480,NAME=\"480p\"
https://media-hls.doppiocdn.org/b-hls-20/228402241/228402241_480p.m3u8?playlistType=lowLatency
"""
    items = core._extract_m3u8_variants(playlist, "https://edge-hls.doppiocdn.org/hls/228402241/master/228402241_auto.m3u8?playlistType=lowLatency")
    assert len(items) == 2
    assert items[0]["url"].startswith("https://media-hls.doppiocdn.org/b-hls-20/228402241/228402241.m3u8")
    assert items[0]["meta"]["height"] == 1080
    assert items[1]["meta"]["format_note"] == "480p"


def test_resolve_uaa_live_room_uses_api_front_and_master_playlist(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if "/api/front/v2/models/username/GG-BONG1/cam" in url:
            return DummyResponse(json_data={
                "cam": {"streamName": "228402241"}
            })
        if "edge-hls.doppiocdn.org/hls/228402241/master/228402241_auto.m3u8" in url:
            return DummyResponse(text="""#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=4724121,RESOLUTION=1920x1080,NAME=\"source\"
https://media-hls.doppiocdn.org/b-hls-20/228402241/228402241.m3u8?playlistType=lowLatency
#EXT-X-STREAM-INF:BANDWIDTH=1391104,RESOLUTION=854x480,NAME=\"480p\"
https://media-hls.doppiocdn.org/b-hls-20/228402241/228402241_480p.m3u8?playlistType=lowLatency
""")
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(core, "fetch_webpage_html", lambda *args, **kwargs: "<title>GG-BONG1</title>")
    monkeypatch.setattr(core.requests, "get", fake_get)

    info = core.resolve_uaa_live_room("https://zh.live.uaa.com/GG-BONG1")
    assert any("/api/front/v2/models/username/GG-BONG1/cam" in url for url in calls)
    assert any("edge-hls.doppiocdn.org/hls/228402241/master/228402241_auto.m3u8" in url for url in calls)
    assert info["resolved_url"].startswith("https://media-hls.doppiocdn.org/b-hls-20/228402241/228402241.m3u8")
    assert info["platform"] == "uaa"
    assert info["extractor"] == "uaa-room"
    assert len(info["streams"]) == 1
    assert len(info["stream_options"]) == 1
    assert len(info["quality_options"]) == 1
    assert info["quality_count"] == 1
    assert len(info["all_quality_options"]) == 2
    assert info["all_quality_count"] == 2


if __name__ == "__main__":
    class _MonkeyPatch:
        def setattr(self, obj, name, value):
            setattr(obj, name, value)

    test_extract_m3u8_variants_parses_master_playlist()
    test_resolve_uaa_live_room_uses_api_front_and_master_playlist(_MonkeyPatch())
    print("PASS: test_uaa_live_room")
