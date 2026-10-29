# 称小二 Mobile

称小二（原 SlimGuard Mobile）是同一套后端的 iOS/Android 客户端，使用 Expo SDK 58 和 React Native。
业务语义仍由服务端 Harness Agent 与 model-first Memory 负责；客户端只负责交互、鉴权、
安全保存刷新令牌、图片预处理和离线可靠投递。

## 功能

- 手机号验证码登录，短期 Access Token + 可轮换 Refresh Token；
- 开发环境可启用 5 个隔离的账号密码测试用户，正式环境强制关闭；
- “今天 / 教练 / 趋势 / 我的”四个原生页面；
- 教练使用前的健康档案硬门槛：必填年龄段、身高、当前体重及日期、目标类型、目标体重和日期，
  可选当前/目标体脂与运动频率；选项优先使用点选，数值最多手动输入一位小数；
- 未成年人完成档案后同样可以使用教练，Agent 会采用儿童青少年适用的判断标准；
- 自然语言和相机/相册饮食图片对话；
- 体重、体脂、饮食、运动记录与趋势；
- 饮食记录展示关联照片的缩略图，点击打开可滑动的大图；每条记录可直接修改菜名、增删菜品和调整餐次；
- 每条餐食使用 44×44 的铅笔图标按钮进入底部编辑抽屉；取消和保存固定在底部，未保存关闭需确认；
- 大图使用底部关闭按钮，原始缩放下可下滑退出；iOS 双指放大后拖动不会误关闭或切图；
- 趋势页可切换近 7 天／近 30 天，图表与记录统计使用同一自然日范围，不再展示重复的“最近活动”；
- 教练对话只展示用户和教练；系统操作反馈在输入框上方显示，普通通知 10 秒后消失，需要回答的提示持续保留；
- 目标和长期记忆的白话展示；
- 本地称重、饮食、晚间复盘提醒；
- 离线文字/图片队列，联网后用幂等键自动补发；
- 一次性绑定码连接已有微信客服身份；
- 双重确认的账号及服务端数据删除。

## 连接状态与消息投递

设备断网、后端暂时不可达和消息仍在处理是三种独立状态。NetInfo 只提供设备连接信息，
不再使用默认 Google 探测结果阻止发送；真实 API 响应和自有 `/health/live` 辅助探测负责
更新服务连接状态，旧探测结果不能覆盖更新的 API 响应。只有设备明确断网才显示“离线模式”。

消息在首次请求前持久化，超时后沿用原幂等键核对结果或重试，不会当作新消息重新创建。
队列会在回到前台、设备恢复连接及退避定时器触发时继续投递，不依赖重启 App；图片、原始
发送时间和账号归属随队列保留。`正在处理` 不等于未提交，也不等于手机离线。

## 品牌与启动画面

展示名为「称小二」，以 `app.json` 的 `expo.name` 为准；页头、登录页和加载页共用
`src/shared/brand/` 下的品牌组件。正式图标使用用户选定的杏橙色小秤原图，保存在
`assets/brand/cheng-xiao-er-icon.png`；透明前景用于 Android 自适应图标和原生启动屏。
素材来源与透明前景的生成提示词保存在 `assets/brand/provenance.json`。

原生启动屏在首个 React 界面完成布局后隐藏，不额外等待接口，也不设置最短展示时间。
App 内加载页使用同一小秤、杏橙背景与轻微呼吸动效，遵循系统“减少动态效果”设置。
业务页面的主题和数据流程不变。

Expo Go 内可以查看新页头和 App 内加载页，原生桌面图标、权限文案和启动屏需要重新构建的
preview / production 安装包验收；Fast Refresh 不会修改手机上的 Expo Go 图标。
`slug`、`scheme`、iOS bundle ID、Android package、登录凭证与离线队列存储键保持原值，
本次显示名变更不迁移或清空用户数据。

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
EXPO_PUBLIC_API_BASE_URL=https://enceladus.online
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

健康档案也可以在“我的 → 健康档案”查看和修改。App 只保存完整档案，不保存半成品；年龄段为
`0～9`、`10～17`、`18～29`，之后按十岁分段至 `80 岁及以上`。档案不会询问过敏、疾病、
医嘱或孕哺情况，因此 Agent 不得把这些字段缺失理解为用户没有相关情况。

## 静态校验

React 与 React DOM 显式固定为 Expo SDK 58 使用的 `19.3.0`。即使只运行原生 App，
`@expo/ui` 的依赖树也会涉及 React DOM；不要单独升级其中一个包，也不要用 `--force` 或
`--legacy-peer-deps` 绕过版本冲突。修改依赖后应验证一次标准 `npm ci`，确认锁文件可以干净安装。

```bash
npm test
npm run typecheck
npm run bundle:ios
npm run bundle:android
```

`npm test` 覆盖日期字段、餐食编辑、大图打开关闭、系统提示去重与过期等 JS 交互，使用原生视图替身；它不替代 iOS/Android
真机布局验收。iOS 日期选择在独立弹窗中完成，滚动后点“确定”才回填，“取消”不修改原值。

## 趋势统计

趋势统计使用 `GET /api/mobile/v1/progress/period?days=7`（或 `30`），以用户 routine 时区为准，包含今天。
后端直接统计日期范围内所有有效记录，不受历史分页条数影响；日期未知、已作废或被替代的记录不计入。
同一天的多条测量都计数，曲线仅保留一个点：优先选择测量时间最晚的值；只有日期时按录入先后选择。
未记录的日期不补零，日期型数据不伪造具体时分。此功能需要部署新的后端接口，无数据库迁移或配置变更。

## 餐食编辑与提示条契约

餐食记录的 `content.photo_asset_ids` 保存图片引用，独立于当前字段证据；更正菜名不会丢失照片。
记录历史只显示日期、餐次、菜品、份量和照片，不展示识别疑虑或邀请更正的对话文案。
`PATCH /api/mobile/v1/records/meal/{record_id}` 接收操作 UUID、预期记录版本、餐次与菜品列表，
直接复用记录修改逻辑，不调用模型。既有菜品须带 `item_id`，新增菜品不带 ID；既有份量与照片保留。
相同操作可安全重试，版本冲突返回 409。迁移 `20261028_01_meal_media_and_editor` 新增照片字段与
手动编辑来源表，并为证据明确的旧餐食回填照片；多餐来源不明确时保留为空。

Conversation Agent 根据交流语义输出系统消息的 `notice_text` 和 `requires_response`，不使用关键词判断
是否需要回答。完整系统文案仍保存供 Trace 和上下文使用。手机端按 Turn 合并系统文字和操作卡，
防止重复通知；历史刷新不会重放已关闭的提示。规划失败时保留可手动关闭的提示，避免重要问题自动丢失。

完整部署和 EAS 构建步骤见仓库根目录的
[`MOBILE_APP_DEPLOYMENT.md`](../MOBILE_APP_DEPLOYMENT.md)。

第一次在 Mac 上安装 Expo、通过 Xcode 图形界面启动 iPhone Simulator、调试并构建双端包，见
[`MOBILE_APP_MAC_GUIDE.md`](../MOBILE_APP_MAC_GUIDE.md)。
