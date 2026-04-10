import asyncio

import app
import core


def test_extract_x_streams_from_graphql_payload_keeps_all_media_entries():
    payload = {
        "data": {
            "tweetResult": {
                "result": {
                    "legacy": {
                        "full_text": "四宫格视频帖",
                        "extended_entities": {
                            "media": [
                                {
                                    "type": "video",
                                    "media_key": "3_1",
                                    "media_url_https": "https://pbs.twimg.com/ext_tw_video_thumb/1/pu/img/a.jpg",
                                    "video_info": {
                                        "variants": [
                                            {"url": "https://video.twimg.com/ext_tw_video/1/pu/vid/640x360/a.mp4", "bitrate": 832000},
                                            {"url": "https://video.twimg.com/ext_tw_video/1/pu/vid/1280x720/a.mp4", "bitrate": 2176000},
                                        ]
                                    },
                                },
                                {
                                    "type": "video",
                                    "media_key": "3_2",
                                    "media_url_https": "https://pbs.twimg.com/ext_tw_video_thumb/2/pu/img/b.jpg",
                                    "video_info": {
                                        "variants": [
                                            {"url": "https://video.twimg.com/ext_tw_video/2/pu/vid/320x180/b.mp4", "bitrate": 256000},
                                            {"url": "https://video.twimg.com/ext_tw_video/2/pu/vid/1280x720/b.mp4", "bitrate": 2176000},
                                        ]
                                    },
                                },
                            ]
                        },
                    }
                }
            }
        }
    }
    info = core.extract_x_streams_from_graphql_payload(payload)
    assert info["title"] == "四宫格视频帖"
    assert len(info["media_entries"]) == 2
    assert len(info["streams"]) == 2
    assert info["streams"][0].endswith("/1280x720/a.mp4")
    assert info["streams"][1].endswith("/1280x720/b.mp4")
    assert info["media_entries"][0]["media_index"] == 0
    assert info["media_entries"][1]["media_index"] == 1


def test_extract_x_streams_from_ytdlp_entries_keeps_each_media_best_stream():
    meta = {
        "entries": [
            {
                "id": "part1",
                "thumbnail": "https://pbs.twimg.com/ext_tw_video_thumb/1/pu/img/a.jpg",
                "formats": [
                    {"url": "https://video.twimg.com/ext_tw_video/1/pu/vid/640x360/a.mp4", "vcodec": "h264", "acodec": "aac", "width": 640, "height": 360, "tbr": 832},
                    {"url": "https://video.twimg.com/ext_tw_video/1/pu/vid/1280x720/a.mp4", "vcodec": "h264", "acodec": "aac", "width": 1280, "height": 720, "tbr": 2176},
                ],
            },
            {
                "id": "part2",
                "thumbnail": "https://pbs.twimg.com/ext_tw_video_thumb/2/pu/img/b.jpg",
                "formats": [
                    {"url": "https://video.twimg.com/ext_tw_video/2/pu/vid/320x180/b.mp4", "vcodec": "h264", "acodec": "aac", "width": 320, "height": 180, "tbr": 256},
                    {"url": "https://video.twimg.com/ext_tw_video/2/pu/vid/1280x720/b.mp4", "vcodec": "h264", "acodec": "aac", "width": 1280, "height": 720, "tbr": 2176},
                ],
            },
        ]
    }
    streams, options, media_entries = core.extract_x_streams(meta)
    assert len(streams) == 2
    assert len(options) == 2
    assert len(media_entries) == 2
    assert streams[0].endswith("/1280x720/a.mp4")
    assert streams[1].endswith("/1280x720/b.mp4")


def test_build_video_output_name_adds_media_suffix_for_multi_video():
    assert app.build_video_output_name("示例标题", 0, 4) == "示例标题 - 1.mp4"
    assert app.build_video_output_name("示例标题", 3, 4) == "示例标题 - 4.mp4"
    assert app.build_video_output_name("示例标题", 0, 1) == "示例标题.mp4"


def test_download_all_x_multi_video_creates_one_job_per_media():
    old_load_config = app.load_config
    old_resolve_request_proxy = app.resolve_request_proxy
    old_resolve_site_cookies_path = app.resolve_site_cookies_path
    old_run_in_executor = app.run_in_executor
    old_discover_stream = app.discover_stream
    old_create_download_job = app.create_download_job

    captured = []
    info = {
        "title": "四个视频",
        "media_type": "video",
        "streams": [
            "https://video.twimg.com/ext_tw_video/1/pu/vid/1280x720/a.mp4",
            "https://video.twimg.com/ext_tw_video/2/pu/vid/1280x720/b.mp4",
        ],
        "media_entries": [
            {
                "media_index": 0,
                "streams": [
                    "https://video.twimg.com/ext_tw_video/1/pu/vid/640x360/a.mp4",
                    "https://video.twimg.com/ext_tw_video/1/pu/vid/1280x720/a.mp4",
                ],
                "best_stream_url": "https://video.twimg.com/ext_tw_video/1/pu/vid/1280x720/a.mp4",
            },
            {
                "media_index": 1,
                "streams": [
                    "https://video.twimg.com/ext_tw_video/2/pu/vid/320x180/b.mp4",
                    "https://video.twimg.com/ext_tw_video/2/pu/vid/1280x720/b.mp4",
                ],
                "best_stream_url": "https://video.twimg.com/ext_tw_video/2/pu/vid/1280x720/b.mp4",
            },
        ],
        "images": [],
    }

    async def fake_run_in_executor(_executor, func, *args, **kwargs):
        return func(*args, **kwargs)

    def fake_create_download_job(payload, retry_of=None):
        captured.append(payload.model_dump())
        return {
            "id": f"job-{len(captured)}",
            "output": f"四个视频 - {len(captured)}.mp4",
            "stream_index": payload.stream_index,
            "media_index": payload.media_index,
            "status_text": "排队中",
        }

    try:
        app.load_config = lambda: {}
        app.resolve_request_proxy = lambda *args, **kwargs: ""
        app.resolve_site_cookies_path = lambda *args, **kwargs: None
        app.run_in_executor = fake_run_in_executor
        app.discover_stream = lambda *args, **kwargs: info
        app.create_download_job = fake_create_download_job

        payload = app.BatchDownloadPayload(url="https://x.com/user/status/123")
        result = asyncio.run(app.download_all(None, payload))
    finally:
        app.load_config = old_load_config
        app.resolve_request_proxy = old_resolve_request_proxy
        app.resolve_site_cookies_path = old_resolve_site_cookies_path
        app.run_in_executor = old_run_in_executor
        app.discover_stream = old_discover_stream
        app.create_download_job = old_create_download_job

    assert result["ok"] is True
    assert result["stream_count"] == 2
    assert len(result["jobs"]) == 2
    assert captured[0]["media_index"] == 0
    assert captured[1]["media_index"] == 1
    assert captured[0]["stream_index"] == 1
    assert captured[1]["stream_index"] == 1
    assert captured[0]["output"] == "四个视频"
    assert captured[1]["output"] == "四个视频"


if __name__ == "__main__":
    test_extract_x_streams_from_graphql_payload_keeps_all_media_entries()
    test_extract_x_streams_from_ytdlp_entries_keeps_each_media_best_stream()
    test_build_video_output_name_adds_media_suffix_for_multi_video()
    test_download_all_x_multi_video_creates_one_job_per_media()
    print("PASS: test_x_multi_video_download")
