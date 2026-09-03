# SlimGuard PostgreSQL 部署

SlimGuard 的部署数据库已经切换为 PostgreSQL 16。SQLite 只保留给快速单元测试使用；Compose、
默认应用配置和服务器部署均使用 `postgresql+psycopg`。本次切换不复制旧 SQLite 数据，首次启动
会在空 PostgreSQL 数据库中创建完整表结构。

Mem0 的 PostgreSQL/pgvector 与 SlimGuard 主业务库是两个独立边界。不要让 SlimGuard 直接使用
Mem0 自带的数据库：主库保存用户、对话、权威健康记录、关系型记忆和 Trace，Mem0 只保存可重建的
语义索引。

## 首次从 SQLite 切换

在 `/home/ubuntu/slim-guard/.env` 增加以下配置。请在第一次启动 PostgreSQL 容器之前确定密码：

```dotenv
POSTGRES_DB=slim_guard
POSTGRES_USER=slim_guard
POSTGRES_PASSWORD=替换为openssl-rand-hex-32生成的值
POSTGRES_HOST_PORT=15432
```

生成 URL 安全的密码：

```bash
openssl rand -hex 32
```

`POSTGRES_HOST_PORT` 只绑定服务器 `127.0.0.1`，不需要在安全组开放。Compose 会根据上面四项
为 App 构造容器内连接地址，因此无需再手写 `DATABASE_URL`。如果 `.env` 里还留着旧的 SQLite
`DATABASE_URL`，Compose 的显式 PostgreSQL 地址会覆盖它；建议删除旧行，避免人工排查时误解。

然后执行：

```bash
cd /home/ubuntu/slim-guard
git pull --ff-only
docker compose pull postgres
docker compose build app
docker compose up -d postgres
docker compose ps postgres
docker compose run --rm app python -m slim_guard.db.migrate
docker compose up -d app admin-web
docker compose ps
```

预期迁移输出包含 `Applied migrations:`；应用启动时也会幂等检查迁移。确认数据库和应用：

```bash
docker compose exec postgres sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT version FROM schema_migrations ORDER BY version;"'
docker compose exec postgres sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\dt"'
curl -i http://127.0.0.1:18083/health/ready
docker compose logs --tail=150 postgres app
```

`/health/ready` 为 200 表示数据库、用户通道和模型配置都已就绪。如果它因为其他通道配置返回
503，可以先检查日志，再单独用上面的 `psql` 命令确认数据库。

旧的 `slim_guard_data` Docker 卷不会被新 Compose 自动删除，所以旧 SQLite 文件暂时仍可恢复，
但新版本不会再读它。确认不需要回滚后再自行清理；切换过程不要求执行 `down -v`。

## 日常备份

创建压缩格式备份：

```bash
cd /home/ubuntu/slim-guard
mkdir -p backups
docker compose exec -T postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > "backups/slim-guard-$(date +%F-%H%M%S).dump"
```

确认备份文件不是空文件，并将其同步到另一台机器或对象存储。恢复演练应在独立数据库完成，不能
直接覆盖线上库。

## 密码变更注意事项

PostgreSQL 数据卷初始化后，单纯修改 `.env` 的 `POSTGRES_PASSWORD` 不会修改库内已有用户密码。
需要先在数据库内执行 `ALTER ROLE`，再修改 `.env` 并重启 App。测试阶段如果数据库没有任何价值，
也可以明确删除 PostgreSQL 卷后按新密码重新初始化，但这会永久清空全部数据。

## 宿主机直接运行

Compose 将 PostgreSQL 映射到 `127.0.0.1:15432`。在宿主机直接运行 Python 时显式配置：

```dotenv
DATABASE_URL=postgresql+psycopg://slim_guard:URL编码后的密码@127.0.0.1:15432/slim_guard
```

若密码由 `openssl rand -hex 32` 生成，无需额外 URL 编码。生产 App 容器仍应交给 Compose 自动构造
连接地址。
