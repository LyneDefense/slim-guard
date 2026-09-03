# SlimGuard 单机生产部署

这是腾讯云服务器的权威部署入口。目标是只保留一个 Compose 项目、一份服务器配置和一个日常命令，
同时让宿主机 Nginx 继续承载证书与多个站点。

## 运行边界

`deploy/compose.production.yaml` 统一管理五个服务：

- `app`：SlimGuard FastAPI 与 Harness；
- `admin-web`：React 管理页面；
- `slim-guard-db`：SlimGuard 权威业务数据库；
- `mem0`：私有语义记忆 API；
- `mem0-db`：Mem0 的 PostgreSQL 17 + pgvector。

只有 `app` 的 `127.0.0.1:18083` 和 `admin-web` 的 `127.0.0.1:18084` 发布到宿主机。
Mem0 和两个数据库没有宿主机端口，也不需要腾讯云安全组规则。`app` 通过
`http://mem0:8000` 访问 Mem0；宿主机 Nginx 负责公网 80/443、Let's Encrypt 和域名路由。

两个数据库故意保持独立。SlimGuard 数据库保存权威用户、对话、健康记录、关系型记忆和 Trace；
Mem0 数据库属于第三方服务，保存语义向量、API 认证和自身设置。不要为了减少一个容器混用数据库。
Mem0 的 SQLite change history 单独放在第三个小型卷中。三个命名卷都声明为 external：首次切换脚本
会显式复用或创建它们，日常 `compose down` 无权删除。

## 一份服务器配置

第一次准备配置：

```bash
cd /home/ubuntu/slim-guard
git pull --ff-only
cp deploy/env.server.example deploy/.env.server
chmod 600 deploy/.env.server
nano deploy/.env.server
```

所有真实密钥只保存在 `deploy/.env.server`，该文件已被 Git 忽略。不要继续维护仓库根目录 `.env`
和 `/home/ubuntu/mem0/server/.env`；它们只在首次迁移时用于复制现值。

首次从当前服务器切换并保留数据时必须填写：

```dotenv
SLIM_GUARD_DB_VOLUME=slim-guard_slim_guard_postgres_data
MEM0_DB_VOLUME=mem0-dev_postgres_db
MEM0_HISTORY_VOLUME=slim-guard-prod-mem0-history
```

数据库密码必须与旧 `.env` 中已经初始化到数据库里的密码保持一致。不能只生成新密码写进配置，
否则新容器会挂载旧数据卷但无法认证。其他密钥至少包括：

```dotenv
SLIM_GUARD_POSTGRES_PASSWORD=原SlimGuard数据库密码
MEM0_POSTGRES_PASSWORD=原Mem0数据库密码
MEM0_API_KEY=原Mem0的ADMIN_API_KEY
MEM0_JWT_SECRET=原Mem0的JWT_SECRET
ZHIPU_API_KEY=智谱API密钥
ADMIN_USERNAME=admin
ADMIN_PASSWORD=后台密码
MOBILE_AUTH_SECRET=移动端固定签名密钥
```

配置文件禁止重复键。部署脚本会在启动任何服务前检查重复变量、`CHANGE_ME`、密钥长度、文件权限和
Compose 渲染；因此不会再次出现两个 `APP_ENV` 或 6 字符 `MOBILE_AUTH_SECRET` 导致容器循环重启。
为了复用测试阶段已经初始化的数据卷，`APP_ENV=development` 允许沿用旧的短数据库密码；切换到
production 前必须先在数据库中修改角色密码，再把两项数据库密码更新为至少 16 位。

当前测试账号阶段保持：

```dotenv
APP_ENV=development
MOBILE_API_ENABLED=true
MOBILE_DEV_OTP_ENABLED=true
MOBILE_TEST_ACCOUNTS_ENABLED=true
MOBILE_TEST_ACCOUNT_PASSWORD=123456
```

正式上线时改成 production、关闭测试账号与开发验证码，并配置短信 Webhook。

## 宿主机 Nginx

仓库中的 `deploy/nginx/slim-guard.locations.conf` 是宿主机配置的版本化源文件，不属于 Compose。
首次安装：

```bash
sudo install -m 0644 \
  deploy/nginx/slim-guard.locations.conf \
  /etc/nginx/snippets/slim-guard.locations.conf
```

在 `enceladus.online` 对应 HTTPS `server` 块中，删除现有重复的 `/admin/`、`/api/admin/`、
`/api/mobile/`、`/callbacks/wecom/kf` 和 `/health/` location，然后加入一行：

```nginx
include /etc/nginx/snippets/slim-guard.locations.conf;
```

验证后再加载：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

Nginx 是服务器公共入口，配置不需要随每次应用发布重装。日常部署的公网 smoke test 会验证这份配置
仍然有效。

## Mem0 镜像

腾讯云服务器不需要 clone Mem0。首次切换脚本使用已经上传的
`/home/ubuntu/mem0/server` 作为源码输入，生成固定镜像：

```text
slim-guard/mem0:2.0.19-sg1
```

自定义 Dockerfile 会把宽泛的 `mem0ai>=...` 固定为 `mem0ai==2.0.19`，不挂载源码、不启用
`uvicorn --reload`，也不会在容器每次启动时强制重装包。构建完成后，日常发布只检查固定镜像存在，
不会读取 Mem0 源码或访问 GitHub。构建脚本只把白名单中的 Python 源码、迁移和依赖清单复制到临时
上下文，旧 Mem0 `.env` 和 history 数据既不会进入镜像，也不会发送进 Docker 构建上下文。

升级 Mem0 是独立受控操作：先在 Mac 下载并上传新源码，修改 `.env.server` 中
`MEM0AI_VERSION`、`MEM0_IMAGE` 和 `MEM0_SOURCE_DIR`，然后执行：

```bash
./deploy.sh build-mem0
./deploy.sh
```

不要让 Mem0 随 SlimGuard 每次发布自动升级。

## 首次安全切换

确认 `.env.server` 和 Nginx 后执行一次：

```bash
cd /home/ubuntu/slim-guard
./deploy.sh bootstrap --cutover
```

脚本按顺序执行：

1. 验证唯一配置、Compose 和数据卷名称；
2. 从已上传源码构建固定 Mem0 生产镜像，并验证镜像内的 `mem0ai` 版本；
3. 在旧服务保持在线时预构建当前 commit 的后端和管理前端镜像；
4. 分别备份当前 SlimGuard、Mem0 PostgreSQL 和 Mem0 change history；
5. 停止旧的两个 Compose 项目，但绝不删除数据卷；
6. 使用同一数据卷启动 `slim-guard-prod`；
7. 等待两个数据库和 Mem0 健康；
8. 幂等重放 Mem0 的智谱 LLM、embedding-3 和 1024 维向量配置；
9. 执行 SlimGuard 数据库迁移；
10. 启动 API 和管理前端，执行内部及公网 smoke test。

任何一步失败时，脚本停止新 Compose 并按数据库、Mem0、应用的顺序恢复旧容器。旧容器不会在切换
成功后自动删除，便于观察一段时间后再人工清理。切换备份位于 `BACKUP_DIR`。

## 日常一键部署

首次切换完成后，日常只执行：

```bash
cd /home/ubuntu/slim-guard
./deploy.sh
```

脚本会：

1. 拒绝覆盖服务器上未提交的代码修改；
2. `git pull --ff-only`；
3. 使用 Git commit SHA 构建并标记 app/admin 镜像；
4. 备份两个数据库和 Mem0 change history；
5. 启动并检查数据库与 Mem0；
6. 重放 Mem0 配置并运行 SlimGuard 迁移；
7. 更新 app/admin；
8. 验证数据库、Mem0、回环端口和公网 HTTPS；
9. 记录当前和上一版本，用于应用镜像回滚。

如果新 app/admin 已开始更新后健康检查失败，脚本会自动尝试恢复上一版应用镜像；数据库迁移仍不会
反向执行，因此迁移必须保持向后兼容。首次 cutover 失败则由外层脚本恢复整套旧容器。

部署已持有文件锁，同一时间不能重复执行。常用运维入口：

```bash
./deploy.sh status
./deploy.sh logs
./deploy.sh backup
./deploy.sh rollback
```

`rollback` 只切回上一份 app/admin 镜像，不反向执行数据库迁移。数据库迁移必须保持向后兼容；如果
发生不可兼容的数据问题，应停止写入并从部署前备份人工恢复，不能让脚本自动覆盖生产数据。

## 数据与安全

- 备份默认保存到 `/home/ubuntu/backups/slim-guard`，包含两个 PostgreSQL dump 和一致性导出的
  Mem0 history，权限为 600，默认保留 14 天；
- `bootstrap` 和日常部署都不会执行 `docker compose down -v`；
- Mem0 API Key 只在容器内部使用，Mem0 不再监听公网 `8888`；
- Mem0 PostgreSQL 不再监听公网 `8432`；
- SlimGuard PostgreSQL 不再需要宿主机 `15433`；
- 需要排查数据库时使用 `docker compose exec`，不要临时开放安全组端口；
- 旧 Compose、旧网络和旧容器只能在新架构稳定并确认备份可用后清理。

建议观察一段时间并至少验证一次 App 对话、管理后台和企业微信回调。确认不再需要旧容器级回退后，
可以执行精确清理：

```bash
./deploy.sh cleanup-legacy --confirm
```

该命令会再次运行 smoke test，只删除已停止且名称明确的旧容器，并尝试删除三个不再使用的旧网络；
如果任何旧容器仍在运行则拒绝操作。它不会删除任何 Docker volume、数据库备份、旧 `.env` 或
`/home/ubuntu/mem0` 源码目录。
