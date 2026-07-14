#!/usr/bin/env python3
import re, sys

def replace_twitter_fallback_streams(content: str) -> str:
    # 匹配整个函数
    pattern = r'(def extract_twitter_fallback_streams\(html: str\) -> list\[str\]:\s*patterns = \[[^\]]+\][^\n]*\n(?:.*?\n)*?    return dedupe_keep_order\(cleaned\))'
    new_body = '''def extract_twitter_fallback_streams(html: str) -> list[str]:
    patterns = [
        # video.twimg.com (传统视频域)
        r'https?://video\\.twimg\\.com/[^"\\'\\s>]+\\.(?:m3u8|mp4)(?:\\?[^"\\'\\s>]*)?',
        r'https?:\\\\/\\\\/video\\.twimg\\.com\\\\/.*?\\.(?:m3u8|mp4)(?:[^"\\\\]*)?',
        # pbs.twimg.com 的视频（e.g., /ext_tw_video/.../pu/vid/.../abcd.mp4）
        r'https?://pbs\\.twimg\\.com/ext_tw_video/[^"\\'\\s>]+\\.mp4(?:\\?[^"\\'\\s>]*)?',
        r'https?:\\\\/\\\\/pbs\\.twimg\\.com\\\\/ext_tw_video\\\\/.*?\\.mp4(?:[^"\\\\]*)?',
        # 通用 playbackUrl
        r'"playbackUrl"\\s*:\\s*"(https?:\\\\/\\\\/video\\.twimg\\.com\\\\/.*?(?:m3u8|mp4)(?:[^"\\\\]*)?)"',
        # variants 块
        r'"video_info".*?"variants"\\s*:\\s*\\[(.*?)\\]',
    ]
    found = []
    for pat in patterns[:4]:
        for match in re.findall(pat, html, re.IGNORECASE):
            candidate = match if isinstance(match, str) else match[0]
            candidate = candidate.replace('\\\\/', '/')
            found.append(candidate)

    variants_blocks = re.findall(patterns[4], html, re.IGNORECASE | re.DOTALL)
    for block in variants_blocks:
        for url in re.findall(r'https?:\\\\/\\\\/video\\.twimg\\.com\\\\/.*?(?:m3u8|mp4)(?:[^"\\\\]*)?', block, re.IGNORECASE):
            found.append(url.replace('\\\\/', '/'))

    # 过滤有效视频域
    cleaned = []
    for candidate in found:
        if not isinstance(candidate, str):
            continue
        if 'video.twimg.com/' in candidate or 'pbs.twimg.com/ext_tw_video/' in candidate:
            cleaned.append(candidate)
    return dedupe_keep_order(cleaned)'''
    return re.sub(pattern, new_body, content, count=1, flags=re.DOTALL)

if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else '/root/.openclaw/workspace/mt-downloader/core.py'
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    new_content = replace_twitter_fallback_streams(content)
    if new_content != content:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print("patched")
    else:
        print("no change")
