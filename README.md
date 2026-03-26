# mt-downloader

<div align="center">

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-Web_UI-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![FFmpeg](https://img.shields.io/badge/FFmpeg-HLS%20Download-007808?style=for-the-badge&logo=ffmpeg&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-black?style=for-the-badge)

**A focused web downloader for m3u8 / HLS streams**

支持网页解析、m3u8 预览、代理、下载队列、失败重试、历史记录。

</div>

---

## Features

- 🔎 **Page / stream parsing**: 输入网页地址或直接输入 m3u8 链接
- ▶️ **In-browser preview**: 解析后可直接在页面内预览 HLS
- 🌐 **Proxy support**: 支持 HTTP / HTTPS / SOCKS5 代理
- 🧾 **Custom headers**: 可自定义 Referer 和 User-Agent
- 📥 **Task queue**: 下载中 / 等待中 / 失败 / 已完成 分栏查看
- 🔁 **Auto retry**: 支持失败后自动重试与手动重试
- 📚 **History panel**: 保留近期下载任务记录
- 🐳 **Docker ready**: 开箱即用，适合直接部署到 Linux 服务器

---

## What it is for

适合这些场景：

- 从普通网页中提取真实 m3u8 / HLS 流地址
- 对非 DRM 的 HLS 视频做预览和下载
- 在服务器上跑一个简单直观的 Web 下载面板

> ⚠️ This project is intended only for content you are authorized to access.
> It does **not** bypass DRM such as Widevine / FairPlay / PlayReady.

---

## Screens / UI Highlights

- 左侧输入链接、Referer、User-Agent、代理
- 中间直接预览 HLS 视频
- 右侧看下载队列、失败任务、历史记录
- 支持默认代理与自动重试配置持久化

如果你准备做得更花，可以后续在 README 里加真实截图。

---

## Quick Start

### 1. Prepare download directory

```bash
mkdir -p /root/docker/video
```

### 2. Run with Docker Compose

```bash
cd /root/docker/mt-downloader
docker compose up -d --build
```

默认端口映射：

- Host: `9151`
- Container: `8080`

打开：

```text
http://<your-server-ip>:9151
```

---

## Docker Compose

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

## Local Development

### Build image

```bash
docker build -t okhao/mt:dev .
```

### Run container

```bash
docker run -d \
  --name Mt \
  -p 9151:8080 \
  -v /root/docker/video:/downloads \
  -v $(pwd)/data:/app/data \
  okhao/mt:dev
```

---

## Configuration

配置文件默认保存在：

```text
/app/data/config.json
```

宿主机映射通常为：

```text
/root/docker/mt-downloader/data/config.json
```

当前支持的核心配置项：

- `default_proxy`
- `auto_retry_enabled`
- `auto_retry_delay_seconds`
- `auto_retry_max_attempts`

---

## Download output

下载的视频默认保存到：

```text
/root/docker/video
```

---

## Project structure

```text
.
├── app.py
├── core.py
├── download.py
├── templates/
├── static/
├── data/
├── Dockerfile
├── docker-compose.yml
└── entrypoint.sh
```

---

## Notes

- 不支持 DRM 视频
- 某些站点可能需要代理 + Referer + User-Agent 一起配合
- 某些站点的真实流地址可能在动态接口里，网页地址不一定每次都能直接解析成功
- 如果页面能播但服务端下载失败，优先检查源站风控、分片可访问性、代理链路和 ffmpeg 错误输出

---

## Security

- 不要把这个面板直接暴露到公网
- 不要把 token、数据库、私钥、`.env` 之类内容提交到仓库
- 建议放在内网、VPN、Tailscale 或反向代理鉴权后面使用

---

## License

Released under the [MIT License](LICENSE).
