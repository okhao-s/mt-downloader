#!/usr/bin/env python3
import json
import core

def main():
    meta = {
        "title": "测试视频",
        "thumbnail": "https://example.com/thumb.jpg",
        "formats": [
            {
                "format_id": "18",
                "url": "https://example.com/video_360p.mp4",
                "ext": "mp4",
                "width": 640,
                "height": 360,
                "vcodec": "avc1",
                "acodec": "aac",
                "duration": 120,
                "tbr": 500,
                "filesize": 5000000,
                "protocol": "https",
                "format_note": "360p"
            },
            {
                "format_id": "22",
                "url": "https://example.com/video_720p.mp4",
                "ext": "mp4",
                "width": 1280,
                "height": 720,
                "vcodec": "avc1",
                "acodec": "aac",
                "duration": 120,
                "tbr": 1200,
                "filesize": 15000000,
                "protocol": "https",
                "format_note": "720p"
            }
        ]
    }
    streams, options = core.extract_instagram_streams(meta)
    assert len(streams) == 2, f"expected 2 streams, got {len(streams)}"
    assert all(isinstance(s, str) for s in streams), "all stream URLs must be strings"
    assert len(options) == 2, f"expected 2 options, got {len(options)}"
    assert all('url' in o and 'source' in o for o in options), "options must contain url and source"
    print(json.dumps({
        "ok": True,
        "stream_count": len(streams),
        "option_sources": [o['source'] for o in options],
        "best_url": streams[0] if streams else None
    }, ensure_ascii=False))

if __name__ == '__main__':
    main()
