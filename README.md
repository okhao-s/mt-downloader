# mt-downloader

轻量、实用、能落地的视频下载面板。  
专注把 **m3u8 / HLS、X(Twitter)、YouTube、Bilibili、Douyin(抖音)** 这几类常见场景稳定做好，不把项目做成什么都想支持、最后什么都半残的“万能下载器”。

---

## 特性

- 支持 **m3u8 / HLS** 页面解析、预览、下载
- 支持 **X / Twitter** 视频解析与下载
- 支持 **YouTube** 轻量下载
- 支持 **Bilibili** 视频解析与下载
- 支持 **Douyin / 抖音** 视频解析与下载
- 支持 **cookies 上传**（X / YouTube / Bilibili 分开管理）
- 支持 **任务队列、重试、历史记录**
- 支持 **中文简洁下载进度**
- 下载结果按类型自动分目录：
  - `/downloads/m3u8`
  - `/downloads/x`
  - `/downloads/youtube`
  - `/downloads/bilibili`
  - `/downloads/douyin`

---

## 支持范围

### m3u8 / HLS

- 输入网页地址或直接输入 `.m3u8`
- 自动尝试提取真实视频流
- 支持页面内预览 HLS
- 支持 Referer / User-Agent / Proxy
- 支持 ffmpeg 下载

### X / Twitter

- 支持 `x.com` / `twitter.com`
- 默认只显示 **最高画质**
- 下载时也默认按 **最高画质** 走
- 对特殊样本支持后端 fallback：
  - HTML fallback
  - 登录态 GraphQL fallback
- `.mp4` 直链自动走直连下载，不误走 HLS
- 支持上传浏览器导出的 `cookies.txt`

### YouTube

- 通过 `yt-dlp` 旁路支持
- 默认只显示 **最高画质**
- 下载统一强制输出 **MP4**
- 支持独立的 YouTube `cookies.txt`
- 遇到脏 cookies / 风控异常时，会自动回退无 cookies 重试
- 不污染原有 `m3u8 / ffmpeg` 主链

### Bilibili

- 独立站点分流接入
- 支持解析非 m3u8 视频流，不再只盯 HLS
- 支持独立的 Bilibili `cookies.txt`
- 下载结果单独落到 `/downloads/bilibili`

### Douyin / 抖音

- 当前优先走 **移动分享页源码解析**，不依赖 PC Web detail 空接口
- 从 `iesdouyin / m.douyin` 分享页源码提取 `video_id / play_addr`
- 优先使用 `/aweme/v1/play/` 直链，必要时回退 `/playwm/`
- 下载阶段继续复用 `yt-dlp` 下载直链，不额外引入重型浏览器依赖
- 下载结果单独落到 `/downloads/douyin`

---

## 设计思路

这个项目现在是三条链路分开处理，不是乱炖：

- **m3u8 / HLS** → 原主链
- **X / Twitter** → X 专用旁路 / fallback
- **YouTube** → `yt-dlp` 专线
- **Bilibili** → 独立站点分流
- **Douyin / 抖音** → 移动分享页源码解析 + `yt-dlp` 直链下载

这么做就一个原因：

> 别为了多支持一个站，把原来稳定链路一起搞炸。

---

## 快速开始

### Docker Compose

```bash
cd /root/docker/mt-downloader
docker compose pull
docker compose up -d
```

默认访问：

```text
http://<your-server-ip>:9151
```

### 推荐的 `docker-compose.yml`

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

---

## 直接运行 Docker 镜像

```bash
docker pull okhao/mt:latest

docker run -d \
  --name Mt \
  -p 9151:8080 \
  -v /root/docker/video:/downloads \
  -v $(pwd)/data:/app/data \
  okhao/mt:latest
```

---

## 下载目录

容器内会自动按类型分目录：

```text
/downloads/m3u8
/downloads/x
/downloads/youtube
/downloads/bilibili
```

如果宿主机挂载的是：

```text
/root/docker/video:/downloads
```

那实际文件会保存到：

```text
/root/docker/video/m3u8
/root/docker/video/x
/root/docker/video/youtube
/root/docker/video/bilibili
```

---

## 下载进度

页面显示的是中文简洁版进度，不再直接甩原始工具输出。

典型状态：

- `排队中 · 当前下载槽位 1/3`
- `开始下载 · 当前下载槽位 1/3`
- `开始抓取视频 · 当前下载槽位 1/3`
- `已下载 37.2% · 总大小 229.20MiB · 速度 2.24MiB/s · 剩余 00:30`
- `正在合并并转成 MP4`
- `下载完成`

---

## Cookies

某些 X / YouTube 视频在游客态下拿不到可下载流，这时需要 cookies。

### 默认保存位置

```text
/app/data/cookies/twitter.cookies.txt
/app/data/cookies/youtube.cookies.txt
/app/data/cookies/bilibili.cookies.txt
```

### 宿主机常见路径

```text
/root/docker/mt-downloader/data/cookies/twitter.cookies.txt
/root/docker/mt-downloader/data/cookies/youtube.cookies.txt
/root/docker/mt-downloader/data/cookies/bilibili.cookies.txt
```

### 分流规则

- X / Twitter → 使用 `twitter.cookies.txt`
- YouTube → 使用 `youtube.cookies.txt`
- Bilibili → 使用 `bilibili.cookies.txt`

不会混着乱用。

---

## 配置文件

默认配置文件：

```text
/app/data/config.json
```

宿主机常见映射：

```text
/root/docker/mt-downloader/data/config.json
```

常见配置项：

- `default_proxy`
- `auto_retry_enabled`
- `auto_retry_delay_seconds`
- `auto_retry_max_attempts`
- `xck`
- `youtubeck`
- `bilibilick`

兼容旧字段：

- `twitter_cookies_path`
- `youtube_cookies_path`
- `bilibili_cookies_path`

---

## 本地开发注意事项

这里有个非常容易踩的坑：

**当前 `docker-compose.yml` 使用的是 `image: okhao/mt:latest`，不是 `build:`。**

所以你改完本地源码后，如果只跑：

```bash
docker compose up -d
```

**代码不会自动生效。**

本地开发正确姿势：

```bash
cd /root/docker/mt-downloader
docker build -t okhao/mt:latest .
docker compose up -d --force-recreate
```

很多“我都改了为什么没变化”，本质上就是这里翻车。

---

## 任务系统

支持：

- 排队中
- 下载中
- 已完成
- 失败
- 已取消
- 手动重试 / 自动重试
- 历史记录清理

页面会尽量把底层状态翻译成人话，不把内部日志直接糊到 UI 上。

---

## 项目边界

### 适合

- 自己部署一个轻量下载面板
- 处理常见的 m3u8 视频页
- 下载 X / Twitter 视频
- 轻量支持 YouTube

### 不打算做

- 通用全站解析器
- DRM 绕过工具
- 什么站都支持的万能下载器
- 花里胡哨的媒体中心

---

## 已知说明

- **不支持 DRM**（Widevine / FairPlay / PlayReady 之类别想了）
- 某些站点必须带 Referer / User-Agent / Proxy 才能解析
- 某些 X / YouTube 视频必须依赖 cookies
- 如果前端改完后页面看起来没更新，先试：

```text
Ctrl + F5
```

或者直接开无痕窗口。很多“没生效”其实只是浏览器缓存发病。

---

## 目录结构

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

## 安全建议

别裸奔到公网。

建议至少满足下面一条：

- 放内网
- 挂到 VPN / Tailscale 后面
- 走反向代理并加鉴权

另外：

- 不要把 cookies 提交到仓库
- 不要把 token、私钥、`.env` 等敏感信息外泄
- 有代理配置时，注意别把敏感流量乱转发

---

## License

MIT
