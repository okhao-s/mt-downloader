import core


def test_direct_mp4_url_is_detected_as_downloadable_stream():
    url = "https://example.com/sample-video.mp4?token=abc"
    info = core.discover_stream(url)

    assert info["resolved_url"] == url
    assert info["is_m3u8"] is False
    assert info["extractor"] == "direct-media"
    assert info["streams"] == [url]


if __name__ == "__main__":
    test_direct_mp4_url_is_detected_as_downloadable_stream()
    print("PASS: test_direct_mp4_url_is_detected_as_downloadable_stream")
