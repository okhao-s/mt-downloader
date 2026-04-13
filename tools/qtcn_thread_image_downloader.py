#!/usr/bin/env python3
import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests
from playwright.async_api import async_playwright

DEFAULT_URL = "https://qtcn.4c1p0.com/forum.php?mod=viewthread&tid=3420892&extra=page%3D1"
DEFAULT_BASE_DIR = "/root/docker/mt-downloader/data/qtcn_thread_downloads"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)


def sanitize_filename(name: str, limit: int = 120) -> str:
    name = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", name).strip(" ._")
    return (name or "thread")[:limit]


def thread_id_from_url(url: str) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    tid = qs.get("tid", [None])[0]
    if tid:
        return tid
    m = re.search(r"thread-(\d+)-", parsed.path)
    return m.group(1) if m else "unknown"


def dedupe_keep_order(items):
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def extract_title(html: str) -> str:
    m = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
    if not m:
        return "thread"
    title = re.sub(r"\s+", " ", m.group(1)).strip()
    title = re.sub(r"\s*-\s*原创自拍区\s*-.*$", "", title)
    title = re.sub(r"\s*-\s*98堂.*$", "", title)
    return title or "thread"


def extract_image_urls(html: str):
    urls = []
    for pattern in [r'zoomfile="([^"]+)"', r'file="([^"]+)"', r'<a href="([^"]+)"[^>]*>下载附件</a>']:
        urls.extend(re.findall(pattern, html, re.I))
    urls = [u.strip() for u in urls if u.strip().startswith("http")]
    return dedupe_keep_order(urls)


async def fetch_thread_page(url: str, chrome_path: str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            executable_path=chrome_path,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=120000)
        try:
            await page.locator("a.enter-btn").first.click(timeout=5000)
            await page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        html = await page.content()
        final_url = page.url
        cookies = await context.cookies()
        await browser.close()
        return html, final_url, cookies


def download_images(urls, referer: str, out_dir: Path, cookie_header: str = ""):
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Referer": referer,
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    })
    if cookie_header:
        session.headers["Cookie"] = cookie_header

    results = []
    total_bytes = 0
    for idx, url in enumerate(urls, 1):
        ext = Path(urlparse(url).path).suffix or ".bin"
        filename = f"{idx:02d}{ext.lower()}"
        target = out_dir / filename
        with session.get(url, stream=True, timeout=120, allow_redirects=True) as resp:
            resp.raise_for_status()
            size = 0
            with open(target, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 256):
                    if not chunk:
                        continue
                    f.write(chunk)
                    size += len(chunk)
        total_bytes += size
        results.append({"index": idx, "url": url, "file": str(target), "bytes": size})
        print(f"[{idx}/{len(urls)}] OK {target.name} {size} bytes", flush=True)
    return results, total_bytes


def main():
    parser = argparse.ArgumentParser(description="下载 Discuz 帖子里的图片/GIF，自动处理 qtcn 安全页。")
    parser.add_argument("url", nargs="?", default=DEFAULT_URL, help="帖子 URL")
    parser.add_argument("--base-dir", default=DEFAULT_BASE_DIR, help="输出根目录")
    parser.add_argument("--chrome-path", default=os.environ.get("CHROME_PATH", "/usr/bin/google-chrome"))
    args = parser.parse_args()

    tid = thread_id_from_url(args.url)
    work_dir = Path(args.base_dir) / tid
    raw_dir = work_dir / "files"
    raw_dir.mkdir(parents=True, exist_ok=True)

    html, final_url, cookies = asyncio.run(fetch_thread_page(args.url, args.chrome_path))
    title = extract_title(html)
    urls = extract_image_urls(html)
    if not urls:
        print("没提取到图片直链，退出。", file=sys.stderr)
        sys.exit(2)

    cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies if c.get("name") and c.get("value"))

    (work_dir / "thread.html").write_text(html, encoding="utf-8")
    (work_dir / "image_urls.txt").write_text("\n".join(urls) + "\n", encoding="utf-8")

    download_results, total_bytes = download_images(urls, final_url, raw_dir, cookie_header=cookie_header)

    summary = {
        "source_url": args.url,
        "final_url": final_url,
        "thread_id": tid,
        "title": title,
        "image_count": len(urls),
        "total_bytes": total_bytes,
        "cookies": [{"name": c.get("name"), "domain": c.get("domain")} for c in cookies],
        "downloads": download_results,
    }
    (work_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    safe_title = sanitize_filename(title)
    latest_link = Path(args.base_dir) / f"latest_{tid}_{safe_title}"
    try:
        if latest_link.exists() or latest_link.is_symlink():
            latest_link.unlink()
        latest_link.symlink_to(work_dir)
    except Exception:
        pass

    print("=== DONE ===")
    print(f"title: {title}")
    print(f"thread_id: {tid}")
    print(f"images: {len(urls)}")
    print(f"saved_dir: {work_dir}")
    print(f"files_dir: {raw_dir}")
    print(f"total_bytes: {total_bytes}")


if __name__ == "__main__":
    main()
