#!/usr/bin/env python3
import sys
sys.path.insert(0, '/root/.openclaw/workspace/mt-downloader')
import core
from unittest.mock import patch

def fake_ytdlp_raises(url, *args, **kwargs):
    raise RuntimeError("ERROR: [Instagram] No video formats found!")

def fake_fetch_html(url, referer=None, user_agent=None, proxy=None):
    # HTML 中包含 og:image 标签
    return """
    <html><head>
      <meta property="og:title" content="Just a photo" />
      <meta property="og:image" content="https://scontent.cdninstagram.com/v/t51.2885-15/12345_1.jpg?stp=dst-jpg_e35" />
    </head><body>Photo</body></html>
    """

def test_instagram_image_fallback():
    with patch.object(core, 'extract_info_with_ytdlp_flare', side_effect=fake_ytdlp_raises):
        with patch.object(core, 'fetch_webpage_html', side_effect=fake_fetch_html):
            url = "https://www.instagram.com/p/ABC123/"
            info = core._discover_stream_uncached(url)
            assert info['media_type'] == 'image', f"expected image, got {info['media_type']}"
            assert len(info['images']) == 1, f"expected 1 image, got {len(info['images'])}"
            assert 'scontent.cdninstagram.com' in info['images'][0], f"unexpected image URL: {info['images']}"
            print({"ok": True, "media_type": info['media_type'], "images": info['images'], "extractor": info.get('extractor')})

if __name__ == '__main__':
    test_instagram_image_fallback()
