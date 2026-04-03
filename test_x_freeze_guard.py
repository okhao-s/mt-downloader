import threading
import time
from pathlib import Path

import core


def test_extract_info_with_ytdlp_times_out(monkeypatch):
    def fake_run(*args, **kwargs):
        raise core.subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout", 0))

    monkeypatch.setattr(core.subprocess, "run", fake_run)

    try:
        core.extract_info_with_ytdlp("https://x.com/user/status/1")
        assert False, "expected timeout"
    except RuntimeError as exc:
        assert "yt-dlp 探测超时" in str(exc)


def test_ffmpeg_download_does_not_deadlock_when_stderr_is_noisy(tmp_path: Path):
    original_popen = core.subprocess.Popen

    class FakeProc:
        def __init__(self):
            self.returncode = 1
            self.stdout = iter([
                "noise line 1\n",
                "noise line 2\n",
                "progress=end\n",
            ])

        def wait(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

    monkeypatch = None
    core.subprocess.Popen = lambda *args, **kwargs: FakeProc()
    try:
        done = {"ok": False, "err": None}

        def runner():
            try:
                core.ffmpeg_download("https://example.com/test.m3u8", tmp_path / "x.mp4")
                done["ok"] = True
            except Exception as exc:
                done["err"] = str(exc)
                done["ok"] = True

        thread = threading.Thread(target=runner)
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive(), "ffmpeg_download should return instead of deadlocking"
        assert done["ok"]
        assert "noise line" in (done["err"] or "")
    finally:
        core.subprocess.Popen = original_popen
