#!/usr/bin/env python3
import sys
sys.path.insert(0, '/root/.openclaw/workspace/mt-downloader')
import core

def test_x_carousel_with_photo_and_video():
    meta = {
        "entries": [
            {
                "url": "https://pbs.twimg.com/media/photo1.jpg?format=jpg&name=small",
                "thumbnails": [{"url": "https://pbs.twimg.com/media/photo1.jpg"}],
                "extractor": "twitter",
                "id": "123",
                "display_id": "123",
                "webpage_url": "https://twitter.com/user/status/123",
                "thumbnail": "https://pbs.twimg.com/media/photo1.jpg"
            },
            {
                "url": "https://pbs.twimg.com/ext_tw_video/12345/pu/vid/640x360/abcd.mp4",
                "formats": [
                    {
                        "url": "https://pbs.twimg.com/ext_tw_video/12345/pu/vid/640x360/abcd.mp4",
                        "ext": "mp4",
                        "width": 640,
                        "height": 360,
                        "vcodec": "avc1",
                        "acodec": "aac",
                        "tbr": 1200,
                    }
                ],
                "thumbnails": [{"url": "https://pbs.twimg.com/media/vidthumb.jpg"}],
                "extractor": "twitter",
                "id": "456",
                "display_id": "456",
                "webpage_url": "https://twitter.com/user/status/456",
                "thumbnail": "https://pbs.twimg.com/media/vidthumb.jpg"
            }
        ]
    }
    streams, options, media_entries = core.extract_x_streams(meta)
    print("streams:", streams)
    print("media_entries count:", len(media_entries))
    assert len(streams) == 1, f"expected 1 stream (the video), got {len(streams)}: {streams}"
    assert all('.mp4' in s for s in streams), "all streams should be video"
    # 检查 media_entries 长度
    assert len(media_entries) == 2, f"expected 2 media entries (photo + video), got {len(media_entries)}"
    print({"ok": True, "streams": streams})

if __name__ == '__main__':
    test_x_carousel_with_photo_and_video()
