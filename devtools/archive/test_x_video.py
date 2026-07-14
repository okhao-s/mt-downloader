#!/usr/bin/env python3
import sys
sys.path.insert(0, '/root/.openclaw/workspace/mt-downloader')
import core
from unittest.mock import patch

def fake_ytdlp_x_video(url, *args, **kwargs):
    # 模拟一个带有视频的 X (Twitter) 帖子
    return {
        "title": "X Video Post",
        "url": "https://pbs.twimg.com/ext_tw_video/1234567890123456789/pu/vid/640x360/abcd1234.mp4",
        "formats": [
            {
                "url": "https://pbs.twimg.com/ext_tw_video/1234567890123456789/pu/vid/640x360/abcd1234.mp4",
                "ext": "mp4",
                "width": 640,
                "height": 360,
                "vcodec": "avc1",
                "acodec": "aac",
                "tbr": 1200,
            }
        ],
        "thumbnails": [
            {
                "url": "https://pbs.twimg.com/media/thumb123.jpg?format=jpg&name=small",
                "width": 680,
                "height": 680
            }
        ]
    }

def test_x_video():
    with patch.object(core, 'extract_info_with_ytdlp_flare', side_effect=fake_ytdlp_x_video):
        url = "https://twitter.com/user/status/1234567890123456789"
        info = core._discover_stream_uncached(url)
        print("media_type:", info.get("media_type"))
        print("streams count:", len(info.get("streams", [])))
        print("images count:", len(info.get("images", [])))
        print("best stream:", info.get("resolved_url"))
        assert info["media_type"] == "video", f"expected video, got {info['media_type']}"
        assert len(info["streams"]) >= 1, "expected at least one video stream"
        assert info["images"] == [], f"unexpected images: {info['images']}"
        print({"ok": True, "streams": info["streams"], "images": info["images"]})

if __name__ == "__main__":
    test_x_video()
