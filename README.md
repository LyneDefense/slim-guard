# SlimGuard

SlimGuard 第一阶段是一个 Python 编写的企业微信“微信客服”通道验证服务。

当前能力只有一条：普通微信用户向微信客服发送文本后，服务固定回复：

```text
收到，我已经连接成功。
```

当前版本不调用大模型，也不解析体重、图片、饮食或运动。

## 本地启动

推荐 Python 3.13，代码和测试兼容 Python 3.11+。

```bash
cp .env.example .env
uv sync --dev
uv run uvicorn slim_guard.main:app --host 0.0.0.0 --port 8000 --reload
```

健康检查：

```text
GET http://localhost:8000/health/live
GET http://localhost:8000/health/ready
```

未填写企业微信配置时，`live` 返回 200，`ready` 返回 503，这是预期行为。

## 企业微信配置

在企业微信管理后台创建或选择一个微信客服账号，并配置通过 API 管理。将以下内容写入 `.env`：

```dotenv
WECOM_CORP_ID=企业ID
WECOM_KF_SECRET=微信客服Secret
WECOM_OPEN_KF_ID=客服账号ID
WECOM_CALLBACK_TOKEN=回调Token
WECOM_CALLBACK_AES_KEY=EncodingAESKey
```

首次开通时可以分两次填写：先配置 `WECOM_CORP_ID`、`WECOM_CALLBACK_TOKEN` 和
`WECOM_CALLBACK_AES_KEY`，启动服务并让企业微信完成回调 URL 验证；验证完成、后台显示
Secret 后，再补充 `WECOM_KF_SECRET` 和 `WECOM_OPEN_KF_ID`。补全前 `/health/ready` 返回 503
属于预期行为。

拿到 Corp ID 和微信客服 Secret 后，可以运行下面的命令查询真正的 `open_kfid`：

```bash
uv run python -m slim_guard.tools.list_kf_accounts
```

把输出中对应客服账号的 `wk...` 值填入 `WECOM_OPEN_KF_ID`。

将回调地址设置为：

```text
https://你的公网域名/callbacks/wecom/kf
```

公网地址必须为 HTTPS。服务器出口 IP 还需要满足企业微信后台的可信 IP 要求。

## 数据

默认 SQLite 文件位于：

```text
data/slim_guard.sqlite3
```

只保存同步 cursor、消息 ID、消息类型、去敏所需身份字段和出站状态，不保存入站文本正文。删除 SQLite 文件会丢失去重状态，可能导致历史消息被重新处理，真机环境不要随意删除。

## 测试与检查

```bash
uv run ruff check .
uv run mypy src
uv run pytest
```

测试会模拟企业微信服务器，不需要真实 Corp ID 或 Secret。

## Docker

```bash
docker compose up --build
```

Compose 会读取当前目录的 `.env`，并把 SQLite 数据保存在 Docker 命名卷 `slim_guard_data`。
默认只把服务绑定到宿主机的 `127.0.0.1:18083`，供同机 Nginx 反向代理，不直接暴露公网端口。

## 设计文档

- [Phase 1 实现设计](./PHASE1_PYTHON_CHANNEL_SPIKE.md)
- [完整技术设计](./TECHNICAL_DESIGN.md)
- [MVP 产品范围](./MVP_0_WECHAT_BRIDGE.md)
