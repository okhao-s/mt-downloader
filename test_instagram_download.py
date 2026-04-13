import asyncio

import app
import core


def test_detect_platform_recognizes_instagram():
    assert core.detect_platform("https://www.instagram.com/p/abc123/") == "instagram"
    assert core.detect_platform("https://instagr.am/p/abc123/") == "instagram"


def test_get_download_subdir_uses_image_dir_for_instagram_images():
    path = app.get_download_subdir("https://www.instagram.com/p/abc123/", media_type="image")
    assert str(path).endswith("/downloads/image")


def test_get_download_subdir_uses_instagram_dir_for_instagram_videos():
    path = app.get_download_subdir("https://www.instagram.com/reel/abc123/", media_type="video")
    assert str(path).endswith("/downloads/instagram")


def test_resolve_download_mode_uses_ytdlp_for_instagram_video():
    assert app.resolve_download_mode("instagram", "https://cdninstagram.com/v/t50.mp4", media_type="video") == "ytdlp"


def test_extract_instagram_media_keeps_video_and_image_entries():
    meta = {
        "entries": [
            {
                "id": "video-1",
                "thumbnail": "https://cdninstagram.com/thumb1.jpg",
                "formats": [
                    {"url": "https://cdninstagram.com/v/360.mp4", "vcodec": "h264", "acodec": "aac", "width": 640, "height": 360, "tbr": 800},
                    {"url": "https://cdninstagram.com/v/720.mp4", "vcodec": "h264", "acodec": "aac", "width": 1280, "height": 720, "tbr": 1800},
                ],
                "thumbnails": [{"url": "https://cdninstagram.com/p/cover1.jpg", "width": 1280, "height": 720, "id": "cover"}],
            },
            {
                "id": "image-1",
                "thumbnail": "https://cdninstagram.com/p/photo1.jpg",
                "thumbnails": [{"url": "https://cdninstagram.com/p/photo1.jpg", "width": 1080, "height": 1350, "id": "orig"}],
            },
        ]
    }
    streams, options, images, image_options, media_entries = core.extract_instagram_media(meta)
    assert len(streams) == 1
    assert len(options) == 1
    assert streams[0].endswith('/720.mp4')
    assert len(images) == 2
    assert len(image_options) == 2
    assert len(media_entries) == 2
    assert media_entries[0]['media_type'] == 'video'
    assert media_entries[1]['media_type'] == 'image'


def test_discover_stream_marks_instagram_photo_post_as_image():
    original = core.extract_info_with_ytdlp
    try:
        core.extract_info_with_ytdlp = lambda *args, **kwargs: {
            "title": "图文帖",
            "thumbnails": [
                {"url": "https://cdninstagram.com/p/photo1.jpg", "width": 1080, "height": 1350, "id": "orig"},
                {"url": "https://cdninstagram.com/p/photo2.png", "width": 1080, "height": 1080, "id": "orig"},
            ],
        }
        info = core.discover_stream("https://www.instagram.com/p/abc123/")
    finally:
        core.extract_info_with_ytdlp = original

    assert info["media_type"] == "image"
    assert len(info["images"]) == 2
    assert info["streams"] == []

def test_discover_stream_instagram_photo_falls_back_to_html_when_ytdlp_reports_no_video(monkeypatch=None):
    html = """
    <html><head>
      <meta property="og:title" content="图片帖子" />
      <script type="application/ld+json">{
        "shortcode_media": {
          "__typename": "GraphImage",
          "id": "photo-1",
          "display_url": "https://scontent.cdninstagram.com/v/t51.2885-15/1.jpg?stp=dst-jpg_e35",
          "dimensions": {"width": 1080, "height": 1350}
        }
      }</script>
    </head></html>
    """
    old_fetch = core.fetch_webpage_html
    old_probe = core.probe_webpage
    old_extract = core.extract_info_with_ytdlp
    try:
        core.fetch_webpage_html = lambda *args, **kwargs: html
        core.probe_webpage = lambda *args, **kwargs: {"streams": [], "stream_options": [], "title": None}
        def fail_ytdlp(*args, **kwargs):
            raise RuntimeError("ERROR: [Instagram] abc: No video formats found!")
        core.extract_info_with_ytdlp = fail_ytdlp
        info = core.discover_stream("https://www.instagram.com/p/photo123/")
    finally:
        core.fetch_webpage_html = old_fetch
        core.probe_webpage = old_probe
        core.extract_info_with_ytdlp = old_extract

    assert info["media_type"] == "image"
    assert info["images"] == ["https://scontent.cdninstagram.com/v/t51.2885-15/1.jpg?stp=dst-jpg_e35"]
    assert info["streams"] == []
    assert info["extractor"] == "instagram-html"
    assert any("No video formats found" in err for err in info["errors"])



def test_download_all_instagram_mixed_media_creates_jobs_per_entry():
    old_load_config = app.load_config
    old_resolve_request_proxy = app.resolve_request_proxy
    old_resolve_site_cookies_path = app.resolve_site_cookies_path
    old_run_in_executor = app.run_in_executor
    old_discover_stream = app.discover_stream
    old_create_download_job = app.create_download_job

    captured = []
    info = {
        "title": "混合帖子",
        "media_type": "video",
        "streams": ["https://cdninstagram.com/v/720.mp4"],
        "media_entries": [
            {
                "media_index": 0,
                "media_type": "image",
                "images": ["https://cdninstagram.com/p/photo1.jpg"],
                "streams": [],
            },
            {
                "media_index": 1,
                "media_type": "video",
                "streams": ["https://cdninstagram.com/v/360.mp4", "https://cdninstagram.com/v/720.mp4"],
                "best_stream_url": "https://cdninstagram.com/v/720.mp4",
            },
        ],
        "images": ["https://cdninstagram.com/p/photo1.jpg"],
    }

    async def fake_run_in_executor(_executor, func, *args, **kwargs):
        return func(*args, **kwargs)

    def fake_create_download_job(payload, retry_of=None):
        captured.append(payload.model_dump())
        return {
            "id": f"job-{len(captured)}",
            "output": payload.output or f"job-{len(captured)}",
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

        payload = app.BatchDownloadPayload(url="https://www.instagram.com/p/abc123/")
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
    assert captured[0]["stream_url"] is None
    assert captured[1]["media_index"] == 1
    assert captured[1]["stream_url"] == "https://cdninstagram.com/v/720.mp4"
    assert captured[1]["stream_index"] == 1


def test_resolve_site_cookies_path_uses_instagram_cookie_config():
    cfg = {
        "instagramck": "/tmp/instagram.cookies.txt",
        "xck": "/tmp/twitter.cookies.txt",
    }
    assert app.resolve_site_cookies_path("https://www.instagram.com/p/abc123/", cfg) == "/tmp/instagram.cookies.txt"


def test_should_use_site_cookies_supports_instagram(tmp_path=None):
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        cookie_path = Path(d) / 'instagram.cookies.txt'
        cookie_path.write_text('# Netscape HTTP Cookie File\n', encoding='utf-8')
        assert app.should_use_site_cookies("https://www.instagram.com/reel/abc123/", str(cookie_path)) is True


def test_home_template_includes_asset_version_and_instagram_upload_entry():
    old_load_config = app.load_config
    old_list_recent_jobs = app.list_recent_jobs
    try:
        app.load_config = lambda: {}
        app.list_recent_jobs = lambda limit: []
        response = asyncio.run(app.home(type('Req', (), {})()))
    finally:
        app.load_config = old_load_config
        app.list_recent_jobs = old_list_recent_jobs

    body = response.body.decode('utf-8')
    assert f"/static/app.js?v={app.STATIC_ASSET_VERSION}" in body
    assert f"/static/style.css?v={app.STATIC_ASSET_VERSION}" in body
    assert '前端版本' in body
    assert app.APP_VERSION in body
    assert 'uploadInstagramCookies()' in body
    assert 'instagram_cookies_file' in body
    assert '上传 Instagram / IG 浏览器 cookies.txt' in body
    assert '旧缓存' in body
    assert body.index('xck（X / Twitter cookies 路径）') < body.index('instagramck（Instagram / IG cookies 路径）') < body.index('youtubeck（YouTube cookies 路径）')
    assert body.index('uploadTwitterCookies()') < body.index('uploadInstagramCookies()') < body.index('uploadYouTubeCookies()')


def test_home_sets_no_store_cache_headers():
    old_load_config = app.load_config
    old_list_recent_jobs = app.list_recent_jobs
    try:
        app.load_config = lambda: {}
        app.list_recent_jobs = lambda limit: []

        async def run():
            scope = {
                'type': 'http',
                'http_version': '1.1',
                'method': 'GET',
                'scheme': 'http',
                'path': '/',
                'raw_path': b'/',
                'query_string': b'',
                'root_path': '',
                'headers': [],
                'client': ('127.0.0.1', 12345),
                'server': ('testserver', 80),
                'state': {},
                'app': app.app,
            }
            request = app.Request(scope)
            return await app.add_no_store_for_shell(request, lambda req: app.home(req))

        response = asyncio.run(run())
    finally:
        app.load_config = old_load_config
        app.list_recent_jobs = old_list_recent_jobs

    assert response.headers['Cache-Control'] == 'no-store, no-cache, must-revalidate, max-age=0'
    assert response.headers['Pragma'] == 'no-cache'
    assert response.headers['Expires'] == '0'


if __name__ == "__main__":
    test_detect_platform_recognizes_instagram()
    test_get_download_subdir_uses_image_dir_for_instagram_images()
    test_get_download_subdir_uses_instagram_dir_for_instagram_videos()
    test_resolve_download_mode_uses_ytdlp_for_instagram_video()
    test_extract_instagram_media_keeps_video_and_image_entries()
    test_discover_stream_marks_instagram_photo_post_as_image()
    test_discover_stream_instagram_photo_falls_back_to_html_when_ytdlp_reports_no_video()
    test_download_all_instagram_mixed_media_creates_jobs_per_entry()
    test_resolve_site_cookies_path_uses_instagram_cookie_config()
    test_should_use_site_cookies_supports_instagram()
    test_home_template_includes_asset_version_and_instagram_upload_entry()
    test_home_sets_no_store_cache_headers()
    print("PASS: test_instagram_download")
