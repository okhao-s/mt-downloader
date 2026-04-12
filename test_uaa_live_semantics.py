from fastapi import HTTPException
from fastapi.testclient import TestClient

import app


def test_parse_api_marks_uaa_room_as_live(monkeypatch=None):
    old_discover = app.discover_stream
    old_run = app.run_in_executor
    old_load_config = app.load_config
    old_resolve_site = app.resolve_site_cookies_path
    try:
        app.load_config = lambda: {}
        app.resolve_site_cookies_path = lambda url, cfg: None

        async def fake_run_in_executor(executor, func, *args, **kwargs):
            return func(*args, **kwargs)

        app.run_in_executor = fake_run_in_executor
        app.discover_stream = lambda *args, **kwargs: {
            'source_url': 'https://zh.live.uaa.com/demo-room',
            'resolved_url': 'https://edge.example.com/live/master.m3u8',
            'title': 'UAA Demo',
            'thumbnail': None,
            'is_m3u8': True,
            'extractor': 'uaa-room',
            'streams': ['https://edge.example.com/live/master.m3u8'],
            'stream_options': [{'url': 'https://edge.example.com/live/master.m3u8', 'format_note': 'source'}],
            'quality_options': [{'url': 'https://edge.example.com/live/master.m3u8', 'format_note': 'source'}],
            'quality_count': 1,
            'all_quality_options': [{'url': 'https://edge.example.com/live/master.m3u8', 'format_note': 'source'}],
            'all_quality_count': 1,
            'images': [],
            'image_options': [],
            'media_type': 'live',
            'is_live': True,
            'live_record_supported': True,
            'errors': [],
            'media_entries': [],
            'platform': 'uaa',
        }
        client = TestClient(app.app)
        res = client.post('/api/parse', json={'url': 'https://zh.live.uaa.com/demo-room'})
        assert res.status_code == 200, res.text
        data = res.json()
        assert data['media_type'] == 'live'
        assert data['is_live'] is True
        assert data['live_record_supported'] is True
        assert data['platform'] == 'uaa'
        assert data['stream_count'] == 1
        assert data['suggested_output'].startswith('uaa-live') or data['suggested_output'].startswith('UAA Demo')
    finally:
        app.discover_stream = old_discover
        app.run_in_executor = old_run
        app.load_config = old_load_config
        app.resolve_site_cookies_path = old_resolve_site


def test_parse_api_keeps_uaa_live_semantics_when_room_has_no_active_stream():
    old_discover = app.discover_stream
    old_run = app.run_in_executor
    old_load_config = app.load_config
    old_resolve_site = app.resolve_site_cookies_path
    try:
        app.load_config = lambda: {}
        app.resolve_site_cookies_path = lambda url, cfg: None

        async def fake_run_in_executor(executor, func, *args, **kwargs):
            return func(*args, **kwargs)

        app.run_in_executor = fake_run_in_executor
        app.discover_stream = lambda *args, **kwargs: {
            'source_url': 'https://zh.live.uaa.com/demo-room',
            'resolved_url': None,
            'title': 'UAA Demo Offline',
            'thumbnail': None,
            'is_m3u8': False,
            'extractor': 'uaa-room',
            'streams': [],
            'stream_options': [],
            'quality_options': [],
            'quality_count': 0,
            'all_quality_options': [],
            'all_quality_count': 0,
            'images': [],
            'image_options': [],
            'media_type': 'live',
            'is_live': True,
            'live_record_supported': False,
            'errors': ['uaa 房间解析失败：UAA 房间页未解析到可录制直播流'],
            'media_entries': [],
            'platform': 'uaa',
        }
        client = TestClient(app.app)
        res = client.post('/api/parse', json={'url': 'https://zh.live.uaa.com/demo-room'})
        assert res.status_code == 200, res.text
        data = res.json()
        assert data['media_type'] == 'live'
        assert data['is_live'] is True
        assert data['live_record_supported'] is False
        assert data['platform'] == 'uaa'
        assert data['stream_count'] == 0
        assert data['preview_url'] is None
    finally:
        app.discover_stream = old_discover
        app.run_in_executor = old_run
        app.load_config = old_load_config
        app.resolve_site_cookies_path = old_resolve_site


def test_create_download_job_rejects_live_source():
    old_discover = app.discover_stream
    old_load_config = app.load_config
    try:
        app.load_config = lambda: {}
        app.discover_stream = lambda *args, **kwargs: {
            'source_url': 'https://zh.live.uaa.com/demo-room',
            'resolved_url': 'https://edge.example.com/live/master.m3u8',
            'streams': ['https://edge.example.com/live/master.m3u8'],
            'stream_options': [{'url': 'https://edge.example.com/live/master.m3u8'}],
            'media_type': 'live',
            'is_live': True,
            'platform': 'uaa',
            'extractor': 'uaa-room',
            'images': [],
            'media_entries': [],
        }
        payload = app.DownloadPayload(url='https://zh.live.uaa.com/demo-room')
        try:
            app.create_download_job(payload)
            raise AssertionError('expected HTTPException for live source')
        except HTTPException as exc:
            assert exc.status_code == 400
            assert '开始录制直播' in str(exc.detail)
    finally:
        app.discover_stream = old_discover
        app.load_config = old_load_config


def test_discover_stream_keeps_uaa_live_semantics_when_room_has_no_active_stream(monkeypatch=None):
    import core

    old_core_resolve = core.resolve_uaa_live_room
    old_probe = core.probe_webpage
    old_ytdlp = core.extract_info_with_ytdlp
    try:
        def fake_resolve(*args, **kwargs):
            raise ValueError('UAA 房间页未解析到可录制直播流')

        core.resolve_uaa_live_room = fake_resolve
        core.probe_webpage = lambda *args, **kwargs: {'streams': ['https://example.com/fake.mp4'], 'stream_options': [{'url': 'https://example.com/fake.mp4'}], 'title': 'fake'}
        core.extract_info_with_ytdlp = lambda *args, **kwargs: {'title': 'fake', 'thumbnail': None, 'formats': []}
        info = core.discover_stream('https://zh.live.uaa.com/demo-room')
        assert info['media_type'] == 'live'
        assert info['is_live'] is True
        assert info['live_record_supported'] is False
        assert info['platform'] == 'uaa'
        assert info['extractor'] == 'uaa-room'
        assert info['resolved_url'] is None
        assert not info['streams']
        assert 'uaa 房间解析失败' in '\n'.join(info.get('errors') or [])
    finally:
        core.resolve_uaa_live_room = old_core_resolve
        core.probe_webpage = old_probe
        core.extract_info_with_ytdlp = old_ytdlp


if __name__ == '__main__':
    test_parse_api_marks_uaa_room_as_live()
    test_parse_api_keeps_uaa_live_semantics_when_room_has_no_active_stream()
    test_create_download_job_rejects_live_source()
    test_discover_stream_keeps_uaa_live_semantics_when_room_has_no_active_stream()
    print('PASS: test_uaa_live_semantics')
