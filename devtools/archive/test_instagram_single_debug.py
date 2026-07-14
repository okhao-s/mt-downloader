#!/usr/bin/env python3
import sys
sys.path.insert(0, '/root/.openclaw/workspace/mt-downloader')
import core
from unittest.mock import patch

def fake_ytdlp_single_image(url, *args, **kwargs):
    # 模拟 Instagram 单图帖子：yt-dlp 返回一个 entry，只有 thumbnail，没有视频
    return {
        "title": "Single Photo",
        "thumbnail": "https://scontent.cdninstagram.com/v/t51.2885-15/12345678_1.jpg?stp=dst-jpg_e35",
        "thumbnails": [
            {"url": "https://scontent.cdninstagram.com/v/t51.2885-15/12345678_1.jpg?stp=dst-jpg_e35", "width": 1080, "height": 1080},
            {"url": "https://scontent.cdninstagram.com/v/t51.2885-15/12345678_2.jpg?stp=dst-jpg_e35", "width": 320, "height": 320},
        ],
        "entries": []
    }

def fake_ytdlp_carousel(url, *args, **kwargs):
    # 模拟轮播：多个 entries，每个 entry 是一个图片
    return {
        "title": "Carousel",
        "entries": [
            {
                "url": "https://scontent.cdninstagram.com/v/t51.2885-15/111_1.jpg",
                "thumbnails": [{"url": "https://scontent.cdninstagram.com/v/t51.2885-15/111_1.jpg"}],
                "thumbnail": "https://scontent.cdninstagram.com/v/t51.2885-15/111_1.jpg"
            },
            {
                "url": "https://scontent.cdninstagram.com/v/t51.2885-15/222_1.jpg",
                "thumbnails": [{"url": "https://scontent.cdninstagram.com/v/t51.2885-15/222_1.jpg"}],
                "thumbnail": "https://scontent.cdninstagram.com/v/t51.2885-15/222_1.jpg"
            }
        ]
    }

def test_single_image_parsing():
    with patch.object(core, 'extract_info_with_ytdlp_flare', side_effect=fake_ytdlp_single_image):
        url = "https://www.instagram.com/p/ABC123/"
        info = core._discover_stream_uncached(url)
        print("=== Single Image Test ===")
        print("media_type:", info.get("media_type"))
        print("images count:", len(info.get("images", [])))
        print("images:", info.get("images"))
        print("media_entries count:", len(info.get("media_entries", [])))
        print("extractor:", info.get("extractor"))

def test_carousel_parsing():
    with patch.object(core, 'extract_info_with_ytdlp_flare', side_effect=fake_ytdlp_carousel):
        url = "https://www.instagram.com/p/DEF456/"
        info = core._discover_stream_uncached(url)
        print("\n=== Carousel Test ===")
        print("media_type:", info.get("media_type"))
        print("images count:", len(info.get("images", [])))
        print("media_entries count:", len(info.get("media_entries", [])))
        print("extractor:", info.get("extractor"))

if __name__ == '__main__':
    test_single_image_parsing()
    test_carousel_parsing()
