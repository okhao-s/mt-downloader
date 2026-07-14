#!/usr/bin/env python3
import sys
sys.path.insert(0, '/root/.openclaw/workspace/mt-downloader')
import core

def test_pbs_twimg_fallback():
    html = '''
    <html>
    <script>
    var videoUrl = "https://pbs.twimg.com/ext_tw_video/1234567890123456789/pu/vid/1280x720/abcd1234.mp4?tag=12";
    </script>
    </html>
    '''
    urls = core.extract_twitter_fallback_streams(html)
    print(urls)
    assert len(urls) >= 1, "expected at least one video url"
    assert any('pbs.twimg.com/ext_tw_video/' in u for u in urls), f"pbs url not found, got {urls}"
    print({"ok": True, "urls": urls})

if __name__ == '__main__':
    test_pbs_twimg_fallback()
