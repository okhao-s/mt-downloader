import json

import app
import core


def main():
    photo_html = """
    <html><head>
      <meta property="og:title" content="图片帖子" />
      <meta property="og:image" content="https://scontent.cdninstagram.com/v/t51.2885-15/1.jpg?stp=dst-jpg_e35" />
    </head></html>
    """
    shell_html = "<html><head><title>Instagram</title></head><body>shell</body></html>"

    old_fetch = core.fetch_webpage_html
    old_probe = core.probe_webpage
    old_extract = core.extract_info_with_ytdlp
    old_submit = app.download_executor.submit
    try:
        def fake_fetch(url, referer=None, user_agent=None, proxy=None):
            if user_agent and 'iPhone' in user_agent:
                return photo_html
            return shell_html

        def fail_ytdlp(*args, **kwargs):
            raise RuntimeError('ERROR: [Instagram] DW80LEUgeJ-: No video formats found!')

        core.fetch_webpage_html = fake_fetch
        core.probe_webpage = lambda *args, **kwargs: {'streams': [], 'stream_options': [], 'title': None}
        core.extract_info_with_ytdlp = fail_ytdlp
        app.download_executor.submit = lambda *args, **kwargs: None

        info = core._discover_stream_uncached('https://www.instagram.com/p/DW80LEUgeJ-/')
        assert info['media_type'] == 'image', info
        assert info['streams'] == [], info
        assert info['images'] == ['https://scontent.cdninstagram.com/v/t51.2885-15/1.jpg?stp=dst-jpg_e35'], info
        assert info['extractor'] == 'instagram-html', info
        assert any('No video formats found' in err for err in info['errors']), info

        payload = type('P', (), {
            'url': 'https://www.instagram.com/p/DW80LEUgeJ-/',
            'output': None,
            'referer': None,
            'user_agent': None,
            'proxy': None,
            'stream_url': None,
            'stream_index': None,
            'media_index': None,
            'wecom_to_user': None,
            'model_dump': lambda self: {
                'url': self.url, 'output': self.output, 'referer': self.referer, 'user_agent': self.user_agent,
                'proxy': self.proxy, 'stream_url': self.stream_url, 'stream_index': self.stream_index,
                'media_index': self.media_index, 'wecom_to_user': self.wecom_to_user,
            }
        })()
        job = app.create_download_job(payload)
        assert job['media_type'] == 'image', job
        assert job['download_via'] == 'image', job
        assert job['image_count'] == 1, job
        assert str(job['download_dir']).endswith('/downloads/image'), job
        print(json.dumps({'ok': True, 'discover_media_type': info['media_type'], 'job_media_type': job['media_type'], 'download_via': job['download_via']}, ensure_ascii=False))
    finally:
        core.fetch_webpage_html = old_fetch
        core.probe_webpage = old_probe
        core.extract_info_with_ytdlp = old_extract
        app.download_executor.submit = old_submit


if __name__ == '__main__':
    main()
