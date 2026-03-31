# mt-downloader

一个适合自托管的视频下载面板。

它的目标很直接：给自己或小团队提供一个能在浏览器里贴链接、发起下载、查看结果的轻量服务，同时兼顾常见平台下载和企业微信接入场景。

## 功能

- 网页面板：粘贴链接、解析、创建下载任务、查看任务状态
- 下载归档：下载结果统一落盘，方便后续整理和取回
- 任务管理：支持排队、重试、删除、查看历史
- 企业微信接入：支持通过企业微信发送链接创建下载任务，并接收任务通知
- Docker 部署：适合直接用容器方式快速跑起来

## 使用价值

这个项目适合下面几类场景：

- 想在自己的机器或服务器上搭一个简单稳定的下载面板
- 不想每次都手敲命令，希望用网页统一管理下载任务
- 需要把下载入口接到企业微信里，减少手动搬链接
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

## 免责声明

本项目仅用于个人自托管和合法授权内容处理。请自行遵守目标平台规则及当地法律法规。
