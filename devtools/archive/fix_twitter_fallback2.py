#!/usr/bin/env python3
import sys

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else '/root/.openclaw/workspace/mt-downloader/core.py'
    with open(path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    start_idx = None
    end_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith('def extract_twitter_fallback_streams('):
            start_idx = i
        if start_idx is not None and line.strip() == 'return dedupe_keep_order(cleaned)':
            end_idx = i
            break

    if start_idx is None or end_idx is None:
        print("function not found")
        return

    # Build replacement function lines (including final return line)
    new_func = [
        'def extract_twitter_fallback_streams(html: str) -> list[str]:\n',
        '    patterns = [\n',
        '        # video.twimg.com (传统视频域)\n',
        "        r'https?://video\\.twimg\\.com/[^\"\\'\\s>]+\\.(?:m3u8|mp4)(?:\\?[^\"\\'\\s>]*)?',\n",
        "        r'https?:\\\\/\\\\/video\\.twimg\\.com\\\\/.*?\\.(?:m3u8|mp4)(?:[^\"\\\\]*)?',\n",
        '        # pbs.twimg.com 的视频（e.g., /ext_tw_video/.../pu/vid/.../abcd.mp4）\n',
        "        r'https?://pbs\\.twimg\\.com/ext_tw_video/[^\"\\'\\s>]+\\.mp4(?:\\?[^\"\\'\\s>]*)?',\n",
        "        r'https?:\\\\/\\\\/pbs\\.twimg\\.com\\\\/ext_tw_video\\\\/.*?\\.mp4(?:[^\"\\\\]*)?',\n",
        '        # 通用 playbackUrl\n',
        "        r'\"playbackUrl\"\\s*:\\s*\"(https?:\\\\/\\\\/video\\.twimg\\.com\\\\/.*?(?:m3u8|mp4)(?:[^\"\\\\]*)?)\"',\n",
        '        # variants 块\n',
        "        r'\"video_info\".*?\"variants\"\\s*:\\s*\\[(.*?)\\]',\n",
        '    ]\n',
        '    found = []\n',
        '    for pat in patterns[:4]:\n',
        '        for match in re.findall(pat, html, re.IGNORECASE):\n',
        '            candidate = match if isinstance(match, str) else match[0]\n',
        "            candidate = candidate.replace('\\\\/', '/')\n",
        '            found.append(candidate)\n',
        '\n',
        '    variants_blocks = re.findall(patterns[4], html, re.IGNORECASE | re.DOTALL)\n',
        '    for block in variants_blocks:\n',
        '        for url in re.findall(r\'https?:\\\\/\\\\/video\\.twimg\\.com\\\\/.*?(?:m3u8|mp4)(?:[^\"\\\\]*)?\', block, re.IGNORECASE):\n',
        "            found.append(url.replace('\\\\/', '/'))\n",
        '\n',
        '    # 过滤有效视频域\n',
        '    cleaned = []\n',
        '    for candidate in found:\n',
        '        if not isinstance(candidate, str):\n',
        '            continue\n',
        "        if 'video.twimg.com/' in candidate or 'pbs.twimg.com/ext_tw_video/' in candidate:\n",
        '            cleaned.append(candidate)\n',
        '    return dedupe_keep_order(cleaned)\n',
    ]

    # Ensure we keep one blank line after function (like original had two before next def)
    # We'll add one extra blank line
    new_lines = lines[:start_idx] + new_func + ['\n'] + lines[end_idx+1:]
    # Write back
    with open(path, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
    print(f"replaced lines {start_idx+1}-{end_idx+1} with new function")

if __name__ == '__main__':
    main()
