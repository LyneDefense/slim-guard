# SlimGuard PostgreSQL 部署

> 本文保留数据库原理和人工恢复说明。腾讯云服务器的统一编排、备份与日常发布以
> [单机生产部署说明](./SERVER_DEPLOYMENT.md) 和 `./deploy.sh` 为准；不要再在服务器直接运行本文的
> 旧版根目录 Compose 发布命令。

SlimGuard 的部署数据库已经切换为 PostgreSQL 16。SQLite 只保留给快速单元测试使用；Compose、
默认应用配置和服务器部署均使用 `postgresql+psycopg`。本次切换不复制旧 SQLite 数据，首次启动
会在空 PostgreSQL 数据库中创建完整表结构。

Mem0 的 PostgreSQL/pgvector 与 SlimGuard 主业务库是两个独立边界。不要让 SlimGuard 直接使用
Mem0 自带的数据库：主库保存用户、对话、权威健康记录、关系型记忆和 Trace，Mem0 只保存可重建的
语义索引。

## 首次从 SQLite 或旧 Compose 切换

在 `/home/ubuntu/slim-guard/deploy/.env.server` 设置下列变量。请在第一次初始化新数据卷之前确定
密码：

```dotenv
SLIM_GUARD_POSTGRES_DB=slim_guard
SLIM_GUARD_POSTGRES_USER=slim_guard
SLIM_GUARD_POSTGRES_PASSWORD=替换为openssl-rand-hex-32生成的值
SLIM_GUARD_DB_VOLUME=slim-guard-prod-db
```

生成 URL 安全的密码：

```bash
openssl rand -hex 32
```

生产 Compose 不把数据库端口发布到宿主机或公网。它会为 App 构造容器内连接地址，因此无需手写
`DATABASE_URL`。

若当前服务器已经使用 `slim-guard_slim_guard_postgres_data`，首次统一切换必须把
`SLIM_GUARD_DB_VOLUME` 改成这个现有卷名，并保持旧数据库密码不变。切换脚本会检查卷名、先备份、
停止旧容器再挂载同一数据卷；它不会删除数据。

配置完整后执行统一切换：

```bash
cd /home/ubuntu/slim-guard
./deploy.sh bootstrap --cutover
```

脚本会幂等迁移并执行健康检查。确认数据库和应用：

```bash
./deploy.sh status
curl -i http://127.0.0.1:18083/health/ready
./deploy.sh logs
```

`/health/ready` 为 200 表示数据库、用户通道和模型配置都已就绪。如果它因为其他通道配置返回
503，可以先通过 `./deploy.sh logs` 检查具体依赖。

旧的 SQLite 文件和旧容器不会被自动删除。确认不需要回滚后再自行清理；任何部署或切换过程都不
执行 `down -v`。

## 日常备份

日常部署会自动备份 SlimGuard 与 Mem0 两个数据库。也可以单独执行：

```bash
cd /home/ubuntu/slim-guard
./deploy.sh backup
```

脚本会拒绝空备份，默认写到 `/home/ubuntu/backups/slim-guard` 并保留 14 天。仍应将备份同步到
另一台机器或对象存储；恢复演练必须在独立数据库完成，不能直接覆盖线上库。

## 密码变更注意事项

PostgreSQL 数据卷初始化后，单纯修改 `.env.server` 的 `SLIM_GUARD_POSTGRES_PASSWORD` 不会修改
库内已有用户密码。需要先在数据库内执行 `ALTER ROLE`，再修改配置并运行 `./deploy.sh`。测试阶段
即使数据不重要，也不要让自动部署脚本删除数据卷；若确需清空，应先明确核对目标卷并人工操作。

## 本地开发时宿主机直接运行

仓库根目录的本地开发 Compose 会将 PostgreSQL 映射到 `127.0.0.1:15432`。在 Mac 上直接运行
Python 时显式配置：

```dotenv
DATABASE_URL=postgresql+psycopg://slim_guard:URL编码后的密码@127.0.0.1:15432/slim_guard
```

若密码由 `openssl rand -hex 32` 生成，无需额外 URL 编码。生产 App 容器仍应交给 Compose 自动构造
连接地址。
