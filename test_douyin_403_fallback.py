from pathlib import Path
from unittest.mock import patch

import requests

import app


class _Resp:
    status_code = 403


class _Progress:
    def __init__(self):
        self.calls = []

    def __call__(self, progress, status):
        self.calls.append((progress, status))


def test_run_download_job_falls_back_to_ytdlp_on_douyin_direct_403():
    output_path = Path('/tmp/mt-douyin-fallback.mp4')
    progress = _Progress()

    app.jobs = [{
        'id': 'job403',
        'status': 'queued',
        'hidden': False,
        'cancel_requested': False,
    }]

    with patch.object(app, 'count_active_jobs', return_value=0), \
         patch.object(app, 'update_job', side_effect=lambda job_id, **kwargs: {'id': job_id, **kwargs}), \
         patch.object(app, 'load_config', return_value={'douyinck': '/tmp/not-exist.cookies'}), \
         patch.object(app, 'resolve_site_cookies_path', return_value='/tmp/not-exist.cookies'), \
         patch.object(app, 'should_use_site_cookies', return_value=False), \
         patch.object(app, 'direct_download', side_effect=requests.HTTPError(response=_Resp())), \
         patch.object(app, 'download_with_ytdlp') as mocked_ytdlp:
        app.run_download_job(
            'job403',
            preview_url='http://127.0.0.1:8080/api/preview.m3u8',
            output_path=output_path,
            aggressive=False,
            stream_url='https://v5-hl-qn-ov.zjcdn.com/video/tos/cn/tos-cn-ve-15c000-ce/demo.mp4',
            download_via='direct',
            source_url='https://www.douyin.com/video/7483505190247449893',
        )

    assert mocked_ytdlp.called
    args, kwargs = mocked_ytdlp.call_args
    assert args[0] == 'https://www.douyin.com/video/7483505190247449893'


if __name__ == '__main__':
    test_run_download_job_falls_back_to_ytdlp_on_douyin_direct_403()
    print('PASS: test_run_download_job_falls_back_to_ytdlp_on_douyin_direct_403')
