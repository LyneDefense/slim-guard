# SlimGuard 用户级 Trace 管理后台部署

> 腾讯云服务器现在以 [单机生产部署说明](./SERVER_DEPLOYMENT.md) 为权威入口。管理前端由统一
> `slim-guard-prod` Compose 和 `./deploy.sh` 发布；本文其余内容主要说明路由与认证边界。

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

## 配置与首次部署

后台配置与所有服务共用 `deploy/.env.server`，不要另外维护管理前端配置。密码建议使用密码管理器
生成的长随机值；`APP_ENV=production` 会让会话 Cookie 强制只通过 HTTPS 发送：

```dotenv
APP_ENV=production
ADMIN_WEB_HOST_PORT=18084
ADMIN_USERNAME=admin
ADMIN_PASSWORD=替换为长随机密码
ADMIN_SESSION_TTL_HOURS=12
```

不需要安装 `apache2-utils`，不需要执行 `htpasswd`，也不需要在 Nginx 保存第二份密码。
修改 `ADMIN_PASSWORD` 会同步使所有已有后台会话失效。

将 [`deploy/nginx/slim-guard.locations.conf`](./deploy/nginx/slim-guard.locations.conf) 安装为宿主机
Nginx snippet，并从现有 HTTPS `server` 块 include。不要覆盖证书路径，也不要给企业微信 callback
增加后台 Session 校验。完整安装与首次切换只执行权威文档中的统一入口：

```bash
cd /home/ubuntu/slim-guard
./deploy.sh bootstrap --cutover
```

迁移是幂等的；空 PostgreSQL 会创建完整业务表结构。

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

浏览器打开登录页，并使用 `deploy/.env.server` 中的唯一一套账号密码登录：

```text
https://你的域名/admin/login
```

## 日常升级

统一部署脚本会先备份数据库，再构建、迁移、启动和执行 smoke test：

```bash
cd /home/ubuntu/slim-guard
./deploy.sh
```

查看运行状态：

```bash
./deploy.sh status
./deploy.sh logs
```

所有应用日志会自动携带当前可用的 `trace_id`。管理页面使用数据库中的持久化 Trace 作为事实
来源，因此容器重启、日志轮转和 outbox 重试不会破坏同一条输出链路。

## 安全边界

- 不要把 `18083` 或 `18084` 改成 `0.0.0.0` 绑定；
- Nginx 不配置 Basic Auth，所有 `/api/admin/` 数据接口由后端验证签名 Session；
- 生产环境必须设置 `APP_ENV=production` 并使用 HTTPS；
- Session Cookie 使用 `HttpOnly`、`SameSite=Strict`，生产环境同时使用 `Secure`；
- `deploy/.env.server` 不得提交 Git；
- 后台展示健康数据，账号不得多人共享；
- Agent transcript、工具内容和已完成出站正文默认在保留期后不可逆脱敏，Trace 元数据继续保留。
