#!/usr/bin/env python3
import json
import core

def main():
    meta = {
        "title": "Instagram Video",
        "formats": [
            {
                "url": "https://example.com/video_360p.mp4",
                "ext": "mp4",
                "width": 640,
                "height": 360,
                "vcodec": "avc1",
                "acodec": "aac",
                "protocol": "https",
            },
            {
                "url": "https://example.com/video_720p.mp4",
                "ext": "mp4",
                "width": 1280,
                "height": 720,
                "vcodec": "avc1",
                "acodec": "aac",
                "protocol": "https",
            }
        ]
    }
    streams, options = core.extract_instagram_streams(meta)
    print(json.dumps({
        "ok": True,
        "streams": streams,
        "sources": [o.get("source") for o in options],
        "count": len(streams)
    }, ensure_ascii=False))

if __name__ == '__main__':
    main()
