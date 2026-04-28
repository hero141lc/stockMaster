# Linux 部署说明

## 1. 密钥安全（必读）

若在聊天、仓库或截图中暴露过以下信息，请在对应控制台**立即轮换或作废**后再部署：

- 飞书 **App Secret**、事件订阅 **Verification Token**、**Encrypt Key**
- 密塔 **API Key**
- 飞书 **Webhook** 地址（含 hook 路径中的令牌）

本项目仅从**环境变量**读取密钥，请勿把真实 `.env` 提交到 Git。

## 2. 服务器准备

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv nginx
cd /opt
sudo mkdir -p feishu-news-bot && sudo chown $USER:$USER feishu-news-bot
```

将本仓库同步到 `/opt/feishu-news-bot`，创建虚拟环境并安装依赖：

```bash
cd /opt/feishu-news-bot
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

复制 `.env.example` 为 `.env`，填写 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_VERIFICATION_TOKEN`、（可选）`FEISHU_ENCRYPT_KEY`、`METASO_API_KEY`、`FEISHU_CHAT_ID` 等。

### 事件订阅：长连接（默认）

1. 飞书开放平台 → 应用 → **事件订阅**，选择 **使用长连接接收事件**（无需填写公网请求地址）。
2. 订阅 `im.message.receive_v1`（接收消息）。
3. 环境变量保持 `FEISHU_WS_ENABLED=true`、`FEISHU_HTTP_EVENTS_ENABLED=false`。
4. 启动本服务后，进程内会拉起 **lark-oapi WebSocket 子进程**；`GET /health` 中 `feishu_ws_process_alive` 为 `true` 表示长连接进程在跑。

### 事件订阅：HTTP 回调（可选）

若改用「将事件发送至开发者服务器」，需公网 **HTTPS** 与固定路径：

- 请求地址：`https://<你的域名>/feishu/events`
- 设置 `FEISHU_HTTP_EVENTS_ENABLED=true`
- 可与长连接并存（一般不必；注意重复处理消息时需自行规避）

### 企业微信（可选）

1. **推送（简报/预警）**：在群内添加「群机器人」，复制 Webhook 地址中的 `key`，或整段 URL 写入 `WECOM_WEBHOOK_KEY` / `WECOM_WEBHOOK_URL`。
2. **交互（发文字触发密塔搜索）**：使用**自建应用** → 开启「接收消息」，回调 URL 填 `https://<你的域名>/wecom/callback`，与 `.env` 中 `WECOM_CORP_ID`、`WECOM_CALLBACK_TOKEN`、`WECOM_ENCODING_AES_KEY` 与管理台一致（需 `pip install wechatpy xmltodict`）。
3. **渠道选择**：环境变量 `IM_PROVIDER=feishu` | `wecom` | `both`。仅企微时设 `wecom` 并关闭飞书长连接（`FEISHU_WS_ENABLED=false`）即可不依赖飞书。

## 3. systemd

```bash
sudo cp deploy/feishu-news-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now feishu-news-bot
sudo systemctl status feishu-news-bot
```

按需修改 `User`/`Group`/`WorkingDirectory`/`EnvironmentFile`/`ExecStart` 路径。

**长连接注意**：每个进程会启动一条 WebSocket；请勿使用 `uvicorn --workers` 多进程，否则可能重复建连。`ExecStart` 中保持**单 worker**（示例已如此）。

## 4. HTTPS 与 Nginx

**仅在使用 HTTP 事件回调时**，必须配置公网 HTTPS 反向代理。若只用长连接，可只监听内网或本机 `127.0.0.1:8000`，不强制上 Nginx。

使用 [Certbot](https://certbot.eff.org/) 或托管证书，将 `deploy/nginx-snippet.conf` 合并进站点配置并 `nginx -t` 后 `systemctl reload nginx`。

## 5. 验证

- `curl -s http://127.0.0.1:8000/health`（或 `https://<域名>/health`）应返回 JSON 且 `ok: true`；长连接模式下应见 `feishu_ws_process_alive: true`。
- 使用 **HTTP 回调** 时，在飞书开发者后台保存事件订阅应能通过 URL 校验。

## 6. 行为说明

- **定时简报**：由 `DIGEST_TIMES` 与 `DIGEST_QUERIES` 控制；同一链接会去重，避免重复推送。
- **关键词预警**：按 `ALERT_INTERVAL_MINUTES` 轮询，分批使用 `ALERT_KEYWORDS`；命中新链接即推送。
- **群内问答（RAG）**：用户 @机器人或私聊发送文本，服务先用密塔检索，再把结果作为上下文交给 LLM 生成回答（`REPLY_MODE=rag`，需配置 `LLM_API_KEY` 等）。若设为 `REPLY_MODE=search` 或未配 LLM，则仍为密塔结果列表。
