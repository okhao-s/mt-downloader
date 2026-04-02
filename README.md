# mt-downloader

新增“自定义企业微信转发”能力，给用户自己测试中转服务：

- `wecom_forward_url`：主动通知转发地址，推荐直接配到应用设置里
- `wecom_forward_token`：主动通知转发鉴权 token，推荐直接配到应用设置里
- `WECOM_FORWARD_URL`：可选环境变量兜底
- `WECOM_FORWARD_TOKEN`：可选环境变量兜底

说明：
- 只有 `started / done / failed` 三类主动通知会优先走自定义转发。
- 如果没配 `wecom_forward_url`（或环境变量 `WECOM_FORWARD_URL`），仍会回退为本地直连企业微信发送。
- 企业微信回调 `/api/wecom/callback` 和被动回复逻辑不变。
- 自定义转发失败会打印清晰日志，但不会额外引入新的自动重试风暴。

转发请求格式：

```json
{
  "kind": "done",
  "job_id": "abc123",
  "to_user": "zhangsan",
  "content": "[Douyin] 下载完成\n文件：short.mp4\n任务ID：abc123",
  "title": "超短视频",
  "status": "done",
  "error": "",
  "source_url": "https://example.com/v.mp4",
  "platform": "douyin",
  "output": "short.mp4",
  "status_text": "下载完成"
}
```

请求头：
- 若配置了 token，会带 `X-Wecom-Forward-Token: <token>`

一个适合自托管的视频下载面板。

它的目标很直接：给自己或小团队提供一个能在浏览器里贴链接、发起下载、查看结果的轻量服务，同时兼顾常见平台下载和企业微信接入场景。

## 功能

- 网页面板：粘贴链接、解析、创建下载任务、查看任务状态
- 下载归档：下载结果统一落盘，方便后续整理和取回
- 任务管理：支持排队、重试、删除、查看历史
- 企业微信接入：支持通过企业微信发送链接创建下载任务，并接收任务通知
- 自定义企业微信转发：主动通知可先转给外部服务，由外部服务代发
- Docker 部署：适合直接用容器方式快速跑起来

## 使用价值

这个项目适合下面几类场景：

- 想在自己的机器或服务器上搭一个简单稳定的下载面板
- 不想每次都手敲命令，希望用网页统一管理下载任务
- 需要把下载入口接到企业微信里，减少手动搬链接
- 想自己测试企业微信通知中转服务
- 想把不同平台的视频下载集中到同一个服务里处理

## 支持范围

当前主要支持以下内容类型或平台：

- HLS / m3u8
- X / Twitter
- YouTube
- Bilibili
- Douyin
- 企业微信回调触发下载

说明：不同平台会受源站策略、登录状态、cookies、地区限制等影响，实际可用性以运行环境和目标链接为准。

## 基本用法

### 方式一：直接拉 Docker 镜像

```bash
docker pull okhao/mt:latest
```

运行示例：

```bash
docker run -d \
  --name mt-downloader \
  -p 9151:8080 \
  -v /root/docker/video:/downloads \
  -v $(pwd)/data:/app/data \
  --restart unless-stopped \
  okhao/mt:latest
```

启动后访问：

```text
http://你的机器IP:9151
```

### 方式二：docker-compose

```yaml
services:
  mt-downloader:
    image: okhao/mt:latest
    container_name: mt-downloader
    ports:
      - "9151:8080"
    environment:
      - WECOM_FORWARD_URL=${WECOM_FORWARD_URL:-}
      - WECOM_FORWARD_TOKEN=${WECOM_FORWARD_TOKEN:-}
    volumes:
      - /root/docker/video:/downloads
      - ./data:/app/data
    restart: unless-stopped
```

启动：

```bash
docker compose up -d
```

### Web 基本使用

1. 打开网页面板
2. 粘贴视频链接
3. 先解析，再创建下载任务
4. 在任务列表里查看进度和结果

### 企业微信基本使用

1. 在服务里配置企业微信参数
2. 把企业微信回调地址指向服务接口
3. 给应用发送带链接的消息
4. 服务收到后创建下载任务，并回传通知

### 自定义企业微信转发使用

1. 在“设置”里填写 `自定义企业微信转发 URL`
2. 如需鉴权，再填 `自定义企业微信转发 Token`
3. 保存后，`started / done / failed` 主动通知会优先 POST 到你的转发服务
4. 你的外部服务收到后，自己决定怎么代发企业微信
5. 如果清空转发 URL 并保存，系统会恢复本地直连企业微信发送

## 免责声明

本项目仅用于个人自托管和合法授权内容处理。请自行遵守目标平台规则及当地法律法规。
