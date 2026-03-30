from pathlib import Path

import app


def test_resolve_download_mode_prefers_ytdlp_for_douyin_direct_stream():
    stream_url = "https://v5-hl-qn-ov.zjcdn.com/video/tos/cn/tos-cn-ve-15c000-ce/demo.mp4"
    assert app.resolve_download_mode("douyin", stream_url) == "ytdlp"


if __name__ == "__main__":
    test_resolve_download_mode_prefers_ytdlp_for_douyin_direct_stream()
    print("PASS: test_resolve_download_mode_prefers_ytdlp_for_douyin_direct_stream")
