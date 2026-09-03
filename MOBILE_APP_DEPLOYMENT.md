# SlimGuard 双端 App 部署与构建

这份文档覆盖腾讯云后端升级、短信登录、Nginx、Mem0、iOS/Android 构建以及上线前验收。
App 不直接连接数据库或 Mem0，只通过 FastAPI 的 `/api/mobile/v1/*` 使用现有 Harness Agent；
管理后台认证、移动端认证和企业微信回调都由后端统一负责。

## 1. 服务器升级

先在应用停止后备份 SQLite，再更新代码：

```bash
cd /home/ubuntu/slim-guard
mkdir -p backups
docker compose stop app
docker compose cp app:/app/data/slim_guard.sqlite3 \
  "backups/slim_guard-$(date +%F-%H%M%S).sqlite3"
docker compose start app
git pull --ff-only
```

为生产环境生成独立密钥：

```bash
openssl rand -hex 32
```

把结果填进服务器 `/home/ubuntu/slim-guard/.env`。不要复用管理员密码、智谱 Key 或 Mem0 Key：

```dotenv
APP_ENV=production
MOBILE_API_ENABLED=true
MOBILE_AUTH_SECRET=粘贴上面生成的64位十六进制随机值
MOBILE_ACCESS_TOKEN_TTL_MINUTES=15
MOBILE_REFRESH_TOKEN_TTL_DAYS=30
MOBILE_OTP_TTL_SECONDS=300
MOBILE_OTP_RESEND_SECONDS=60
MOBILE_OTP_HOURLY_LIMIT=5
MOBILE_DEV_OTP_ENABLED=false
MOBILE_SMS_WEBHOOK_URL=https://你的短信适配服务/slimguard/otp
MOBILE_SMS_WEBHOOK_TOKEN=短信适配服务使用的随机Bearer密钥
MOBILE_WECOM_BINDING_TTL_MINUTES=10
```

`APP_ENV=production` 会强制要求关闭开发验证码并配置短信 Webhook。服务端向 Webhook 发送：

```json
{
  "phone": "+8613800138000",
  "code": "123456",
  "expires_in_seconds": 300,
  "purpose": "slim_guard_login"
}
```

请求头是 `Content-Type: application/json`；配置 Token 后还会带
`Authorization: Bearer <MOBILE_SMS_WEBHOOK_TOKEN>`。Webhook 返回任意 2xx 表示短信平台已接收，
其他状态会让 App 得到“验证码发送失败”。建议用腾讯云短信实现这个很薄的适配服务，并在模板中
标注验证码有效期。SlimGuard 自身已经做了 60 秒重发限制、每手机号每小时 5 次限制和 5 次错误锁定。

构建、迁移、启动：

```bash
docker compose build app
docker compose run --rm app python -m slim_guard.db.migrate
docker compose up -d app
docker compose ps
docker compose logs --tail=100 app
```

迁移会新增移动登录、Session、幂等请求、设备和微信绑定表，不会删除现有健康记录与记忆。

## 2. Nginx

App 使用与现有域名相同的 HTTPS 后端即可。将下面的 location 放进当前 `listen 443 ssl`
的 `server` 块；如果已经有一个覆盖所有路径的 `location /` 转发到 18083，则无需重复添加。

```nginx
client_max_body_size 12m;

location ^~ /api/mobile/ {
    proxy_pass http://127.0.0.1:18083;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 90s;
}
```

Nginx 不做 Basic Auth，也不保存移动端密钥。Access Token、Refresh Session、限流和用户隔离
都由 FastAPI 处理。`client_max_body_size` 要放在对应 `server` 或更高层，否则饮食照片可能先被
Nginx 以 413 拒绝。

验证配置：

```bash
sudo nginx -t
sudo systemctl reload nginx
curl -i https://你的-api-域名/health/live
curl -i https://你的-api-域名/api/mobile/v1/me
```

第二个请求没有 Token 时应返回 `401`；返回 `404` 表示 Nginx 没有把移动 API 转发给后端，
返回 `503` 表示 `MOBILE_API_ENABLED` 或 `MOBILE_AUTH_SECRET` 没有生效。

## 3. Mem0

继续使用已经验证成功的私有 Mem0：

```dotenv
MEMORY_SEMANTIC_ENABLED=true
MEM0_BASE_URL=http://你的Mem0可达地址:8888
MEM0_API_KEY=你的ADMIN_API_KEY
MEM0_NAMESPACE=slim_guard
```

若 Mem0 和 SlimGuard 不在同一个 Compose 网络，容器里的 `127.0.0.1` 指向 SlimGuard 容器自身，
不能用作 Mem0 地址。可以使用宿主机内网地址，或把两个服务加入同一个 Docker network 后用
服务名访问。智谱 `embedding-3` 是 1024 维时，Mem0 的 `vector_store.config.embedding_model_dims`
也必须保持 1024。Mem0 不可用时，本地关系型记忆仍是权威数据源；语义召回会降级，不应阻断聊天。

## 4. 配置移动端

在开发机执行：

```bash
cd mobile-app
cp .env.example .env.local
npm ci
```

`.env.local` 只写公网 API Origin，不带 `/api/mobile/v1`：

```dotenv
EXPO_PUBLIC_API_BASE_URL=https://你的-api-域名
```

包标识当前是：

- iOS：`com.slimguard.app`
- Android：`com.slimguard.app`

正式上架前如需更换，必须在第一次创建商店应用之前修改 `app.json`，之后不能随意变化。

## 5. EAS 构建 iOS 与 Android

EAS 会管理原生构建环境，适合当前 Expo Managed 项目：

```bash
cd mobile-app
npx eas-cli@latest login
npx eas-cli@latest init
npx eas-cli@latest env:set --name EXPO_PUBLIC_API_BASE_URL \
  --value https://你的-api-域名 --environment preview --visibility plaintext
npx eas-cli@latest env:set --name EXPO_PUBLIC_API_BASE_URL \
  --value https://你的-api-域名 --environment production --visibility plaintext
npx eas-cli@latest build --platform all --profile preview
```

`eas init` 会将项目 ID 写入 App 配置；这也是获取 Expo Push Token 所必需的。Preview 包用于真机
验收。API 地址不是密钥，并且会被编译进客户端，所以按 Expo 要求使用 `plaintext`；真正的服务端
密钥绝不能使用 `EXPO_PUBLIC_` 前缀。`eas.json` 已把三个构建 Profile 分别绑定到对应 EAS
Environment。确认无误后构建正式包：

```bash
npx eas-cli@latest build --platform all --profile production
```

iOS 需要 Apple Developer Program 账号和可用的签名/Provisioning Profile；Android 需要 Google
Play Console 账号和上传密钥。EAS 可以首次引导生成凭据。生成后提交：

```bash
npx eas-cli@latest submit --platform ios --profile production
npx eas-cli@latest submit --platform android --profile production
```

如果所在网络无法稳定访问 Expo/EAS，可以执行 `npx expo prebuild` 生成 `ios/` 与 `android/`，
在 macOS/Xcode 构建 iOS，在 Android Studio/Gradle 构建 Android。执行 prebuild 后产生的是需要
自行维护的原生工程，应单独提交；本 MVP 默认保持 Managed Workflow。

## 6. 微信身份绑定

用户在 App 的“我的 → 连接微信”生成一次性 8 位码，再把完整的 `SG-XXXXXXXX` 单独发送给
SlimGuard 微信客服。绑定码使用服务端 HMAC 保存、10 分钟过期、只能使用一次，也不暴露微信
external_userid。若 App 和微信两边都已有 Agent 历史，服务端会拒绝自动覆盖并显示冲突；否则会
把手机号登录和设备安全迁到已有记录的身份。普通对话仍全部进入 model-first Harness，只有严格的
绑定协议消息在 Agent 前由身份层处理。

## 7. 通知策略

保存提醒后，App 会请求通知权限并在设备上安排本地称重、饮食和晚间复盘提醒，即使服务端暂时
不可达也能触发。完成 EAS 初始化后，真机会同时向后端登记 Expo Push Token。后端已经提供可替换的
Expo/APNs/FCM Provider 边界；当前 MVP 的日程提醒以本地通知为主，服务端远程推送可在后续用于
召回、运营或跨设备同步，不能在没有用户授权时发送。

## 8. 上线前验收

按顺序在 iPhone 和至少一台主流 Android 真机完成：

1. 国内手机号获取验证码、错误验证码、60 秒内重复请求和 Token 自动刷新；
2. 发送文字、相机照片、相册照片，切断网络后发送，再联网确认只产生一条记录；
3. 重复发送相同业务内容，确认 Agent 用数据库事实回复但不会重复写入；
4. 检查“今天”“趋势”和“我的记忆”与管理后台同一用户的数据一致；
5. 修改三个本地提醒，重启 App 后检查通知仍存在；
6. 绑定一个有历史的微信用户，确认 App 自动刷新身份并读取原来的记忆；
7. 模拟 App/微信两边都有历史，确认提示冲突且任何一边都没被覆盖；
8. 退出登录后 Refresh Token 失效；测试账号执行永久删除后，旧 Token 返回 401；
9. 检查相机、相册、通知权限拒绝后的提示，以及小屏、深色系统设置下的可用性；
10. 在管理后台按用户查看 App 对话 Trace，确认 `channel_id=mobile` 且模型/工具链路完整。

商店提交前还需准备隐私政策公网 URL、服务条款、客服邮箱、应用截图、Apple 隐私问卷和 Google
Data Safety 表。健康信息应按敏感数据申报；产品文案必须保持“日常管理，不替代医疗诊断”的边界。
