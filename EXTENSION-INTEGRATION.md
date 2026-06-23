# 浏览器插件 → mt 对接方案

## 概述

浏览器扩展 `save-page-images-extension-v1.1.0.zip` 可直接将当前页面扫描到的图片链接推送到 mt-downloader，实现一键下载。

---

## 架构

```
┌─────────────────────────┐         POST /api/picture/push          ┌──────────────────┐
│  Chrome 浏览器插件       │ ──────────────────────────────────────► │  mt-downloader   │
│  (MV3 Extension)        │         http://<host>:9151              │  (Docker 容器)   │
│                         │                                         │                  │
│  - 扫描页面可见图片      │                                         │  - 接收图片链接   │
│  - 优先 tu.* 源         │                                         │  - 多线程下载     │
│  - 支持导出/推送        │                                         │  - 保存到 /downloads/photo/  │
└─────────────────────────┘                                         └────────┬─────────┘
                                                                           │
                                                                           ▼
                                                                  /root/docker/video/photo/
```

---

## 环境现状

| 项目 | 状态 |
|------|------|
| **mt 容器** | ✅ 已修复，使用本地构建的 arm64 镜像 (`mt-downloader:arm64`) |
| **mt 端口** | `0.0.0.0:9151 → 8080` |
| **mt 宿主机 IP** | `10.0.0.111` |
| **下载目录** | `/root/docker/video` (容器内 `/downloads`) |
| **图片存放** | `/root/docker/video/photo/<页面标题>/` |
| **API 鉴权** | 可选 (`MT_API_TOKEN` 环境变量) |

---

## 插件安装

1. Chrome 地址栏输入 `chrome://extensions/`
2. 开启 **开发者模式**
3. 点击 **加载已解压的扩展程序**
4. 选择解压后的插件目录（需先 `unzip`）

```bash
# 解压插件
mkdir -p /tmp/save-page-images-ext
cd /tmp/save-page-images-ext
# 使用 Python 解压（因为系统没有 unzip）
python3 -c "
import zipfile
with zipfile.ZipFile('/root/docker/save-page-images-extension-v1.1.0.zip', 'r') as z:
    z.extractall('/tmp/save-page-images-ext')
"
ls /tmp/save-page-images-ext/
# manifest.json popup.html popup.js background.js
```

然后在 Chrome 中加载 `/tmp/save-page-images-ext` 目录。

---

## 插件配置

在插件 popup 中设置：

| 字段 | 值 | 说明 |
|------|-----|------|
| **图片子目录名** | `my-images` | 自定义，用于组织下载文件 |
| **mt 接口配置 → mtBaseUrl** | `http://10.0.0.111:9151` | mt 容器的宿主机 IP:端口 |
| **mt 接口配置 → mtToken** | (留空) | 如果设置了 `MT_API_TOKEN` 则填写 |

> ⚠️ 如果 mt 容器设置了 `MT_API_TOKEN`，插件必须填写对应的 token，否则推送会被拒绝。

---

## 推送 API 协议

### 请求

```
POST http://<mt-host>:9151/api/picture/push
Content-Type: application/json
X-MT-Token: <token>  (如果配置了 MT_API_TOKEN)
```

### 请求体

```json
{
  "pageUrl": "https://example.com/thread/123",
  "pageTitle": "帖子标题",
  "pageHost": "example.com",
  "suggestedSubdir": "thread-123",
  "referer": "https://example.com/thread/123",
  "links": [
    {
      "url": "https://img.example.com/image.jpg",
      "source": "content",
      "width": 1200,
      "height": 800,
      "priority": 3,
      "kind": "img"
    }
  ]
}
```

### 响应

```json
{
  "ok": true,
  "job": {
    "id": "abc123",
    "status": "downloading",
    "download_dir": "photo/帖子标题"
  },
  "accepted": 5
}
```

---

## 使用流程

1. **浏览器打开目标页面**（确保图片已解密/显示）
2. **点击插件图标**，打开 popup
3. **填写/确认 mtBaseUrl** → `http://10.0.0.111:9151`
4. **点击「扫描并推送到 mt」**
5. 插件自动：
   - 扫描当前页面可见图片
   - 优先保留 `tu.*` 源链接
   - 过滤掉 avatar/logo/ad 等无关图片
   - 调用 `/api/picture/push` 推送给 mt
6. mt 后台异步下载，图片保存到 `/root/docker/video/photo/<标题>/`

---

## 排查

### 插件无法连接 mt

```bash
# 从宿主机测试
curl -s http://10.0.0.111:9151/api/version

# 从浏览器检查：
# 1. 是否跨域（mt 已配置 CORS allow-origin: *）
# 2. mtBaseUrl 是否正确
# 3. 如果设置了 MT_API_TOKEN，插件是否填写了 token
```

### mt 容器异常

```bash
# 查看日志
docker logs mt-downloader

# 重启容器
docker restart mt-downloader

# 检查端口
docker ps | grep mt-downloader
```

### 图片未下载

```bash
# 查看下载目录
ls -la /root/docker/video/photo/

# 查看任务列表
curl -s http://127.0.0.1:9151/api/picture/list 2>&1
```

---

## 安全建议

1. **设置 MT_API_TOKEN**：在 docker-compose 中设置环境变量，防止未授权推送
   ```yaml
   environment:
     - MT_API_TOKEN=your-secure-token-here
   ```
2. **网络隔离**：mt 容器只暴露 9151 端口，插件通过宿主机 IP 访问
3. **下载目录权限**：确保 `/root/docker/video` 有足够磁盘空间

---

## 更新镜像

```bash
cd /root/.openclaw/workspace/mt-downloader
docker build -t mt-downloader:arm64 .
docker restart mt-downloader
```
