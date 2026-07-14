#!/usr/bin/env python3
import sys
sys.path.insert(0, '/root/.openclaw/workspace/mt-downloader')
import core

def test_extract_x_streams():
    meta = {
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
                "filesize": 5000000,
                "protocol": "https",
            }
        ],
        "thumbnails": [
            {
                "url": "https://pbs.twimg.com/media/thumb123.jpg",
                "width": 680,
                "height": 680
            }
        ]
    }
    streams, options, media_entries = core.extract_x_streams(meta)
    print("streams:", streams)
    print("options:", [o.get('url') for o in options])
    print("media_entries count:", len(media_entries))
    assert len(streams) == 1, f"expected 1 stream, got {len(streams)}"
    assert streams[0].endswith('.mp4'), f"expected mp4 stream, got {streams[0]}"
    assert len(media_entries) == 1, f"expected 1 media entry, got {len(media_entries)}"
    print({"ok": True, "stream": streams[0]})

if __name__ == '__main__':
    test_extract_x_streams()
