# SlimGuard 用户级 Trace 管理后台部署

管理后台由两个独立服务组成：FastAPI 提供 `/api/admin/*`，React SPA 由独立的
`admin-web` 容器提供。两个容器只绑定宿主机回环地址，宿主机 Nginx 只负责公网 HTTPS、
静态前端转发和 API 反向代理；认证统一由 FastAPI 负责。

## 路由

- `/admin/`：React 管理页面；
- `/api/admin/auth/login`：后端登录并签发 Session Cookie；
- `/api/admin/`：需要后端 Session、按用户隔离的只读管理 API；
- `/callbacks/wecom/kf`：企业微信原有回调，不加管理后台认证；
- `/health/`：原有健康检查。

浏览器先访问用户列表，再进入一个用户查看 Trace、记忆、健康记录和提醒。敏感 Trace、记忆
和健康记录详情的读取会写入 `admin_audit_events`。API 不返回原始 `external_userid`，只返回
不可逆的短哈希引用。

## 首次部署

在拉取代码之前先备份正在使用的 SQLite。下面的复制在停止应用后执行，避免复制到不一致的
SQLite/WAL 状态：

```bash
cd /home/ubuntu/slim-guard
mkdir -p backups
docker compose stop app
docker compose cp app:/app/data/slim_guard.sqlite3 \
  "backups/slim_guard-$(date +%F-%H%M%S).sqlite3"
docker compose start app
```

然后更新代码：

```bash
git pull --ff-only
```

在 `.env` 中增加后台端口和唯一一套后台凭据。密码建议使用密码管理器生成的长随机值；
`APP_ENV=production` 会让会话 Cookie 强制只通过 HTTPS 发送：

```dotenv
APP_ENV=production
ADMIN_WEB_HOST_PORT=18084
ADMIN_USERNAME=admin
ADMIN_PASSWORD=替换为长随机密码
ADMIN_SESSION_TTL_HOURS=12
```

不需要安装 `apache2-utils`，不需要执行 `htpasswd`，也不需要在 Nginx 保存第二份密码。
修改 `ADMIN_PASSWORD` 会同步使所有已有后台会话失效。

将 `deploy/nginx/slim-guard.conf.example` 中的 `/admin/` 和 `/api/admin/` location 合并进
服务器现有 HTTPS `server` 块。不要覆盖现有证书路径，也不要给企业微信 callback 增加
后台 Session 校验。

构建新镜像并显式执行数据库迁移：

```bash
docker compose build
docker compose run --rm app python -m slim_guard.db.migrate
docker compose up -d
docker compose ps
```

迁移是幂等的；应用启动时也会自动执行一次。首次升级会创建 `schema_migrations`、
`interaction_traces`、`trace_spans` 和 `admin_audit_events`，不会删除现有记录。

验证并加载 Nginx：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

验证后端和前端：

```bash
curl https://你的域名/health/live
curl -I https://你的域名/admin/
```

浏览器打开登录页，并使用 `.env` 中的唯一一套账号密码登录：

```text
https://你的域名/admin/login
```

## 日常升级

涉及数据库模型的升级仍建议先执行上述 SQLite 备份。一般升级命令：

```bash
cd /home/ubuntu/slim-guard
git pull --ff-only
docker compose build
docker compose run --rm app python -m slim_guard.db.migrate
docker compose up -d
docker compose ps
sudo nginx -t
sudo systemctl reload nginx
```

查看运行状态：

```bash
docker compose logs --tail=200 app
docker compose logs --tail=100 admin-web
```

所有应用日志会自动携带当前可用的 `trace_id`。管理页面使用数据库中的持久化 Trace 作为事实
来源，因此容器重启、日志轮转和 outbox 重试不会破坏同一条输出链路。

## 安全边界

- 不要把 `18083` 或 `18084` 改成 `0.0.0.0` 绑定；
- Nginx 不配置 Basic Auth，所有 `/api/admin/` 数据接口由后端验证签名 Session；
- 生产环境必须设置 `APP_ENV=production` 并使用 HTTPS；
- Session Cookie 使用 `HttpOnly`、`SameSite=Strict`，生产环境同时使用 `Secure`；
- `.env` 不得提交 Git；
- 后台展示健康数据，账号不得多人共享；
- Agent transcript、工具内容和已完成出站正文默认在保留期后不可逆脱敏，Trace 元数据继续保留。
