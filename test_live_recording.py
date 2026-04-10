from pathlib import Path

import app


def reset_jobs():
    with app.jobs_lock:
        app.jobs.clear()


def test_create_live_record_job_uses_segment_and_log_metadata():
    reset_jobs()
    old_executor = app.download_executor

    class DummyExecutor:
        def submit(self, fn, *args, **kwargs):
            self.fn = fn
            self.args = args
            self.kwargs = kwargs
            return None

    app.download_executor = DummyExecutor()
    try:
        payload = app.LiveRecordPayload(
            url="https://example.com/live/test.m3u8",
            output="demo-live",
            segment_minutes=15,
            max_reconnect_attempts=4,
            restart_delay_seconds=3,
        )
        job = app.create_live_record_job(payload)
        assert job["job_type"] == "live_record"
        assert job["status"] == "queued"
        assert job["segment_minutes"] == 15
        assert job["max_reconnect_attempts"] == 4
        assert job["restart_delay_seconds"] == 3
        assert job["output"].endswith(".mp4")
        assert "%Y%m%d-%H%M%S" in job["output"]
        assert job["log_path"].endswith(f"{job['id']}.log")
    finally:
        app.download_executor = old_executor
        reset_jobs()


def test_resolve_recording_extension_prefers_flv_for_flv_stream():
    assert app.resolve_recording_extension("https://example.com/live.flv") == ".flv"
    assert app.resolve_recording_extension("https://example.com/live/index.m3u8") == ".mp4"


def test_create_live_record_job_resolves_uaa_room_page_to_stream_url():
    reset_jobs()
    old_executor = app.download_executor
    old_discover_stream = app.discover_stream
    old_choose_stream_url = app.choose_stream_url

    class DummyExecutor:
        def submit(self, fn, *args, **kwargs):
            self.fn = fn
            self.args = args
            self.kwargs = kwargs
            return None

    app.download_executor = DummyExecutor()
    app.discover_stream = lambda *args, **kwargs: {
        "resolved_url": "https://edge.example.com/live/master.m3u8",
        "streams": [
            "https://edge.example.com/live/master.m3u8",
        ],
        "stream_options": [
            {"url": "https://edge.example.com/live/master.m3u8", "quality": "best"},
        ],
        "quality_options": [
            {"url": "https://edge.example.com/live/master.m3u8", "quality": "best"},
        ],
        "quality_count": 1,
        "all_quality_options": [
            {"url": "https://edge.example.com/live/master.m3u8", "quality": "best"},
            {"url": "https://edge.example.com/live/fallback.m3u8", "quality": "fallback"},
        ],
        "all_quality_count": 2,
        "title": "UAA Demo",
        "platform": "uaa",
        "extractor": "uaa-room",
        "errors": [],
    }
    app.choose_stream_url = lambda info, *args, **kwargs: info["resolved_url"]
    try:
        payload = app.LiveRecordPayload(
            url="https://zh.live.uaa.com/some-room",
            output="uaa-demo",
        )
        job = app.create_live_record_job(payload)
        assert job["job_type"] == "live_record"
        assert job["source_url"] == "https://zh.live.uaa.com/some-room"
        assert job["stream_url"] == "https://edge.example.com/live/master.m3u8"
        assert job["platform"] == "uaa"
        assert job["extractor"] == "uaa-room"
        assert job["title"] == "uaa-demo"
    finally:
        app.download_executor = old_executor
        app.discover_stream = old_discover_stream
        app.choose_stream_url = old_choose_stream_url
        reset_jobs()


if __name__ == "__main__":
    test_create_live_record_job_uses_segment_and_log_metadata()
    test_resolve_recording_extension_prefers_flv_for_flv_stream()
    test_create_live_record_job_resolves_uaa_room_page_to_stream_url()
    print("PASS: test_live_recording")
