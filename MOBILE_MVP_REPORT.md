# SlimGuard Mobile MVP 交付报告

交付日期：2026-09-03

## 产品结果

MVP 已从“微信客服中的减脂助手”扩展为复用同一后端的 iOS/Android App。核心原则没有变化：
模型负责理解，数据库记录是权威事实，客户端不靠关键词规则猜测用户语义。

四个主入口覆盖首版闭环：

- **今天**：最近体重/体脂、目标、当天饮食与运动次数、快捷记录；
- **教练**：自然语言和饮食图片对话，直接进入现有 Harness Agent；
- **趋势**：体重与体脂曲线、四类记录计数和最近活动；
- **我的**：昵称、白话记忆、提醒、微信绑定、退出和永久删除。

## 架构

```text
iOS / Android (Expo + React Native)
  ├─ SecureStore: Refresh Token、安装 ID
  ├─ AsyncStorage + App Documents: 离线文字/图片队列
  ├─ Local Notifications: 称重、饮食、复盘
  └─ HTTPS JSON API
          ↓
FastAPI /api/mobile/v1
  ├─ OTP + Access Token + rotating Refresh Session
  ├─ user-scoped API + idempotency ledger
  ├─ existing Harness Agent / tools / traces
  ├─ relational records + authoritative memory
  ├─ optional Mem0 semantic recall
  └─ device registry + push provider boundary
          ↕
WeCom one-time identity binding
```

移动对话没有另造一套 Agent。它创建标准 Thread/Turn/Item/Trace，调用同一个 Harness Runtime，
因此微信里已经实现的体重、体脂、饮食、运动、记忆摄取/召回和 handoff 都能复用。每个移动请求
带客户端生成的幂等键；断网重试、超时重试和重复点击会拿到同一次结果。

## 安全和隐私

- Access Token 默认 15 分钟；Refresh Token 只在系统 SecureStore 保存并在服务端轮换；
- 手机号只保存带密钥哈希与尾号提示，不保存可直接读取的完整号码；
- 微信绑定码只保存 HMAC，单次使用且自动过期；微信外部用户 ID 只存短哈希引用；
- App/微信两边都有历史时拒绝自动覆盖；
- 图片在发送前转成压缩 JPEG，离线文件位于 App 私有目录，发送成功即删除；
- 永久删除会删除认证、设备、对话、Trace、记录、记忆、日程和外部语义记忆；
- 健康能力定位为生活方式管理，不输出医疗诊断承诺。

## 已验证

- Python Ruff 全仓检查；
- Python mypy 全部 131 个源文件；
- Python 全量测试套件；
- 移动端 TypeScript `tsc --noEmit`；
- iOS Expo production export；
- Android Expo production export。

真实短信、APNs/FCM/Expo 远程投递、Apple/Google 签名和商店审核依赖外部账号，需按
[`MOBILE_APP_DEPLOYMENT.md`](MOBILE_APP_DEPLOYMENT.md) 在部署环境完成最终真机验收。
