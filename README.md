# mt-downloader

mt-downloader 是一个面向自托管场景的轻量下载面板，用于统一处理常见在线视频链接的解析、预览、排队下载与结果归档。

当前实现聚焦于几类高频链路：HLS / m3u8、X / Twitter、YouTube、Bilibili、Douyin（抖音）。项目目标不是覆盖所有站点，而是在有限范围内保持部署简单、依赖可控、链路稳定。

## Summary

- 提供 Web 界面，支持链接解析、媒体预览、任务下载与历史查看
- 支持按平台分流处理：HLS、X / Twitter、YouTube、Bilibili、Douyin
- 支持任务队列、失败重试、取消、历史清理
- 支持按站点分别配置 cookies 路径
- 支持企业微信自建应用回调接入，用于通过消息触发下载并接收状态通知
- 适合部署为单容器服务，不依赖浏览器自动化或额外多容器编排

## Features

### Core capabilities

- 输入网页链接或媒体链接后执行解析
- 对可预览的 HLS 内容生成预览播放地址
- 创建后台下载任务并展示实时进度
- 下载完成后按平台自动落盘到不同目录
- 支持 Referer、User-Agent、代理配置
- 支持自动重试、手动重试、任务取消、历史记录管理

### Supported platforms

当前代码已实现的主要平台能力如下：

- **HLS / m3u8**：网页提取、m3u8 预览、ffmpeg / 分片下载
- **X / Twitter**：链接识别、流探测、最高画质优选、必要时走 fallback
- **YouTube**：通过 `yt-dlp` 下载，默认强制输出 MP4
- **Bilibili**：独立分流解析与下载，支持站点 cookies
- **Douyin / 抖音**：分享页解析、直链优先下载、必要时回退 `yt-dlp`

## Architecture / Workflow

项目按平台分流，而不是把所有站点塞进一条统一链路。

1. 前端提交 URL、可选输出名、Referer、User-Agent、代理等参数
2. 后端根据 URL 识别平台
3. 针对不同平台选择对应解析策略
4. 生成预览信息、标题、推荐文件名与下载任务
5. 下载阶段根据媒体类型选择 HLS、直连或 `yt-dlp`
6. 结果按平台归档到 `/downloads/<platform>` 子目录

当前主要下载模式：

- **HLS**：优先走分片下载，失败时回退 ffmpeg
- **Direct download**：已拿到可直接下载的媒体 URL 时直接拉流
- **yt-dlp**：用于 YouTube、Bilibili、部分 X / Twitter 场景，以及 Douyin 的回退路径

这种结构的目标是降低互相干扰：某个平台的修复不应轻易破坏其他已稳定的链路。

## Title display and file naming

标题展示与文件名策略是当前版本的一项明确约束：

- 页面展示优先保留抓取到的原始标题信息
- 后端会基于标题生成 `suggested_output`
- 下载文件名会经过净化，移除不适合落盘的字符
- 文件名长度会被控制，避免异常标题导致创建文件失败
- 同目录下如发生重名，会自动追加 ` (1) / (2)` 等后缀避让

对 Douyin 而言，当前标题优先来源于：

- `desc`
- `share_desc`

因此，**页面展示标题** 与 **最终下载文件名** 是两个相关但不完全等同的概念：前者尽量保留信息，后者以可安全落盘为目标。

## Douyin Notes

### Current lightweight no-cookie path

当前 Douyin（抖音）实现强调“轻量单服务链路”，优先不依赖浏览器自动化、不引入额外抓取容器。

已实现的现实能力：

- 能识别 `douyin.com`、`iesdouyin.com`、`v.douyin.com` 及部分 aweme / CDN 直链
- 优先走移动分享页源码解析
- 从分享页中提取 `video_id / play_addr` 等信息
- 已拿到 Douyin / aweme / CDN 直链时，优先 direct download
- 必要时可回退到 `yt-dlp`
- 对 Douyin 直连下载会补充移动端 User-Agent 与默认 Referer
- 会避免 `play / playwm` 双流重复问题
- `requests` 超时时会尝试 `curl` fallback（见现有实现说明）

### Capability boundary

无 cookies 轻量链路的边界也需要明确：

- 当前能力主要建立在**分享页可访问、页面源码可提取、直链仍有效**的前提下
- 不保证覆盖所有 Douyin 链接形态、所有风控态或所有私密内容
- 当目标链接触发验证码、强风控、临时校验或分享页策略变化时，解析可能失败
- 当前项目没有引入浏览器自动化来穿透这些限制
- 当前 Web 设置页虽然有 `douyinck` 配置字段兼容，但**没有像 X / YouTube / Bilibili 那样提供单独上传 Douyin cookies 的界面按钮**

代码里已保留 Douyin cookies 路径与 fresh cookies 选择逻辑：

- 默认路径：`/app/data/cookies/douyin.cookies.txt`
- fresh cookies 路径：`/app/data/cookies/douyin.fresh.cookies.txt`

当错误信息提示需要 fresh cookies 时，应按当前实现约定：

1. 用浏览器重新打开目标抖音链接
2. 完成风控 / 验证
3. 立刻导出最新 cookies
4. 覆盖到 `/app/data/cookies/douyin.fresh.cookies.txt`
5. 再次重试

这属于补救路径，不代表当前版本已经把 Douyin 登录态接入做成完整 UI 能力。

## Cookies and platform routing

项目支持为不同站点分别配置 cookies 路径，避免混用。

默认路径：

```text
/app/data/cookies/twitter.cookies.txt
/app/data/cookies/youtube.cookies.txt
/app/data/cookies/bilibili.cookies.txt
/app/data/cookies/douyin.cookies.txt
```

平台分流规则：

- X / Twitter → `twitter.cookies.txt`
- YouTube → `youtube.cookies.txt`
- Bilibili → `bilibili.cookies.txt`
- Douyin → `douyin.cookies.txt`（或更新鲜的 `douyin.fresh.cookies.txt`）

当前 Web 页面已提供上传入口的只有：

- X / Twitter cookies
- YouTube cookies
- Bilibili cookies

Douyin cookies 目前仍以配置路径 / 手工落盘为主。

## WeCom Integration

### Integration model

项目已接入企业微信自建应用回调，入口为：

```text
/api/wecom/callback
```

当前实现能力：

- `GET /api/wecom/callback`：处理企业微信 URL 校验（`echostr` 解密回显）
- `POST /api/wecom/callback`：接收企业微信加密 XML 消息
- 当前只处理 `MsgType=text`
- 从文本内容中自动提取第一个 `http/https` 链接
- 识别后复用现有下载任务创建逻辑

### Configuration method

企业微信参数推荐在 **Web 设置页** 中填写并保存，不建议写死在镜像或提交到仓库。

设置页对应字段包括：

- `wecom_enabled`
- `wecom_corp_id`
- `wecom_agent_id`
- `wecom_secret`
- `wecom_token`
- `wecom_encoding_aes_key`
- `wecom_callback_url`

敏感字段处理方式：

- 保存时写入配置
- 配置读取接口返回掩码值，不返回明文
- 如需清空，可留空后再次保存

### Notification workflow

企业微信通知链路当前已经绑定到真实任务生命周期，重点包括三段：

1. **防超时被动回复**  
   企业微信回调收到文本后，服务会先快速返回一条被动文本回复，表示“已收到链接，正在创建任务”。这样做是为了避免企业微信等待超时。

2. **任务创建成功通知**  
   当真实 job 已创建并写入网页任务列表后，系统会异步通过企业微信应用消息主动发送“任务创建成功”通知，格式类似：  
   `［平台］任务创建成功：媒体名`

3. **任务完成 / 失败 / 取消回执**  
   当真实 job 状态进入 `done / failed / cancelled` 时，系统会发送最终状态通知，内容通常包含：
   - 平台
   - 状态（下载完成 / 下载失败 / 任务已取消）
   - 标题
   - 文件名
   - 任务 ID
   - 失败时的简要原因

### Current scope of WeCom delivery

当前企业微信链路只负责：

- 接收文本中的下载链接
- 创建任务
- 回传文本状态

当前**不会**把下载完成的视频文件主动回传到企业微信会话中。

## Configuration

默认配置文件位置：

```text
/app/data/config.json
```

常见配置项：

- `default_proxy`
- `auto_retry_enabled`
- `auto_retry_delay_seconds`
- `auto_retry_max_attempts`
- `xck`
- `youtubeck`
- `bilibilick`
- `douyinck`
- `wecom_enabled`
- `wecom_corp_id`
- `wecom_agent_id`
- `wecom_secret`
- `wecom_token`
- `wecom_encoding_aes_key`
- `wecom_callback_url`

兼容旧字段：

- `twitter_cookies_path`
- `youtube_cookies_path`
- `bilibili_cookies_path`
- `douyin_cookies_path`

## Docker Deployment

### docker-compose.yml

仓库当前提供的 `docker-compose.yml` 如下：

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

启动方式：

```bash
cd /root/docker/mt-downloader
docker compose pull
docker compose up -d
```

默认访问地址示例：

```text
http://<server-ip>:9151
```

### docker run

```bash
docker pull okhao/mt:latest

docker run -d \
  --name Mt \
  -p 9151:8080 \
  -v /root/docker/video:/downloads \
  -v $(pwd)/data:/app/data \
  okhao/mt:latest
```

### Image contents

当前 Dockerfile 基于 `python:3.12-slim`，并安装：

- `ffmpeg`
- `curl`
- FastAPI 及相关依赖
- `yt-dlp`
- `pycryptodome`

因此镜像已覆盖：

- Web 服务运行
- HLS / ffmpeg 下载
- `yt-dlp` 下载
- 企业微信消息加解密与回调处理

## Download directories

容器内下载目录按平台自动分流：

```text
/downloads/m3u8
/downloads/x
/downloads/youtube
/downloads/bilibili
/downloads/douyin
```

如果宿主机映射为：

```text
/root/docker/video:/downloads
```

则实际文件会落在宿主机对应子目录中。

## Operational Notes / Limitations

以下限制应在部署和使用前明确：

- **不支持 DRM 内容**，包括但不限于 Widevine、FairPlay、PlayReady
- 某些平台必须依赖 Referer、User-Agent、代理或有效 cookies 才能解析
- X / Twitter、YouTube、Bilibili 的部分样本在游客态下可能无法获得可下载流
- Douyin 无 cookies 轻量链路并非全覆盖方案，风控或页面策略变化会直接影响成功率
- 企业微信当前只支持文本消息触发下载，不支持回传视频文件
- 当前系统是轻量面板，不是通用全站解析器，也不承诺覆盖所有媒体站点

## Local development note

有一个很容易踩的点：仓库里的 `docker-compose.yml` 当前使用的是现成镜像：

```yaml
image: okhao/mt:latest
```

这意味着本地修改源码后，直接执行：

```bash
docker compose up -d
```

**不会自动带上你的源码改动。**

如果你是在本地开发当前仓库，应先重建镜像，例如：

```bash
cd /root/docker/mt-downloader
docker build -t okhao/mt:latest .
docker compose up -d --force-recreate
```

## Security notes

建议不要将服务直接裸露到公网。

更稳妥的方式包括：

- 放在内网环境中使用
- 通过 VPN / Tailscale 暴露
- 置于反向代理之后并补充访问控制

另外请注意：

- 不要把 cookies 提交进 Git 仓库
- 不要把企业微信密钥、Token、AES Key 等敏感信息写入公开文档
- 使用代理时，确认流量与凭据不会被错误转发到不可信节点

## Roadmap

项目后续可以继续增强，但当前 README 只对已存在能力做说明。若继续演进，比较自然的方向包括：

- 完善 Douyin 登录态接入体验
- 补充更细粒度的任务观测与错误分类
- 为企业微信链路增加更多消息类型或运维提示

以上不代表已实现，仅作为可能的演进方向。

## License

MIT
