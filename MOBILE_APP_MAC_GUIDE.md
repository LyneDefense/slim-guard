# SlimGuard App：Mac 模拟器、Expo 与双端构建完整指南

本文面向第一次在 Mac 上运行 SlimGuard App 的开发者，覆盖：

- 完全通过 macOS/Xcode 图形界面启动 iPhone Simulator；
- 安装项目依赖、Expo CLI 和 Expo Go；
- 连接本地或腾讯云后端；
- 在模拟器中启动、刷新和调试 SlimGuard；
- 校验 JavaScript Bundle；
- 使用 EAS Build 生成模拟器包、测试包和商店正式包；
- Android 模拟器与 Android 包的对应流程；
- 常见故障的排查方法。

本文以仓库当前版本为准：

- Expo SDK：57
- React Native：0.86.3
- Node.js：22.13 或更高版本
- iOS Bundle Identifier：`com.slimguard.app`
- Android Package：`com.slimguard.app`
- 移动端目录：`mobile-app`

> 当前这台 Mac 已安装 Xcode 26.5、iOS 26.5 Simulator Runtime、iPhone 17 Pro 模拟器和
> Expo Go。以后通常可以直接从“第 2 节”开始。

## 1. 先理解四种运行和构建方式

| 方式 | 用途 | 是否生成安装包 | 是否需要付费开发者账号 |
| --- | --- | --- | --- |
| Expo Go + Metro | 日常开发、快速看界面和业务逻辑 | 否 | 否 |
| iOS Simulator Development Build | 测试 Expo Go 不完整支持的原生能力 | 是，仅供模拟器 | 否 |
| Preview Build | 安装到真实测试手机验收 | 是 | iOS 通常需要 Apple Developer Program |
| Production Build | TestFlight、App Store、Google Play | 是 | iOS/Google Play 上架需要对应开发者账号 |

平时优先使用 Expo Go。通知、原生配置、签名和真机行为需要更完整验证时，再使用 Development
Build 或 Preview Build。

### Expo SDK、Expo CLI、Expo Go、EAS CLI 分别是什么

- **Expo SDK**：项目代码依赖，已经写在 `mobile-app/package.json` 中。
- **Expo CLI**：启动 Metro、打开模拟器和检查项目的命令行工具。它随项目依赖安装，不需要全局安装。
- **Expo Go**：安装在虚拟 iPhone 或真实手机里的调试容器，负责加载 Metro 提供的 JavaScript。
- **EAS CLI**：连接 Expo 云构建服务，用来生成模拟器包、测试包和正式包。

## 2. 通过 Mac 图形界面启动 iPhone 模拟器

这一节不需要命令行。

### 2.1 从 Xcode 打开 Simulator

1. 打开 macOS 的“应用程序”目录。
2. 双击 **Xcode**。
3. 如果首次启动出现许可协议或“Install additional components”，按提示完成。
4. 在屏幕顶部菜单栏选择 **Xcode → Open Developer Tool → Simulator**。
5. Simulator 窗口打开后，选择顶部菜单栏：
   **File → Open Simulator → iOS 26.5 → iPhone 17 Pro**。
6. 等待虚拟 iPhone 出现锁屏或主屏幕。

也可以按 `Command + Space` 打开 Spotlight，输入 `Simulator`，直接打开 Simulator App。

### 2.2 如果看不到任何 iPhone

先检查模拟器运行时：

1. 打开 Xcode。
2. 选择 **Xcode → Settings → Components**。
3. 找到一个 iOS Simulator Runtime，例如 **iOS 26.5**。
4. 如果右侧显示下载按钮，点击下载并等待安装完成。

如果运行时已经安装，但仍然没有设备：

1. 在 Xcode 顶部菜单选择 **Window → Devices and Simulators**。
2. 打开 **Simulators** 标签页。
3. 点击左下角的 `+`。
4. Device Type 选择一个 iPhone，例如 iPhone 17 Pro。
5. OS Version 选择已经安装的 iOS Runtime。
6. 点击 **Create**。
7. 回到 Simulator，通过 **File → Open Simulator** 打开刚创建的设备。

### 2.3 退出和重新启动

- 关闭窗口只会隐藏 Simulator，不一定关闭虚拟设备。
- 完全退出使用 **Simulator → Quit Simulator** 或 `Command + Q`。
- 下次使用时，通过 Spotlight 搜索 `Simulator` 即可重新打开。
- 如果模拟器异常，可在菜单中选择 **Device → Shut Down**，再重新打开。
- 只有确认不需要模拟器数据时，才使用 **Device → Erase All Content and Settings**；它会清除
  模拟器内的 App、登录状态和本地数据。

## 3. 安装 Node.js、项目依赖和 Expo

### 3.1 Node.js

SlimGuard 要求 Node.js 22.13 或更高版本。这台 Mac 当前已经满足要求。换新电脑时可以从
[Node.js 官网](https://nodejs.org/)安装 Node.js 22 LTS，然后重新打开终端。

检查版本：

```bash
node --version
npm --version
```

### 3.2 安装项目依赖

打开 macOS 的“终端”App，执行：

```bash
cd /Users/pinjhu/work/personal/my-projects/slim-guard/mobile-app
npm ci
```

`npm ci` 会严格按照 `package-lock.json` 安装依赖，其中已经包含 Expo SDK 和项目使用的 Expo
CLI。通常不要再全局安装旧的 `expo-cli`。

适合重新执行 `npm ci` 的情况：

- 第一次克隆项目；
- `package.json` 或 `package-lock.json` 更新；
- `node_modules` 损坏；
- 切换到包含依赖升级的 Git 分支。

### 3.3 安装 Expo Go

iOS Simulator 没有普通的 App Store 安装流程。最简单的方法是：

1. 先按第 2 节启动 iPhone Simulator。
2. 在 `mobile-app` 目录运行 `npm run ios`。
3. 如果模拟器里还没有 Expo Go，Expo CLI 会自动下载并安装。
4. 第一次可能需要等待；后续会直接复用已经安装的 Expo Go。

不需要为了使用 Expo Go 注册 Apple Developer Program，也不需要给模拟器配置代码签名。

## 4. 配置 App 连接后端

移动端只通过 HTTPS/HTTP API 访问 SlimGuard 后端，不直接连接 PostgreSQL 或 Mem0。

### 4.1 创建移动端配置

进入移动端目录：

```bash
cd /Users/pinjhu/work/personal/my-projects/slim-guard/mobile-app
cp .env.example .env.local
```

如果 `.env.local` 已经存在，不要重复覆盖，直接编辑它。

`.env.local` 只需要配置 API Origin：

```dotenv
EXPO_PUBLIC_API_BASE_URL=https://你的-api-域名
```

注意：

- 地址末尾不要写 `/api/mobile/v1`；App 会自己追加接口路径。
- 公网服务器应使用 HTTPS。
- `EXPO_PUBLIC_*` 会进入客户端 Bundle，只能放公开配置，绝不能放智谱 Key、Mem0 Key、数据库
  密码或后端认证密钥。
- 修改 `.env.local` 后，应停止并重新启动 Metro。

### 4.2 方案 A：连接腾讯云后端（推荐做完整联调时使用）

`.env.local` 写腾讯云上的 SlimGuard HTTPS 域名：

```dotenv
EXPO_PUBLIC_API_BASE_URL=https://你的-api-域名
```

先检查后端：

```bash
curl -i https://你的-api-域名/health/live
curl -i https://你的-api-域名/api/mobile/v1/me
```

正确结果是：

- `/health/live` 返回 `200`；
- 未携带登录 Token 的 `/api/mobile/v1/me` 返回 `401`。

其他常见结果：

- `404`：Nginx 没有把 `/api/mobile/` 转发给 SlimGuard；
- `503`：服务器没有启用 Mobile API，或 `MOBILE_AUTH_SECRET` 未生效；
- `502`：Nginx 无法连接后端容器；
- 验证码发送失败：短信 Webhook 没配置成功或短信平台拒绝了请求。

服务器完整配置见 [MOBILE_APP_DEPLOYMENT.md](MOBILE_APP_DEPLOYMENT.md)。

### 4.3 方案 B：连接 Mac 本地 Docker 后端（适合不发真实短信的开发）

iOS Simulator 中的 `127.0.0.1` 指向这台 Mac，因此可以直接访问 Docker 映射到宿主机的端口。

项目根目录 `.env` 的开发配置至少应包含：

```dotenv
APP_ENV=development
DATABASE_URL=postgresql+psycopg://slim_guard:slim_guard-local-only@postgres:5432/slim_guard
MOBILE_API_ENABLED=true
MOBILE_AUTH_SECRET=至少32字符的本地随机值
MOBILE_DEV_OTP_ENABLED=true
MOBILE_TEST_ACCOUNTS_ENABLED=true
MOBILE_TEST_ACCOUNT_PASSWORD=123456
```

还要保留项目正常聊天所需的模型配置。随机密钥可用以下命令生成，然后复制到 `.env`：

```bash
openssl rand -hex 32
```

启动本地后端：

```bash
cd /Users/pinjhu/work/personal/my-projects/slim-guard
docker compose up -d postgres
docker compose run --rm app python -m slim_guard.db.migrate
docker compose up -d --build app
curl -i http://127.0.0.1:18083/health/ready
```

移动端 `.env.local` 使用 Compose 暴露的 `18083`：

```dotenv
EXPO_PUBLIC_API_BASE_URL=http://127.0.0.1:18083
```

开发模式获取验证码时，登录页会自动填入测试验证码，不需要腾讯云短信。生产环境必须关闭
`MOBILE_DEV_OTP_ENABLED` 和 `MOBILE_TEST_ACCOUNTS_ENABLED`。

启用测试账号后，登录页默认显示账号密码入口：

- 账号：`test1`、`test2`、`test3`、`test4`、`test5`；
- 密码：统一为 `123456`；
- 5 个账号拥有独立的用户、对话、记录和记忆；
- 登录后在“我的 → 称呼”修改名字，重新登录后仍会保留；
- 点击登录页的“手机号”仍可测试原有验证码登录。

该能力仅用于开发测试。后端在 `APP_ENV=production` 时会拒绝启动任何开启测试账号的配置。

## 5. 每天如何在模拟器启动 SlimGuard

### 5.1 推荐顺序

1. 通过 Spotlight 或 **Xcode → Open Developer Tool → Simulator** 打开 Simulator。
2. 在 Simulator 的 **File → Open Simulator** 中选择 iPhone 17 Pro。
3. 确认要使用的后端已启动，并检查 `.env.local` 的 API 地址。
4. 打开终端，执行：

```bash
cd /Users/pinjhu/work/personal/my-projects/slim-guard/mobile-app
npm run ios
```

5. 保持这个终端窗口运行。
6. 等待终端显示 `iOS Bundled`，模拟器会自动进入 SlimGuard 登录页。

`npm run ios` 实际执行的是 `expo start --ios`：它启动 Metro、寻找已经打开的 iPhone
Simulator，并让 Expo Go 加载当前项目。

### 5.2 常用调试操作

Metro 运行时，可以在它所在的终端中使用：

- 按 `r`：重新加载 App；
- 按 `i`：再次尝试打开 iOS Simulator；
- 按 `j`：打开 JavaScript 调试器；
- 按 `Ctrl + C`：停止 Metro。

在 iOS Simulator 中按 `Command + D` 可以打开开发菜单。

代码保存后通常会自动刷新。如果状态异常，先按 `r`。缓存异常时停止 Metro，再运行：

```bash
npx expo start --clear
```

### 5.3 如何判断启动成功

至少满足以下条件：

- Simulator 中能看到 SlimGuard 登录页；
- 终端出现 `iOS Bundled`，没有红色编译错误；
- 登录页底部开发信息显示的 API 地址与 `.env.local` 一致；
- 获取验证码后能继续登录；
- 登录后“今天 / 教练 / 趋势 / 我的”四个页面可以打开。

## 6. 修改代码后的检查流程

在提交代码前运行：

```bash
cd /Users/pinjhu/work/personal/my-projects/slim-guard/mobile-app
npm run typecheck
npm run bundle:ios
npm run bundle:android
```

这些命令的含义：

- `typecheck`：检查 TypeScript 类型；
- `bundle:ios`：验证 iOS JavaScript 和资源能否导出；
- `bundle:android`：验证 Android JavaScript 和资源能否导出。

`bundle:ios` 和 `bundle:android` 生成的是静态 Bundle 校验产物，不是可安装的 `.ipa`、`.app`、
`.apk` 或 `.aab`。真正的安装包需要下一节的原生构建。

也建议运行 Expo 项目检查：

```bash
npx expo-doctor
```

如果 Expo 提示包版本与 SDK 57 不匹配，先查看差异，再执行：

```bash
npx expo install --fix
```

此命令可能修改 `package.json` 和 `package-lock.json`，执行后应重新跑类型检查和双端 Bundle。

## 7. 安装并配置 EAS Build

EAS Build 在 Expo 云端编译原生 iOS/Android 包，日常 Expo Go 调试不需要它。

### 7.1 注册和登录

1. 在 [expo.dev](https://expo.dev/) 注册 Expo 账号。
2. 在终端进入移动端目录：

```bash
cd /Users/pinjhu/work/personal/my-projects/slim-guard/mobile-app
npx eas-cli@latest login
npx eas-cli@latest whoami
```

本项目推荐一直使用 `npx eas-cli@latest`，无需全局安装 EAS CLI。

### 7.2 首次把项目关联到 EAS

```bash
npx eas-cli@latest init
```

首次运行会让你选择或创建 Expo Project，并把 Project ID 写进 App 配置。执行后检查 Git 差异并
提交该配置；不要反复创建不同的 Expo Project。

当前仓库已经有 `mobile-app/eas.json`，包含：

- `development`：开发客户端；
- `preview`：内部测试包；
- `production`：正式商店包，自动递增版本号。

### 7.3 配置构建环境中的 API 地址

本地 `.env.local` 不会自动成为 EAS 云环境变量。分别配置：

```bash
npx eas-cli@latest env:set --name EXPO_PUBLIC_API_BASE_URL \
  --value https://你的-api-域名 --environment development --visibility plaintext

npx eas-cli@latest env:set --name EXPO_PUBLIC_API_BASE_URL \
  --value https://你的-api-域名 --environment preview --visibility plaintext

npx eas-cli@latest env:set --name EXPO_PUBLIC_API_BASE_URL \
  --value https://你的-api-域名 --environment production --visibility plaintext
```

API Origin 是客户端公开信息，所以用 `plaintext`。后端密钥不要上传到移动端 EAS 环境。

## 8. 编译可独立安装的 iOS Simulator 包

Expo Go 对推送通知和部分原生能力支持不完整。需要更接近真实 App 的模拟器测试时，生成一个
Development Build。

### 8.1 一次性安装 Development Client

```bash
cd /Users/pinjhu/work/personal/my-projects/slim-guard/mobile-app
npx expo install expo-dev-client
```

这会修改依赖文件，应连同 `package-lock.json` 一起提交。

### 8.2 给 `eas.json` 增加模拟器 Profile

在 `build` 下增加：

```json
"ios-simulator": {
  "extends": "development",
  "ios": {
    "simulator": true
  }
}
```

这里的 `ios.simulator: true` 很重要。普通 iPhone 真机包不能安装进 Simulator，Simulator 包也不能
安装到真实 iPhone。

### 8.3 云端编译并安装

```bash
npx eas-cli@latest build --platform ios --profile ios-simulator
```

构建结束后，CLI 询问是否安装到 Simulator 时选择 `Y`。也可以以后安装最近一次模拟器构建：

```bash
npx eas-cli@latest build:run --platform ios --latest
```

Development Build 安装后仍需要 Metro 提供开发 JavaScript：

```bash
npx expo start
```

iOS Simulator Build 不需要 TestFlight，也不需要付费 Apple Developer Program。

## 9. 编译 Preview 测试包

Preview Build 用于功能相对稳定后安装到真实手机验收。

同时构建双端：

```bash
cd /Users/pinjhu/work/personal/my-projects/slim-guard/mobile-app
npx eas-cli@latest build --platform all --profile preview
```

也可以分开构建：

```bash
npx eas-cli@latest build --platform ios --profile preview
npx eas-cli@latest build --platform android --profile preview
```

注意：

- iOS 真机内部测试包通常需要 Apple Developer Program、签名证书和已登记测试设备；
- Android `distribution: internal` 通常生成可直接安装的 APK；
- EAS CLI 可以在首次构建时引导生成和托管签名凭据；
- EAS 构建页面可以查看队列、实时日志和下载链接。

查看历史构建：

```bash
npx eas-cli@latest build:list
```

## 10. 编译 Production 正式包

上线前先确认：

- `app.json` 中的 Bundle Identifier/Package 不再修改；
- Production EAS 环境指向正式 HTTPS API；
- 服务器关闭开发验证码并接通真实短信；
- 隐私政策、服务条款、客服信息和商店素材已经准备；
- 真机完成登录、聊天、图片、趋势、记忆、通知、离线重试和删除账号验收。

构建双端正式包：

```bash
cd /Users/pinjhu/work/personal/my-projects/slim-guard/mobile-app
npx eas-cli@latest build --platform all --profile production
```

也可以单独构建：

```bash
npx eas-cli@latest build --platform ios --profile production
npx eas-cli@latest build --platform android --profile production
```

结果通常是：

- iOS：可提交 App Store Connect/TestFlight 的签名构建；
- Android：默认生成适合 Google Play 的 AAB。

提交到商店：

```bash
npx eas-cli@latest submit --platform ios --profile production
npx eas-cli@latest submit --platform android --profile production
```

iOS 正式构建和提交需要 Apple Developer Program；Google Play 发布需要 Google Play Console
开发者账号。EAS 可以托管签名凭据，但开发者账号、商店协议和合规资料仍需自己完成。

## 11. 使用 Xcode 在 Mac 本地原生编译（高级/备用）

默认推荐 EAS Build，因为它会准备匹配 Expo SDK 的构建环境并处理大部分原生依赖。只有在需要
排查原生编译问题、修改原生能力，或 EAS 网络长期不可用时，再采用本节。

### 11.1 生成 iOS 原生工程

先确认工作区没有遗漏的重要改动，然后执行：

```bash
cd /Users/pinjhu/work/personal/my-projects/slim-guard/mobile-app
npx expo prebuild --platform ios
```

这会根据 `app.json` 和 Expo Config Plugins 生成 `mobile-app/ios/`。当前仓库将生成的 `ios/` 和
`android/` 目录忽略，不把它们当作配置源；以后修改权限、图标和插件时仍应优先修改 `app.json`。

### 11.2 通过 Xcode 图形界面编译到 Simulator

1. 在 Finder 中打开 `mobile-app/ios/`。
2. 双击其中的 `.xcworkspace` 文件。使用了 CocoaPods 的项目应打开 `.xcworkspace`，不要打开
   `.xcodeproj`。
3. 等待 Xcode 完成 Package Resolution 和 Indexing。
4. 在 Xcode 顶部选择 SlimGuard Scheme。
5. 在运行目标中选择一个 iPhone Simulator，例如 iPhone 17 Pro。
6. 选择菜单 **Product → Build**，只编译项目。
7. 选择菜单 **Product → Run**，编译并安装到当前模拟器。
8. 如果出现红色错误，在左侧 Report Navigator 打开最新一次 Build 查看第一条真实编译错误。

也可以用一条命令生成原生工程、编译并打开模拟器：

```bash
npx expo run:ios
```

这条命令不是日常 Expo Go 启动命令；它会走完整原生编译，因此明显更慢。

### 11.3 Archive 和真机签名

要通过 Xcode 生成可分发 Archive：

1. 在 Xcode 登录 Apple ID：**Xcode → Settings → Accounts**。
2. 在项目的 **Signing & Capabilities** 选择正确 Team。
3. 把运行目标切换为通用 iOS Device，而不是 Simulator。
4. 选择 **Product → Archive**。
5. Archive 完成后会打开 Organizer，可继续验证或上传到 App Store Connect。

真机 Archive、TestFlight 和 App Store 分发需要有效的 Apple Developer Program 与正确签名。
如果只是模拟器调试，不需要付费账号，也不要选择 Archive 路线。

### 11.4 本地原生工程异常时

因为 `ios/` 是可再生成目录，优先检查 `app.json`、Expo 插件和依赖版本。不要手工在生成文件中做
无法回溯的关键配置。需要重新生成时，先确认其中没有要保留的手工原生修改，再按 Expo Prebuild
文档处理；不要在不清楚影响范围时直接删除整个目录。

## 12. Android 模拟器和构建

即使主要使用 iPhone，也建议发布前至少测试一台 Android 模拟器和一台 Android 真机。

### 12.1 通过 Android Studio 图形界面启动模拟器

1. 安装并打开 Android Studio。
2. 在欢迎页或项目窗口打开 **Device Manager**。
3. 点击 **Create Virtual Device**。
4. 选择一个常见 Pixel 设备。
5. 下载并选择一个兼容的 Android System Image。
6. 创建完成后，点击设备右侧的播放按钮。

模拟器启动后，在终端执行：

```bash
cd /Users/pinjhu/work/personal/my-projects/slim-guard/mobile-app
npm run android
```

### 12.2 Android 安装包

- 内部测试 APK：`npx eas-cli@latest build --platform android --profile preview`
- Google Play AAB：`npx eas-cli@latest build --platform android --profile production`

## 13. Expo Go 的能力边界

Expo Go 适合快速开发，但不是最终 App：

- 页面、登录、普通网络请求、聊天、趋势和大部分本地交互可以测试；
- 当前项目使用 `expo-notifications`，Expo Go 会提示通知能力不完整，这是预期警告；
- 远程推送、最终权限文案、签名、Universal Link、真实相机体验和真机性能要使用 Development/
  Preview Build 或真实手机测试；
- 模拟器不能真实反映电池、蜂窝网络、相机画质、推送到达率和后台进程限制。

因此推荐顺序是：Expo Go 日常开发 → Simulator Development Build → 双端真机 Preview →
Production/TestFlight/Google Play。

## 14. 常见问题

### 14.1 Simulator 打开后没有设备

在 **Xcode → Settings → Components** 下载 iOS Runtime，再通过
**Window → Devices and Simulators** 创建 iPhone。

### 14.2 `npm run ios` 一直停在 Loading project

检查：

1. Metro 所在终端是否仍在运行；
2. 终端是否正在首次下载 Expo Go；
3. 是否最终出现 `iOS Bundled`；
4. 停止后执行 `npx expo start --clear`；
5. 在 Simulator 中退出 Expo Go，再运行 `npm run ios`。

### 14.3 登录页能打开，但获取验证码失败

先看登录页底部显示的 API 地址，然后检查对应 `/health/live`：

```bash
curl -i API地址/health/live
```

如果使用本地 Docker，API 应为 `http://127.0.0.1:18083`，不是截图中未配置时的默认
`http://127.0.0.1:8000`。

### 14.4 修改 `.env.local` 后地址没变化

停止 Metro，再重新运行 `npm run ios`。仍未变化时：

```bash
npx expo start --clear
```

### 14.5 Expo Go 显示通知警告

这是 Expo Go 的功能限制，不影响普通页面和聊天。要验证完整通知行为，使用第 8 节的
Development Build，并最终在真实设备上验收。

### 14.6 EAS 构建很慢或下载不稳定

EAS 和 Expo Go 需要访问 Expo 服务。在网络不稳定时：

- 保留终端运行并观察是否仍有进度；
- 从 Expo Build Dashboard 查看云端构建是否仍在执行；
- 不要因为短时间无输出就重复创建多个构建；
- 失败后使用构建详情页的明确错误定位，不要只看最后一行。

### 14.7 端口被占用

- Metro 默认使用 `8081`；
- Docker 后端默认暴露 `127.0.0.1:18083`；
- PostgreSQL 默认暴露 `127.0.0.1:15432`。

先关闭旧的 Metro 或冲突容器，再重试。不要随意改端口而忘记同步 `.env.local`。

## 15. 一次完整自测清单

首次把环境跑通时，建议逐项勾选：

- [ ] Xcode 可以正常启动；
- [ ] Xcode Components 中已经安装 iOS Simulator Runtime；
- [ ] Simulator 中可以打开 iPhone 17 Pro；
- [ ] `node --version` 不低于 22.13；
- [ ] `npm ci` 成功；
- [ ] `.env.local` 指向正确的后端 Origin；
- [ ] 后端 `/health/live` 或 `/health/ready` 返回 200；
- [ ] `npm run ios` 最终显示 `iOS Bundled`；
- [ ] SlimGuard 登录页显示正确 API 地址；
- [ ] 可以获取验证码并登录；
- [ ] 可以发送文字消息并收到 Agent 回复；
- [ ] 体重、体脂、目标和记忆能在对应页面显示；
- [ ] `npm run typecheck` 通过；
- [ ] `npm run bundle:ios` 通过；
- [ ] `npm run bundle:android` 通过；
- [ ] 真机测试前已经生成 Preview Build；
- [ ] 上线前双端 Production Build 和商店资料均已准备。

## 16. 官方参考

- [Expo SDK 57 文档](https://docs.expo.dev/versions/v57.0.0/)
- [Expo：创建第一个 EAS Build](https://docs.expo.dev/build/setup/)
- [Expo：为 iOS Simulator 构建](https://docs.expo.dev/build-reference/simulators/)
- [Expo：iOS Simulator Development Build 教程](https://docs.expo.dev/tutorial/eas/ios-development-build-for-simulators/)
- [Expo CLI：本地编译与静态导出](https://docs.expo.dev/more/expo-cli/)
- [Expo：开发流程与 Prebuild](https://docs.expo.dev/workflow/overview/)
- [Apple：在模拟设备或真实设备上运行 App](https://developer.apple.com/documentation/xcode/running-your-app-on-simulated-or-physical-devices)
