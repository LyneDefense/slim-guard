# SlimGuard Mobile

SlimGuard Mobile 是同一套后端的 iOS/Android 客户端，使用 Expo SDK 57 和 React Native。
业务语义仍由服务端 Harness Agent 与 model-first Memory 负责；客户端只负责交互、鉴权、
安全保存刷新令牌、图片预处理和离线可靠投递。

## 功能

- 手机号验证码登录，短期 Access Token + 可轮换 Refresh Token；
- 开发环境可启用 5 个隔离的账号密码测试用户，正式环境强制关闭；
- “今天 / 教练 / 趋势 / 我的”四个原生页面；
- 自然语言和相机/相册饮食图片对话；
- 体重、体脂、饮食、运动记录与趋势；
- 目标和长期记忆的白话展示；
- 本地称重、饮食、晚间复盘提醒；
- 离线文字/图片队列，联网后用幂等键自动补发；
- 一次性绑定码连接已有微信客服身份；
- 双重确认的账号及服务端数据删除。

## 本地运行

要求 Node.js 22.13 或更高版本。首次运行：

```bash
cd mobile-app
cp .env.example .env.local
npm ci
npm run start
```

将 `.env.local` 的地址改为手机能访问的后端地址：

```dotenv
EXPO_PUBLIC_API_BASE_URL=https://你的-api-域名
```

真机不能使用 `127.0.0.1` 访问电脑。生产包必须使用公网 HTTPS；本地调试可以使用同一局域网
地址或 HTTPS 隧道。然后使用 Expo Go 扫码，或执行 `npm run ios` / `npm run android`。

后端开发环境应启用 Mobile API 和开发验证码：

```dotenv
APP_ENV=development
MOBILE_API_ENABLED=true
MOBILE_AUTH_SECRET=至少32字符且不要提交到Git的随机值
MOBILE_DEV_OTP_ENABLED=true
MOBILE_TEST_ACCOUNTS_ENABLED=true
MOBILE_TEST_ACCOUNT_PASSWORD=123456
```

开发验证码会直接显示在登录页。生产环境必须关闭这个能力并接入短信 Webhook。
测试账号是 `test1` 到 `test5`，密码统一为 `123456`；每个账号有独立数据，登录后可以在
“我的 → 称呼”修改名字。`APP_ENV=production` 会拒绝启用测试账号。

## 静态校验

```bash
npm run typecheck
npm run bundle:ios
npm run bundle:android
```

完整部署和 EAS 构建步骤见仓库根目录的
[`MOBILE_APP_DEPLOYMENT.md`](../MOBILE_APP_DEPLOYMENT.md)。

第一次在 Mac 上安装 Expo、通过 Xcode 图形界面启动 iPhone Simulator、调试并构建双端包，见
[`MOBILE_APP_MAC_GUIDE.md`](../MOBILE_APP_MAC_GUIDE.md)。
