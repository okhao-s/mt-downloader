# QTCN 帖子图片下载

这个目录下的脚本用于直接下载帖子正文里的图片/GIF，自动：

1. 用 Playwright 打开帖子
2. 自动点击 18+ 安全页的进入按钮，拿到真实帖子 HTML
3. 从正文里提取 `zoomfile/file/下载附件` 的真实图片直链
4. 带 `Referer` 批量下载到本地

## 一键运行

```bash
cd /root/docker/mt-downloader
python3 tools/qtcn_thread_image_downloader.py 'https://qtcn.4c1p0.com/forum.php?mod=viewthread&tid=3420892&extra=page%3D1'
```

默认输出目录：

```text
/root/docker/mt-downloader/data/qtcn_thread_downloads/<tid>/
```

下载结果包括：

- `files/`：实际下载下来的图片/GIF
- `image_urls.txt`：提取出的图片直链列表
- `thread.html`：抓到的帖子 HTML
- `summary.json`：下载汇总

## 说明

- 当前站点的 `_safe` cookie 由脚本自动处理，不需要手工填。
- 如果以后站点安全页逻辑变了，脚本仍会先走浏览器再提取，不依赖手抄 cookie。
- 如果机器上 Chrome 路径不同，可传：

```bash
python3 tools/qtcn_thread_image_downloader.py <url> --chrome-path /path/to/chrome
```
