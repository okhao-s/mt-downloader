#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

from core import discover_stream, ffmpeg_download, normalize_filename


def main():
    parser = argparse.ArgumentParser(description="Download non-DRM m3u8/HLS videos from a page or direct URL")
    parser.add_argument("--url", required=True, help="Web page URL or direct m3u8 URL")
    parser.add_argument("--output", default="output.mp4", help="Output filename")
    parser.add_argument("--dir", default="/downloads", help="Output directory inside container")
    parser.add_argument("--referer", default=None, help="Optional Referer header")
    parser.add_argument("--user-agent", default=None, help="Optional User-Agent header")
    parser.add_argument("--cookies", default=None, help="Optional cookies.txt path")
    parser.add_argument("--proxy", default=None, help="Optional HTTP/HTTPS proxy URL")
    args = parser.parse_args()

    info = discover_stream(args.url, args.referer, args.user_agent, args.cookies, args.proxy)
    stream_url = info.get("resolved_url")
    if not stream_url:
        print("[x] no m3u8 stream found. Try passing the direct .m3u8 URL.", flush=True)
        sys.exit(2)

    output_path = Path(args.dir) / normalize_filename(args.output)
    try:
        ffmpeg_download(stream_url, output_path, args.referer, args.user_agent, args.proxy)
    except Exception as e:
        print(f"[x] ffmpeg download failed: {e}", flush=True)
        sys.exit(1)

    print(f"[ok] saved: {output_path}", flush=True)


if __name__ == "__main__":
    main()
