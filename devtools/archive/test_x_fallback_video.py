#!/usr/bin/env python3
import sys
sys.path.insert(0, '/root/.openclaw/workspace/mt-downloader')
import core

def test_video_twimg_fallback():
    html = '''
    <html>
    <body>
    <video src="https://video.twimg.com/amplify_video/12345/vid/640x360/abc123.mp4?tag=1"></video>
    </body>
    </html>
    '''
    urls = core.extract_twitter_fallback_streams(html)
    print(urls)
    assert any('video.twimg.com/' in u for u in urls), f"video.twimg.com not found, got {urls}"
    print({"ok": True, "urls": urls})

if __name__ == '__main__':
    test_video_twimg_fallback()
