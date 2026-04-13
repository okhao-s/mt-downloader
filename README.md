# mt-downloader

一个偏自托管场景的视频下载面板，支持网页发起任务，也支持接企业微信做下载入口。

这次发布后的口径，重点就这几件事：

- 主动通知现在只保留 `started / done / failed` 三类
- 企业微信主动通知的正式方案是 **自建 relay / 代理转发**
- 本地服务继续负责 **企业微信回调、消息解析、交互回复、任务创建**
- X / Twitter 下载已经补了几轮稳定性收口：超时保护、卡死保护、线程池隔离、短 TTL 探测缓存、`yt-dlp` 失败后的 fallback

如果你之前看过旧 README，旧口径里那些“创建成功通知”“开发态中转测试说法”“本地直发优先”之类内容，现在都可以忽略。

另外补一句：仓库里的测试现在按正式回归用例保留，不再保留一次性调试脚本。

---

## 当前能力

### 1. Web 面板

- 粘贴链接后解析
- 创建下载任务
- 查看排队、下载中、成功、失败状态
- 查看已完成文件
- 删除、重试任务

### 2. 下载支持

当前主要覆盖：

- HLS / m3u8
- X / Twitter
- YouTube
- Bilibili
- Douyin
- 其他可直链媒体 URL

说明：不同站点是否能成功，仍然会受源站风控、账号态、cookies、地区、代理、限流影响。

### 3. 企业微信接入

支持两部分：

- **被动入口**：企业微信回调到本地 `/api/wecom/callback`，由本地服务解析消息并创建任务
- **主动通知**：任务状态变化后，发 `started / done / failed` 给企业微信用户

### 4. 企业微信主动通知转发（正式方案）

当前推荐把主动通知交给你自己的 relay/代理服务代发。

支持两种转发方式：

#### 方案 A：自定义 JSON relay

本服务会把通知 POST 到你配置的 `wecom_forward_url`，请求头可带：

- `X-Wecom-Forward-Token: <token>`

请求体示例：

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

适合你自己做统一通知网关、审计、风控、二次路由。

#### 方案 B：wxchat 一类企业微信 API 代理

如果 `wecom_forward_url` 填的是代理根地址，或 `/cgi-bin/message/send` 这类地址，程序会自动识别为代理模式，走：

- `/cgi-bin/gettoken`
- `/cgi-bin/message/send`

这类模式下：

- 不发送自定义 JSON
- 通常不需要 `wecom_forward_token`
- 但 **本地仍然需要配置** `wecom_corp_id / wecom_agent_id / wecom_secret`
- 因为 token 仍然是 mt-downloader 通过代理去换

### 5. 本地直连企业微信

还保留，但现在更适合作为：

- 内网部署
- 简单单实例
- 临时兜底

如果你有公网、转发、审计、复用等需求，优先用 relay/代理方案。

---

## 当前通知语义

主动通知现在只会发 3 类：

- `started`：任务真正进入下载阶段
- `done`：任务完成
- `failed`：任务失败

不再主动发“任务已创建”这一类通知。

这样做的目的：

- 减少噪音
- 避免排队阶段刷消息
- 让通知更贴近用户真正关心的状态

---

## 推荐部署

### Docker 直接运行

```bash
docker pull okhao/mt:latest

docker run -d \
  --name mt-downloader \
  -p 9151:8080 \
  -e TZ=Asia/Shanghai \
  #-e WECOM_FORWARD_URL= \
  #-e WECOM_FORWARD_TOKEN= \
  -v /root/docker/video:/downloads \
  -v $(pwd)/data:/app/data \
  --restart unless-stopped \
  okhao/mt:latest
```

启动后访问：

```text
http://你的机器IP:9151
```

### docker-compose

仓库内置 `docker-compose.yml` 已按发布态调整，默认直接拉镜像：

```bash
docker compose up -d
```

默认挂载：

- `./data -> /app/data`
- `/root/docker/video -> /downloads`

如果你的下载目录不是这个路径，改宿主机左侧路径就行。

---

## 推荐配置

## 1. 基础

建议至少确认这几项：

- 持久化 `./data`
- 持久化 `/downloads`
- 宿主机磁盘空间够用
- 容器所在机器到目标站点网络可达

## 2. 代理

如果你需要代理，建议按站点实际情况配置。

当前有一条内置策略：

- **Douyin / Bilibili 默认不走全局代理复用逻辑**
- X / YouTube / 其他 URL 可继续用代理

原因很简单：有些站点挂代理反而更不稳，或者更容易触发额外风控。

## 3. Cookies

当前支持单独上传站点 cookies，路径默认落在：

- `/app/data/cookies/twitter.cookies.txt`
- `/app/data/cookies/youtube.cookies.txt`
- `/app/data/cookies/bilibili.cookies.txt`
- `/app/data/cookies/instagram.cookies.txt`
- `/app/data/cookies/douyin.cookies.txt`
- `/app/data/cookies/douyin.fresh.cookies.txt`

建议：

- X：有登录态/年龄限制/敏感内容时，尽量提供可用 cookies
- YouTube：遇到地区/年龄/登录限制时补 cookies
- Bilibili：遇到 412、风控、清晰度受限时补 cookies
- Instagram：遇到 `empty media response`、帖子需要登录态、游客拿不到媒体时补 cookies
- Douyin：遇到风控/验证码时，用最新 fresh cookies

### Instagram 特别说明

Instagram 现在对游客访问收得更紧：

- 有些帖子 / Reels 未登录就会直接返回空媒体
- `yt-dlp` 常见报错会是 `Instagram sent an empty media response`
- 这类情况通常不是我们代码逻辑坏了，而是 **源站要求登录态**

这一版已经补上：

- `/app/data/cookies/instagram.cookies.txt` 默认路径
- 设置页可直接上传 Instagram `cookies.txt`
- 解析和下载都会自动带上 Instagram cookies
- `docker-compose.yml` 现有 `./data:/app/data` 挂载可直接复用，不用额外挂目录

最省事的用法：

1. 浏览器里登录 Instagram
2. 导出 Netscape 格式 `cookies.txt`
3. 在设置页点“上传 Instagram cookies”
4. 重新贴链接直接下载

如果还是失败，再看两件事：

- cookies 是否过期
- 当前 IP / 代理 是否也被 Instagram 风控

### X / Twitter 特别说明

X 的可用性对 cookies 影响很大：

- 有些推文未登录几乎拿不到稳定媒体信息
- 有些链接 `yt-dlp` 能识别标题，但拿不到可下流
- 有些链接会返回空媒体、异常 HTML、或者中途卡住

这一版已经做了这些稳定性优化：

- `yt-dlp` 探测超时保护
- ffmpeg / 子进程卡死保护
- 避免正则灾难回溯导致卡死
- 媒体代理与普通请求线程池隔离
- 短 TTL 的解析缓存，减少重复探测抖动
- **即使 `yt-dlp` 报错，X 仍会继续尝试 fallback 抓流**

但要实话实说：

- cookies 失效时，X 仍可能失败
- 源站临时抽风时，仍可能失败
- 被删帖、地区限制、私密内容，本服务也没法硬解

---

## 企业微信配置建议

## 1. 回调

企业微信回调地址继续指向本地服务：

```text
https://你的域名/api/wecom/callback
```

本地服务负责：

- URL 校验
- 解密消息
- 解析用户发来的链接
- 创建任务
- 被动回复

## 2. 主动通知

推荐配置：

- `wecom_forward_url`：填你的 relay/代理地址
- `wecom_forward_token`：如果你的 relay 需要鉴权，就填

环境变量也可作为兜底：

- `WECOM_FORWARD_URL`
- `WECOM_FORWARD_TOKEN`

### 推荐拓扑

```text
企业微信用户
   -> 企业微信回调
   -> mt-downloader 本地 /api/wecom/callback
   -> 本地创建任务
   -> 任务状态变化
   -> mt-downloader POST 到你的 relay/代理
   -> relay/代理 代发企业微信主动通知
```

这个拓扑的好处：

- 主动通知和业务入口解耦
- 方便做公网暴露控制
- 方便多服务统一通知
- 方便做鉴权、日志、审计、重试

---

## 使用流程

### Web

1. 打开面板
2. 粘贴链接
3. 先解析
4. 确认文件名/流后创建任务
5. 在任务列表查看结果

### 企业微信

1. 配好企业微信参数
2. 配好回调 URL
3. 配好主动通知 relay/代理（推荐）
4. 给应用发送带链接的消息
5. 本地服务创建任务
6. 用户收到 `started / done / failed` 通知

---

## 当前限制

这部分别神话，提前说清楚：

- 不保证所有站点、所有链接都能下
- 不保证被风控、私密、地区限制内容可下载
- X / YouTube / Bilibili / Douyin 的稳定性都依赖源站策略
- cookies 过期后，成功率会明显下降
- 单实例默认并发下载槽位有限，更适合个人/小团队，不是大规模分布式下载器
- 失败自动重试不是万能，源站策略问题通常重试也没用

---

## 排障

### 1. 企业微信主动通知没发出去

先看你走的是哪条路：

- relay JSON
- wxchat 代理
- 本地直连

排查建议：

1. 看应用设置里 `wecom_forward_url` 是否真的保存成功
2. 如果 relay 要鉴权，确认 `wecom_forward_token` 是否正确
3. 如果走代理模式，确认代理地址是否填对
4. 如果走 wxchat 代理，确认本地 `corp_id / agent_id / secret` 仍然已配置
5. 查容器日志里是否有：
   - `[wecom-forward]`
   - `[wecom] job_started notify failed`
   - `[wecom] job_done notify failed`
   - `[wecom] job_failed notify failed`

### 2. 企业微信回调失败

重点看：

- `wecom_token`
- `wecom_encoding_aes_key`
- `wecom_corp_id`
- 企业微信后台回调地址是否与实际公网地址一致
- 反代是否把 query string / body 搞坏了

### 3. X 链接解析失败

先判断是不是 cookies 问题：

- 没 cookies
- cookies 过期
- 内容需要登录
- 内容地区/年龄受限

建议处理：

1. 重新导出可用的 `twitter.cookies.txt`
2. 再试一次解析
3. 看日志里是否出现 `yt-dlp 探测失败`
4. 如果已经 fallback 仍失败，基本就是源站限制或 cookies 不可用

### 4. Bilibili 提示 412 / 风控

这通常就是 cookies 不够新或不够完整。

建议：

- 上传有效的 Bilibili `cookies.txt`
- 再重试

### 5. Douyin 提示 fresh cookies

说明旧 cookies 已经不够用了。

建议：

1. 浏览器打开目标抖音链接
2. 过完风控/验证码
3. 立刻重新导出 cookies
4. 覆盖 `douyin.fresh.cookies.txt`
5. 再试

### 6. 页面能开，但播放/预览拖慢首页

这版已经把媒体代理和普通请求做了线程池隔离，通常不会再把首页一起拖死。

如果你还遇到：

- 先检查机器本身带宽和 CPU
- 再看是不是某个源站本身非常慢
- 最后看是否有大量并发媒体预览请求

---

## 开发/版本信息

容器支持注入：

- `APP_VERSION`
- `APP_COMMIT`

可通过接口查看：

- `GET /api/version`

---

## 合法使用

本项目仅用于个人自托管和合法授权内容处理。

请自行遵守：

- 目标平台规则
- 所在地区法律法规
- 企业内部合规要求
