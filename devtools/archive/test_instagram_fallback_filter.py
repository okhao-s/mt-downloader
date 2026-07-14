#!/usr/bin/env python3
import sys
sys.path.insert(0, '/root/.openclaw/workspace/mt-downloader')
import core

def test_fallback_dedup_and_filter():
    # Test case 1: og:image present -> only that
    html1 = '''
    <html>
    <head>
        <meta property="og:image" content="https://scontent.cdninstagram.com/v/t51.2885-15/1234_1.jpg?stp=dst-jpg_e35" />
    </head>
    <body>
        <img src="https://static.cdninstagram.com/rsrc.php/yz/r/ZTBx7zo74LV.webp" />
        <img src="https://scontent.cdninstagram.com/v/t51.2885-15/1234_2.jpg?stp=dst-jpg_e35" />
    </body>
    </html>
    '''
    result1 = core.extract_instagram_images_from_html(html1)
    print("Test1 - og:image only")
    print("images count:", len(result1['images']))
    print("images:", result1['images'])
    assert len(result1['images']) == 1, f"expected 1, got {len(result1['images'])}"

    # Test case 2: no og:image, many static and cdn images
    html2 = '''
    <html><body>
        <img src="https://static.cdninstagram.com/rsrc.php/yz/r/ZTBx7zo74LV.webp" />
        <img src="https://static.cdninstagram.com/rsrc.php/yn/r/R5c1GwJk_n2.webp" />
        <img src="https://scontent.cdninstagram.com/v/t51.2885-15/5678_1.jpg?stp=dst-jpg_e35" />
        <img src="https://scontent.cdninstagram.com/v/t51.2885-15/5678_1.jpg?stp=dst-jpg_e35" />
        <img src="https://scontent.cdninstagram.com/v/t51.2885-15/5678_2.jpg?stp=dst-jpg_e35" />
    </body></html>
    '''
    result2 = core.extract_instagram_images_from_html(html2)
    print("\nTest2 - no og:image, fallback with dedupe and static filter")
    print("images count:", len(result2['images']))
    print("images:", result2['images'])
    # Should be 2 distinct scontent bases (5678_1 and 5678_2), static filtered out
    assert len(result2['images']) == 2, f"expected 2, got {len(result2['images'])}"
    print("All tests passed!")

if __name__ == '__main__':
    test_fallback_dedup_and_filter()
