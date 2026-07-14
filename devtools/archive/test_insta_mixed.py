#!/usr/bin/env python3
import json
import core

def main():
    meta = {
        "title": "Instagram 轮播（视频+图片）",
        "formats": [
            {
                "url": "https://example.com/video_720p.mp4",
                "ext": "mp4",
                "width": 1280,
                "height": 720,
                "vcodec": "avc1",
                "protocol": "https",
            }
        ],
        "thumbnails": [
            {
                "url": "https://example.com/thumb1.jpg",
                "width": 1080,
                "height": 1080
            }
        ]
    }
    # 模拟 extract_platform_streams 的内部逻辑
    platform = "instagram"
    instagram_streams, instagram_options = core.extract_instagram_streams(meta)
    extra_images, extra_image_options, media_entries_from_images = core.extract_instagram_images(meta)
    # 合并返回
    all_streams = instagram_streams
    all_options = instagram_options + extra_image_options
    media_entries = media_entries_from_images

    print(json.dumps({
        "ok": True,
        "video_streams": all_streams,
        "image_options_count": len(extra_image_options),
        "media_entries_count": len(media_entries),
        "combined_options_sources": [o.get("source") for o in all_options],
    }, ensure_ascii=False))

if __name__ == '__main__':
    main()
