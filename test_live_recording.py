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


if __name__ == "__main__":
    test_create_live_record_job_uses_segment_and_log_metadata()
    test_resolve_recording_extension_prefers_flv_for_flv_stream()
    print("PASS: test_live_recording")
