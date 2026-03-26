# mt-downloader

<div align="center">

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-Web_UI-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![FFmpeg](https://img.shields.io/badge/FFmpeg-HLS%20Download-007808?style=for-the-badge&logo=ffmpeg&logoColor=white)
![yt-dlp](https://img.shields.io/badge/yt--dlp-X%2FTwitter-FFCC00?style=for-the-badge)
![License](https://img.shields.io/badge/License-MIT-black?style=for-the-badge)

**A practical web downloader for m3u8 / HLS pages and X(Twitter) videos**

支持网页解析、HLS 预览、下载队列、失败重试、历史记录、X/Twitter cookies 上传与最高画质下载。

</div>

---

## What it does

`mt-downloader` 是一个偏实用主义的 Web 下载面板，目标不是做花哨媒体中心，而是把这些事干明白：

- 输入网页地址，尽量解析出真实 m3u8 / HLS 流
- 支持页面内预览 HLS
- 支持下载队列、失败重试、历史记录
- 对 **X / Twitter** 链接走专门逻辑：
  - 支持 `yt-dlp` 解析
  - 支持上传浏览器导出的 `cookies.txt`
  - 自动过滤纯音频假流
  - 默认只使用 **最高画质**

> ⚠️ This project is intended only for content you are authorized to access.
> It does **not** bypass DRM such as Widevine / FairPlay / PlayReady.

---

## Current feature set

### HLS / m3u8

- 输入普通网页地址或直接输入 `.m3u8`
- 解析后页面内预览 HLS
- 支持 Referer / User-Agent / Proxy
- 支持直接下载 HLS 视频

### X / Twitter

- 支持 `x.com` / `twitter.com` 视频链接解析
- 集成 `yt-dlp`
- 支持上传浏览器导出的 `cookies.txt`
- 自动带 cookies 参与解析 / 下载
- 自动过滤纯音频 HLS 变体
- 页面只显示 **最高画质**
- 下载时默认也走 **最高画质**，不是只在 UI 上看起来像最高

### Task system

- 下载中 / 等待中 / 失败 / 已完成 / 已取消 / 已重试 分类展示
- 支持自动重试与手动重试
- 支持历史任务清理
- 任务时间会显示为：
  - `已运行 xx`
  - `已等待 xx`
  - `xx前 · 耗时 xx`

---

## Quick Start

### 1. Prepare download directory

```bash
mkdir -p /root/docker/video
```

### 2. Run with Docker Compose

```bash
cd /root/docker/mt-downloader
docker compose pull
docker compose up -d
```

默认端口映射：

- Host: `9151`
- Container: `8080`

打开：

```text
http://<your-server-ip>:9151
```

---

## Docker Compose example

```yaml
services:
  m3u8-downloader:
    container_name: Mt
    image: okhao/mt:latest
    ports:
      - "9151:8080"
    volumes:
      - /root/docker/video:/downloads
      - ./data:/app/data
    restart: unless-stopped
```

如果你要从源码本地构建开发版：

```yaml
services:
  m3u8-downloader:
    container_name: Mt
    build: .
    image: okhao/mt:dev
    ports:
      - "9151:8080"
    volumes:
      - /root/docker/video:/downloads
      - ./data:/app/data
    restart: unless-stopped
```

---

## Docker image

推荐直接拉：

```bash
docker pull okhao/mt:latest
```

运行：

```bash
docker run -d \
  --name Mt \
  -p 9151:8080 \
  -v /root/docker/video:/downloads \
  -v $(pwd)/data:/app/data \
  okhao/mt:latest
```

---

## X / Twitter cookies usage

某些 X / Twitter 视频游客模式下可能拿不到正确视频流，这时建议上传浏览器导出的 `cookies.txt`。

### In UI

1. 打开设置面板
2. 找到 **上传浏览器导出的 cookies.txt**
3. 选择文件并上传
4. 重新解析 X / Twitter 链接

### Saved location

容器内默认保存到：

```text
/app/data/cookies/twitter.cookies.txt
```

对应宿主机通常是：

```text
/root/docker/mt-downloader/data/cookies/twitter.cookies.txt
```

---

## Configuration

配置文件默认保存在：

```text
/app/data/config.json
```

常见映射：

```text
/root/docker/mt-downloader/data/config.json
```

当前主要配置项：

- `default_proxy`
- `auto_retry_enabled`
- `auto_retry_delay_seconds`
- `auto_retry_max_attempts`
- `twitter_cookies_path`

---

## Download output

下载的视频默认保存到：

```text
/root/docker/video
```

---

## Notes / behavior details

- **不支持 DRM** 视频
- 某些站点需要 Referer / User-Agent / Proxy 配合
- X / Twitter 当前策略是：
  - 解析时识别多档视频流
  - 页面只展示最高画质
  - 下载时也默认使用最高画质
- 如果页面能解析但下载结果异常，优先检查：
  - cookies 是否有效
  - 目标视频是否对当前账号可见
  - 代理链路是否稳定
  - 浏览器是否还缓存着旧前端资源（可强刷 `Ctrl+F5`）

---

## Project structure

```text
.
├── app.py
├── core.py
├── templates/
├── static/
├── data/
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh
└── README.md
```

---

## Security

- 不要把这个面板直接裸露到公网
- 建议放在内网、VPN、Tailscale 或反向代理鉴权后面
- 不要把 cookies、token、私钥、`.env` 等敏感内容提交到仓库

---

## License

Released under the [MIT License](LICENSE).
