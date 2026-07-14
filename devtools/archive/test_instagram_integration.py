#!/usr/bin/env python3
import sys
sys.path.insert(0, '/root/.openclaw/workspace/mt-downloader')
import core
from unittest.mock import patch

def fake_ytdlp(url, referer=None, user_agent=None, proxy=None, cookies_path=None):
    # 模拟 Instagram video 的 yt-dlp 返回
    return {
        "title": "Instagram Video Test",
        "formats": [
            {
                "url": "https://example.com/insta_360p.mp4",
                "ext": "mp4",
                "width": 640,
                "height": 360,
                "vcodec": "avc1",
                "acodec": "aac",
                "protocol": "https",
                "duration": 60,
                "tbr": 500,
            },
            {
                "url": "https://example.com/insta_720p.mp4",
                "ext": "mp4",
                "width": 1280,
                "height": 720,
                "vcodec": "avc1",
                "acodec": "aac",
                "protocol": "https",
                "duration": 60,
                "tbr": 1200,
            }
        ],
        "thumbnail": "https://example.com/insta_thumb.jpg"
    }

def fake_fetch_html(url, referer=None, user_agent=None, proxy=None):
    return "<html><head><title>Test</title></head><body>No images</body></html>"

def test_instagram_video_parsing():
    with patch.object(core, 'extract_info_with_ytdlp_flare', side_effect=fake_ytdlp):
        with patch.object(core, 'fetch_webpage_html', side_effect=fake_fetch_html):
            url = "https://www.instagram.com/p/DaX_6MEMlrZ/"
            info = core._discover_stream_uncached(url)
            assert info['media_type'] == 'video', f"expected video, got {info['media_type']}"
            assert len(info['streams']) >= 1, f"expected at least one stream, got {len(info['streams'])}"
            assert any('.mp4' in s for s in info['streams']), f"expected mp4 in streams: {info['streams']}"
            print({"ok": True, "media_type": info['media_type'], "stream_cnt": len(info['streams']), "streams": info['streams']})

if __name__ == '__main__':
    test_instagram_video_parsing()
