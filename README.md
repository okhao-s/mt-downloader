# mt-downloader

一个适合自托管的轻量视频下载面板，支持网页解析、任务排队、下载归档，以及企业微信「被动回复 + 主动通知」接入。

> 当前定位：**单服务、低依赖、保守稳定**。优先把已跑通链路做稳，而不是追求全站点全能力覆盖。

## 功能概览

- Web UI：贴链接、解析预览、创建下载任务、查看历史
- 多平台分流：HLS / m3u8、X(Twitter)、YouTube、Bilibili、Douyin
- 后台任务队列：排队、取消、重试、历史清理
- 按平台归档：下载结果自动落到 `/downloads/<platform>`
- cookies 管理：X / YouTube / Bilibili 支持上传 cookies.txt，Douyin 支持路径配置
- 企业微信接入：支持回调校验、文本消息触发下载、创建/完成主动通知
- Docker 部署：单容器即可跑，不依赖浏览器自动化
- 版本可观测：提供 `/api/version` 用于确认运行镜像版本/提交

---

## 支持的平台与策略

### 1) HLS / m3u8
- 支持网页里提取 m3u8
- 支持预览播放
- 下载时优先走分片方案，失败再回退 ffmpeg

### 2) X / Twitter
- 优先挑最高质量流
- 必要时回退 `yt-dlp`
- 建议上传浏览器导出的 cookies.txt，稳定性更好

### 3) YouTube
- 默认走 `yt-dlp`
- 输出优先归一成 mp4
- 遇到年龄限制 / 登录限制时建议配 cookies

### 4) Bilibili
- 独立分流解析
- 遇到 412 / 风控时建议上传 cookies

### 5) Douyin
- 优先走轻量解析链路
- 已拿到可直连视频时优先 direct download
- 必要时回退 `yt-dlp`
- 支持配置 `douyin.cookies.txt`，也支持更“新鲜”的 `douyin.fresh.cookies.txt`

---

## 项目结构

核心文件：

- `app.py`：FastAPI 主服务、任务管理、API、企业微信回调
- `core.py`：平台识别、解析、下载器封装、配置读写
- `wecom.py`：企业微信加解密与主动发消息客户端
- `static/app.js`：前端逻辑
- `templates/index.html`：页面模板
- `Dockerfile` / `docker-compose.yml`：容器部署

---

## 快速开始

### 方式一：直接用 Docker Compose

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

启动：

```bash
docker compose up -d
```

访问：

```text
http://你的机器IP:9151
```

### 方式二：本地构建镜像

```bash
docker build \
  --build-arg APP_VERSION=$(git rev-parse --short HEAD) \
  --build-arg APP_COMMIT=$(git rev-parse HEAD) \
  -t okhao/mt:latest .
```

运行：

```bash
docker run -d \
  --name Mt \
  -p 9151:8080 \
  -v /root/docker/video:/downloads \
  -v $(pwd)/data:/app/data \
  --restart unless-stopped \
  okhao/mt:latest
```

---

## 配置说明

配置文件默认位置：

```text
/app/data/config.json
```

常见字段：

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

代理路由规则（当前默认行为）：

- `default_proxy` 仍然是统一默认代理入口
- `douyin` / `bilibili` 自动直连，不走代理
- 其他平台默认走 `default_proxy`
- 若请求里显式传了 `proxy`，也会先经过同一套路由规则

### cookies 默认路径

```text
/app/data/cookies/twitter.cookies.txt
/app/data/cookies/youtube.cookies.txt
/app/data/cookies/bilibili.cookies.txt
/app/data/cookies/douyin.cookies.txt
/app/data/cookies/douyin.fresh.cookies.txt
```

说明：

- X / YouTube / Bilibili：前端可直接上传 cookies.txt
- Douyin：当前以路径配置为主，没有单独上传按钮
- Douyin 若提示需要 fresh cookies，优先覆盖 `douyin.fresh.cookies.txt`

---

## 企业微信接入

### 支持的能力

企业微信入口：

```text
/api/wecom/callback
```

当前已实现：

- `GET /api/wecom/callback`：企业微信 URL 校验
- `POST /api/wecom/callback`：接收企业微信加密回调
- 文本消息内自动提取第一个 http/https 链接
- 先快速返回被动回复，避免回调超时
- 再异步创建任务
- 再通过应用消息主动发“任务创建成功 / 下载完成 / 下载失败 / 已取消”通知

### 设置页需要填写的字段

- `wecom_enabled`
- `wecom_corp_id`
- `wecom_agent_id`
- `wecom_secret`
- `wecom_token`
- `wecom_encoding_aes_key`
- `wecom_callback_url`

### 配置保存行为

当前版本对敏感字段做了保守处理：

- 设置页密码框默认不回显明文
- **留空保存 = 保留原值**
- 如需清空，需明确通过接口传空值或改配置文件

这样可以避免“只是改了别的设置，结果把企业微信 Secret / Token / AESKey 一起冲掉”的事故。

### 企业微信消息链路

1. 用户发文本链接到企业微信应用
2. 服务先被动回复：收到任务，正在创建任务
3. job 进入 `downloading` 后，主动发一条“开始下载”
4. job 成功进入 `done` 后，主动发一条“下载完成”

---

## API 速览

### 解析

```http
POST /api/parse
```

### 创建下载任务

```http
POST /api/download
```

### 批量创建下载任务

```http
POST /api/download/all
```

### 查看任务

```http
GET /api/jobs
```

### 删除 / 取消任务

```http
POST /api/jobs/{job_id}/delete
```

### 手动重试

```http
POST /api/jobs/{job_id}/retry
```

### 获取配置

```http
GET /api/config
```

### 保存配置

```http
POST /api/config
```

### 版本信息

```http
GET /api/version
```

返回示例：

```json
{
  "version": "v0.1.0",
  "commit": "abcdef123456"
}
```

---

## 稳定性说明

当前版本重点做的是“保守稳态”，包括：

- 企业微信通知只在真实任务节点触发
- 删除 queued 任务时，做了隐藏/取消保护，避免幽灵任务继续跑
- 下载线程启动前会二次检查任务是否已被删除
- 敏感配置默认保留，不因空输入误清空
- `/api/version` 可辅助确认部署镜像与代码版本是否一致

---

## 常见问题

### 1. 解析成功但预览失败
常见原因：
- 源站限制了 Referer / User-Agent
- 预览链路被代理或源站拦截
- 该流适合直接下载，不适合预览

建议：
- 补 Referer / User-Agent
- 配代理
- 直接试下载

### 2. Bilibili 返回 412 / 拿不到流
大概率是风控，先上传有效 cookies.txt 再试。

### 3. Douyin 不稳定 / 提示 fresh cookies
说明旧 cookies 已经过期或风控态不对。重新在浏览器打开目标链接、完成验证后，立刻导出最新 cookies 覆盖：

```text
/app/data/cookies/douyin.fresh.cookies.txt
```

### 4. 企业微信能收到“已收到链接”，但后面没主动通知
优先检查：
- CorpID / AgentID / Secret 是否正确
- 应用可见范围是否包含目标成员
- `touser` 是否真的是企业微信成员 UserID
- 应用是否有发消息权限

### 5. 我改了设置，企业微信突然失效
旧版本里这类问题通常和敏感字段被空覆盖有关。当前版本已改成：留空保存默认保留原值。

### 6. 删除了任务，怎么还在下载？
旧竞态下 queued 任务可能在删除瞬间被线程拿走。当前版本已补“软删除 + 启动前二次检查”，这类幽灵任务风险已明显降低。

---

## 更新建议

建议实际部署时固定自己的发布流程：

1. 本地改完代码
2. 跑最小验证
3. 构建镜像时写入 `APP_VERSION` / `APP_COMMIT`
4. 推送镜像
5. 部署后访问 `/api/version` 确认线上版本

---

## 开发调试

### 基本语法检查

```bash
python -m py_compile app.py core.py wecom.py
```

### 跑现有脚本测试

```bash
python test_wecom_notify.py
python test_wecom_race.py
```

---

## 免责声明

本项目仅用于个人自托管和合法授权内容下载。请自行遵守目标平台服务条款与当地法律法规。
