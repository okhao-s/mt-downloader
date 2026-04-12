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


def test_create_live_ffmpeg_command_uses_http_safe_reconnect_flags_only():
    cmd = app.create_live_ffmpeg_command(
        "https://example.com/live/test.m3u8",
        Path("/tmp/demo_%Y%m%d-%H%M%S.mp4"),
        referer="https://example.com/room",
        user_agent="demo-agent",
        proxy="http://127.0.0.1:8080",
        segment_minutes=15,
    )
    assert "-rw_timeout" in cmd
    assert "-timeout" not in cmd
    assert "-reconnect" in cmd
    assert "-reconnect_streamed" in cmd
    assert "-reconnect_on_network_error" in cmd
    assert "-reconnect_at_eof" in cmd
    assert "-reconnect_delay_max" in cmd
    assert "-http_proxy" in cmd
    assert "-headers" in cmd


def test_create_live_ffmpeg_command_skips_http_only_flags_for_file_inputs():
    cmd = app.create_live_ffmpeg_command(
        "file:///tmp/input.mp4",
        Path("/tmp/output.mp4"),
    )
    assert "-rw_timeout" not in cmd
    assert "-reconnect" not in cmd
    assert "-timeout" not in cmd


def test_build_live_segment_pattern_is_clean():
    pattern = app.build_live_segment_pattern("demo-live")
    assert pattern == "demo-live_%Y%m%d-%H%M%S.mp4"


if __name__ == "__main__":
    test_create_live_record_job_uses_segment_and_log_metadata()
    test_resolve_recording_extension_prefers_flv_for_flv_stream()
    test_create_live_record_job_resolves_uaa_room_page_to_stream_url()
    test_create_live_ffmpeg_command_uses_http_safe_reconnect_flags_only()
    test_create_live_ffmpeg_command_skips_http_only_flags_for_file_inputs()
    test_build_live_segment_pattern_is_clean()
    print("PASS: test_live_recording")
