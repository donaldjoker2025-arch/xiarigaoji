# 夏日告急 — 北航空调电量监控系统 —— UTF-8

## 🎉 v2.0 更新亮点
- **极速网页版**：新增纯前端静态页面版 (`pages/index.html`)，可直接托管至 GitHub/Gitee Pages，无需本地客户端即可查电量、跳充值。
- **安装包极大瘦身**：深度优化 PyInstaller 打包策略，剔除无用的 Windows SDK 等超大依赖库，软件体积断崖式减小。
- **内置自动更新提醒**：新增后台版本轮询，在 GitHub 发布新版时，系统通过桌面弹窗、Web Push 或 Server酱全方位主动提醒用户升级。

> 本文档面向 **开发者 / AI 助手**，用于快速理解项目架构、每个文件的职责、模块间的调用关系，以及常见 Debug 入口。
> 读完本文，你应当能在不逐行通读代码的情况下，定位任意一个 Bug 该去哪个文件、哪个函数排查。

---

## 1. 项目是什么

一个 **本地化运行** 的北航校园电量监控小工具。用户在网页里选择自己的宿舍/教研室电表，程序定时去学校的电量查询接口拉取剩余电量，当电量低于阈值时通过 **桌面 Toast / 浏览器 Web Push / 微信（Server酱）** 三个渠道告警，并支持直接跳转学校官方页面充值。

核心设计取向：
- **纯本地、纯 HTTP**：服务跑在 `localhost`，不依赖任何云端。利用 W3C "Secure Contexts" 规范——`http://localhost` / `http://127.0.0.1` 被现代浏览器视为安全上下文，因此 Service Worker / Web Push / 通知 API 全部可用，**无需任何 HTTPS 证书**，从根本上规避自签名证书的 SSL 报错。
- **不碰支付**：充值只生成学校官方支付链接并跳转，本程序从不接触支付密码。
- **隐私**：所有数据仅存本机 SQLite。

---

## 2. 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python + Flask 3.1 |
| 定时任务 | APScheduler 3.11（BackgroundScheduler + IntervalTrigger） |
| 存储 | SQLite（WAL 模式） |
| 推送 | win11toast（桌面）、pywebpush + py-vapid（浏览器 Web Push）、Server酱（微信） |
| 系统集成 | pystray（系统托盘）、qrcode + Pillow（手机扫码二维码） |
| 前端 | 原生 HTML/CSS/JS（无框架）+ Chart.js（趋势图，CDN 引入） |
| PWA | manifest.json + sw.js（Service Worker，处理推送展示） |

依赖见 [requirements.txt](requirements.txt)。

---

## 3. 目录结构

```
夏日告急/
├── app.py              # ★ 主入口：Flask 应用 + 所有 HTTP 路由 + 系统托盘 + 启动流程
├── config.py           # ★ 全局配置常量（端口、API 地址、电表系统列表、等级参数、许可证配置）
├── database.py         # ★ SQLite 封装：电表/读数/订阅/通知/设置/许可证 的全部 CRUD
├── buaa_api.py         # ★ 学校接口封装：查电表列表、查实时电量、创建缴费订单
├── license_manager.py  # ★ 授权：机器码 + Ed25519 验签 + Gitee 在线吊销（内置公钥）
├── scheduler.py        # ★ 定时轮询：周期拉取所有电表读数 + 触发告警
├── notifier.py         # ★ 三渠道通知：桌面 Toast / Web Push / 微信 Server酱
├── cert_manager.py     # VAPID 密钥生成与读取 + 获取本机局域网 IP
├── requirements.txt    # Python 依赖
├── start.bat           # Windows 启动脚本（源码运行）
├── build.bat           # 一键打包成 exe（PyInstaller）
├── xrtj.spec           # PyInstaller 打包配置（单文件/窗口化/安全排除）
├── 使用说明.txt         # 给最终用户的说明（随 exe 一起分发，UTF-8 BOM）
├── 后台静默运行.vbs     # 无窗口后台启动脚本
│
├── tools/              # 版权方工具（不随 App 分发）
│   ├── gen_keys.py      # 一次性：生成 Ed25519 签名密钥对
│   ├── gen_license.py   # 用私钥签发绑定机器的许可证
│   └── make_icon.py     # 生成应用图标 static/icon.ico
│
├── secrets/            # 签名私钥与签发台账（git 已忽略，绝不分发/打包）
│   ├── license_private_key.pem   # 签发私钥（命门，只在版权方本机）
│   └── issued_licenses.csv       # 签发记录（jti/机器码/到期/备注）
│
├── data/               # 运行时数据（git 已忽略）
│   ├── buaa_power.db    # SQLite 数据库
│   ├── vapid_keys.json  # 自动生成的 VAPID 密钥对
│   └── revocation_cache.json  # 吊销名单本地缓存
│
└── static/             # 前端（Flask 以 /static 提供，根路径回退到 index.html）
    ├── index.html      # 单页应用骨架（三个 Tab：仪表盘 / 电表配置 / 设置）
    ├── icon.ico        # 应用/托盘图标（exe 图标也用它）
    ├── manifest.json   # PWA 清单
    ├── sw.js           # Service Worker（接收 push 事件并展示通知）
    ├── css/style.css   # 全部样式
    └── js/app.js       # ★ 前端全部逻辑（IIFE，无模块化）
```

★ = 改 Bug 时最常打开的文件。

---

## 4. 模块职责与彼此联系

### 4.1 调用关系总览

```
              ┌──────────────┐
   浏览器  ──▶ │   app.py     │  (Flask 路由层，所有 HTTP 入口)
              └──────┬───────┘
                     │ 调用
     ┌───────────────┼───────────────┬───────────────┐
     ▼               ▼               ▼               ▼
┌─────────┐   ┌────────────┐   ┌───────────┐   ┌──────────────┐
│database │   │ buaa_api   │   │ scheduler │   │ notifier     │
│(SQLite) │   │(学校HTTP)  │   │(定时轮询) │   │(三渠道通知)  │
└─────────┘   └────────────┘   └─────┬─────┘   └──────────────┘
                                     │ 周期调用 buaa_api + database + notifier
                                     ▼
                              poll_all_meters()

config.py    ← 所有模块都 import config 读常量
cert_manager ← app.py / scheduler.py 用它拿 VAPID 密钥和本机 IP
```

关键点：**`scheduler.poll_all_meters()` 是业务核心循环**——它把 buaa_api（取数）、database（存数 + 防重复）、notifier（告警）三者串起来。读懂这个函数就读懂了整个后端的数据流。

### 4.2 各文件详解

#### `config.py` — 配置中心
所有可调常量集中于此。改 Bug 时**先看这里确认参数**。重点字段：
- `SERVER_HOST='0.0.0.0'` / `SERVER_PORT=5000`：监听地址（0.0.0.0 允许局域网手机访问）。
- `METER_SYSTEMS`：**多电表系统列表**，每项 `{key, name, base_url}`。学校把不同校区/用途的电表拆成多个独立子系统，各自有独立 API host，但接口路径一致。前端「电表系统」下拉就是读这个列表。
- `DEFAULT_SYSTEM_KEY='xyl_ac'`：历史数据/未标注系统的电表默认归属，保证向后兼容。
- `DEFAULT_THRESHOLD=5.0`：默认告警阈值（kWh）。
- 免费/赞助等级参数：`FREE_*`、`SPONSOR_*`（轮询间隔范围、最大电表数）。
- `LICENSE_SETTING_KEY` / `REVOCATION_LIST_URL` / `REVOCATION_TTL_HOURS`：许可证存储键、Gitee 吊销名单地址、吊销缓存时长。**旧的对称 `SPONSOR_SECRET` 已删除**，授权改用 Ed25519 非对称签名（见 `license_manager.py`）。

> **当前 `METER_SYSTEMS` 配置（与查询/购电站点的对应关系）：**
> | key | 显示名 | base_url | 查询(PubBuaa) | 购电(BuaaPay) |
> |-----|--------|----------|---------------|----------------|
> | `xyl_ac` | 学院路空调表 | `https://xylktsd.buaa.edu.cn` | `xylktsd.buaa.edu.cn/PubBuaa` | `xylktsd.buaa.edu.cn/BuaaPay` |
> | `shahe` | 学院路照明表及沙河照明空调表 | `http://shsd.buaa.edu.cn` | `shsd.buaa.edu.cn/PubBuaa` | `shsd.buaa.edu.cn/BuaaPay` |
>
> 即 `base_url` 同时承载查询与购电——查询拼 `/PubBuaa/...`，购电拼 `/BuaaPay/...`，见 `buaa_api.py`。
>
> ⚠️ **两系统 scheme 不同**：`xylktsd` 用 `https://`，但 `shsd`（shahe）的 443 端口无有效 TLS，**只能用 `http://`**，写错会握手失败。
> ⚠️ **校内地址是内网（10.x.x.x），必须绕过本机代理**——见下方 `buaa_api.py` 的 `_SESSION`。

#### `app.py` — Flask 应用 / 路由层 / 启动入口
唯一的 HTTP 入口，也是程序 `main()`。两部分：

**A. 启动流程 `main()`**（文件末尾）：
1. 创建 `data/`、`static/` 目录
2. `database.init_db()` 建表
3. `cert_manager.ensure_vapid_keys()` 生成/读取 VAPID 密钥
4. `scheduler.start_scheduler(app)` 启动定时轮询
5. 启动系统托盘图标（后台线程，`_create_tray_icon`）
6. 延迟 1.5s 自动打开浏览器
7. `app.run()` 启 HTTP 服务（`use_reloader=False`，避免双重启动调度器）

**B. API 路由**（全部返回 `{success, data/error}` 结构）：

| 路由 | 方法 | 作用 | 主要下游 |
|------|------|------|----------|
| `/` `/<path>` | GET | 静态文件 / SPA 回退到 index.html | — |
| `/sw.js` | GET | 提供 Service Worker（须在根作用域） | — |
| `/api/meter-systems` | GET | **返回电表系统列表**（前端「电表系统」下拉） | `config.METER_SYSTEMS` |
| `/api/meter-options` | GET | 级联选项（校区>楼>层>房>表）；`?system=` 指定系统 | `buaa_api.fetch_meter_options` |
| `/api/meter-info/<identity_no>` | GET | 单表实时信息；`?system=` 或按库内系统 | `buaa_api.fetch_meter_info` |
| `/api/meters` | GET/POST | 列出（附最新读数）/ 新增电表 | `database` |
| `/api/meters/<id>` | DELETE | 删除电表及历史 | `database.delete_meter` |
| `/api/meters/<id>/threshold` | PUT | 改阈值 | `database.update_threshold` |
| `/api/readings/<id>` | GET | 历史读数（`?limit=`） | `database.get_readings` |
| `/api/charge/<identity_no>` | POST | 创建充值订单，返回支付 URL | `buaa_api` + `database` 路由系统 |
| `/api/poll-now` | POST | 手动触发一次全量轮询（后台线程） | `scheduler.poll_all_meters` |
| `/api/tier` `/api/settings` | GET/PUT | 用户等级 / 轮询间隔（tier 含 `expires_at` 授权到期） | `database` + `scheduler` |
| `/api/machine-id` | GET | 返回本机机器码（前端展示给用户，用于签发绑定许可证） | `license_manager.get_machine_id` |
| `/api/activate` | POST | 激活许可证（body `code`=许可证字符串） | `database.activate_sponsor` |
| `/api/push/subscribe` | POST/DELETE | 保存/移除 Web Push 订阅 | `database` |
| `/api/vapid-public-key` | GET | 返回 VAPID 公钥（前端订阅用） | `cert_manager` |
| `/api/serverchan` | GET/PUT | 微信 SendKey 状态/保存（脱敏） | `database` |
| `/api/serverchan/test` | POST | 发测试微信推送 | `notifier.ServerChanNotifier` |
| `/api/qrcode` | GET | 生成手机访问二维码 PNG | `cert_manager.get_local_ip` |

CORS：`@app.after_request` 给所有响应加 `Access-Control-Allow-*`，允许手机跨域访问。

#### `database.py` — SQLite 数据访问层
每个函数自开自关连接（`_get_connection()`，WAL 模式 + 外键）。**5 张表**：

| 表 | 作用 | 关键列 |
|----|------|--------|
| `settings` | 键值对设置 | `poll_interval`、`serverchan_key`、`sponsor_code`、`code_seed_*` |
| `meters` | 电表配置 | `identity_no`(UNIQUE)、`threshold`、`system_key`、校区/楼/层/房 |
| `readings` | 历史读数 | `meter_id`(FK)、`remain`、`price`、`reading_time`、`pay_status` |
| `push_subscriptions` | Web Push 订阅 | `subscription_json`(UNIQUE) |
| `notifications` | 通知去重记录 | `meter_id`、`level`、`reset_at` |

注意点：
- `init_db()` 幂等（`IF NOT EXISTS`），并含一段**迁移逻辑**：给早期 `meters` 表补 `system_key` 列。
- **防重复通知机制**（重点）：`should_notify(meter_id, level)` 检查是否存在 `reset_at IS NULL` 的同级别记录——有则不再通知；`mark_notified` 写记录；`reset_notification` 在电量回升后把 `reset_at` 置时间戳，从而允许下次再次告警。
- **许可证 / 授权**：`activate_sponsor(license_str)` 调 `license_manager.verify_license` 做（签名 + 机器绑定 + 到期 + 吊销）四重校验，通过才把许可证串存进 settings(`license_key`)；`is_sponsor()` / `get_license_info()` 每次都重新校验。**不再有任何随包分发的密钥**——见下方 `license_manager.py`。

#### `buaa_api.py` — 学校接口封装
封装三个学校 HTTP 接口，**通过 `resolve_base_url(system_key)` 把请求路由到正确的系统 host**：

| 函数 | 学校接口 | 说明 |
|------|----------|------|
| `fetch_meter_options(system_key)` | `GET {base}/PubBuaa/QueryIdData` | 拉全部电表，转成 `校区>楼>层>房>[表]` 嵌套字典（OrderedDict） |
| `fetch_meter_info(identity_no, system_key)` | `GET {base}/BuaaPay/Meter?id=` | 查单表实时数据，**带 3 次重试**，字段标准化（`remain`/`price`/`payStatus`/`payUrl`/`id` 等） |
| `create_pay_order(api_id, power, system_key)` | `POST {base}/BuaaPay/Pay` | 创建缴费订单，返回支付 URL；度数强制转 int |

`_safe_float` 兜底处理空值。注意：`create_pay_order` 用的是学校内部 `id`（来自 `fetch_meter_info` 的返回），**不是** `identity_no`，也不是本地数据库 id。

> **代理绕过（重点）**：学校系统都在校园内网（`10.x.x.x`）。若本机开了代理/VPN（如 Clash `127.0.0.1:7897`），`requests` 默认读系统代理、把内网请求也丢进隧道 → 内网 IP 不可达 → **502**。因此本模块用一个模块级 `_SESSION = requests.Session()` 且 `_SESSION.trust_env = False` 来**强制直连、绕过任何系统代理**，三个接口函数全部走它。**新增任何校内请求都要用 `_SESSION`，不要直接 `requests.get/post`。**

#### `license_manager.py` — 授权 / 许可证（闭源收费命门）
非对称签名授权：**App 内只内置公钥（`LICENSE_PUBLIC_KEY_B64`），只能验签、无法伪造**；签发私钥只在版权方本机 `secrets/`，绝不入库/打包。
- `get_machine_id()`：本机稳定机器码（Windows 取注册表 `MachineGuid` 加盐 SHA-256，截 16 位十六进制）；`format_machine_id` 格式化成 `ABCD-EFGH-IJKL-MNOP` 给用户复制。
- 许可证串格式：`XRTJ.<base64url(payload)>.<base64url(签名)>`；payload 含 `tier/mid/iat/exp/jti/note`，`mid='*'` 为浮动许可（不绑机器）。
- `verify_license(license_str, machine_id)`：四重校验——①Ed25519 验签 ②机器绑定 ③到期 ④在线吊销；返回 `{valid, tier, exp, jti, reason}`，`reason` 为中文可直接展示。
- **在线吊销**：从 `config.REVOCATION_LIST_URL`（Gitee raw JSON `{"revoked":[jti,...]}`）拉名单，按 `REVOCATION_TTL_HOURS` 缓存到 `data/revocation_cache.json`；**拉取失败软放行**（不误伤付费用户）。URL 留空=纯离线、不查吊销。
- 签发工具：`python tools/gen_keys.py`（一次性生成密钥对）、`python tools/gen_license.py --machine <机器码> [--days N] [--floating] [--note ..]`（用私钥签发，记录进 `secrets/issued_licenses.csv`；吊销时把 `jti` 加进 Gitee 的 `revoked.json`）。

#### `scheduler.py` — 定时轮询（业务核心）
- `start_scheduler(app)`：建 `BackgroundScheduler`，按当前间隔加 `IntervalTrigger` 任务。`coalesce=True` + `max_instances=1` 防止任务堆积。
- **`poll_all_meters()`** —— 核心循环，对每个电表：
  1. `buaa_api.fetch_meter_info` 取数（按 `system_key` 路由）
  2. `database.add_reading` 存读数
  3. `remain < threshold` → 判级别（`<=1.0` 为 `critical`，否则 `warning`）
  4. `database.should_notify` 去重检查 → `notifier.notify_all` 发告警 → `mark_notified`
  5. `remain >= threshold*2` 视为已充值 → `reset_notification` 重置告警状态
- `update_poll_interval(hours)` / `_get_poll_interval()`：间隔受等级约束（免费固定 12h；赞助 2–24h，须 2 的倍数）。

#### `notifier.py` — 多渠道通知
统一入口 **`notify_all(title, body, url, db, vapid_keys)`**，并发触发三渠道：
1. `DesktopNotifier`（win11toast）：Windows Toast + `winsound` 提示音；非 Windows 优雅降级。
2. `WebPushNotifier`（pywebpush）：向所有订阅推送；收到 410 Gone 自动清理失效订阅。**依赖境外推送服务（Google FCM 等），大陆网络常不可达**。
3. `ServerChanNotifier`（微信）：**大陆最可靠的手机提醒**。自动识别两种 SendKey：`SCT`（Turbo 版）→ `sctapi.ftqq.com`；`sctp{uid}t`（Server酱³）→ `{uid}.push.ft07.com`。`send()` 返回 `(success, message)` 便于"测试发送"。

#### `cert_manager.py` — 密钥与网络工具
- `ensure_vapid_keys()` / `get_vapid_keys()`：生成/读取 `data/vapid_keys.json`，含 base64url 公钥（前端 `applicationServerKey`）和 PEM 私钥（pywebpush 签名）。
- `get_local_ip()`：用 UDP socket 连 8.8.8.8 的技巧取本机局域网 IP（用于二维码访问地址），失败回退 `127.0.0.1`。

#### 前端 `static/js/app.js`（IIFE，全局 `state`）
单文件承载全部前端逻辑。结构分区（按注释块）：
- **UTILITIES**：`api(method, path, body)` 统一请求封装（自动解包 `{success,data}`）。
- **三个 Tab**：
  - `loadDashboard` → `loadMeters` → `renderDashboard`/`renderMeterCard`（仪表盘卡片 + 环形电量表 + Chart.js 趋势图）。
  - `loadConfigTab` → **级联下拉**：`loadMeterSystems`→`populateSystemSelect`→`onSystemChange`→`loadMeterOptions`→`populateCampusSelect`→`onCampusChange`→…→`onRoomChange`。`state.meterOptionsCache` 按系统缓存选项避免重复请求。`saveMeter` 提交新增。
  - `loadSettingsTab`：等级展示、轮询间隔、赞助激活、Server酱 微信、Web Push 开关。
- **PUSH**：`ensureServiceWorker`（注册 `/sw.js` 并等到 `activated`）、`subscribePush`/`unsubscribePush`，`withTimeout` 防 FCM 卡死。
- 前端状态对象 `state` 持有 `meters / systems / currentSystem / meterOptions / tier / settings / chart` 等。

#### `static/sw.js` — Service Worker
监听 `push` 事件，解析 payload（`{title, body, url}`），用内联 SVG 图标 `showNotification`；`notificationclick` 聚焦/打开页面。

---

## 5. 关键数据流（端到端）

### 添加一个电表
```
前端选系统/校区/楼/层/房/表 (app.js 级联下拉)
  → POST /api/meters {system_key, identity_no, threshold, ...}
  → database.add_meter() 校验等级上限+去重 → 写 meters 表
  → 前端自动触发 POST /api/poll-now 拉首次读数
```

### 一次定时轮询告警
```
APScheduler 周期触发 → scheduler.poll_all_meters()
  对每个电表:
    buaa_api.fetch_meter_info(identity_no, system_key)   # 按系统路由 host
    database.add_reading()
    if remain < threshold and database.should_notify():
        notifier.notify_all()   # 桌面 + WebPush + 微信
        database.mark_notified()
    if remain >= threshold*2:
        database.reset_notification()
```

### 充值
```
前端点充值 → 同步先 window.open 占位（防弹窗拦截）
  → POST /api/charge/<identity_no> {amount}
  → database.get_system_key_by_identity()  # 定位该表所属系统
  → buaa_api.fetch_meter_info() 取学校内部 id（若已有未完成订单 payStatus==0 直接返回 payUrl）
  → buaa_api.create_pay_order(api_id, amount, system_key) 返回支付 URL
  → 前端把占位窗口 location 跳到学校官方支付页
```

---

## 6. 启动与运行

```bash
# 安装依赖
pip install -r requirements.txt

# 启动（任选其一）
python app.py
# 或 Windows 双击 start.bat
# 或 双击 后台静默运行.vbs（无控制台窗口）
```

启动后：
- 本机访问 `http://localhost:5000`
- 局域网手机访问 `http://<局域网IP>:5000`（控制台会打印，或扫 `/api/qrcode`）
- 系统托盘有⚡图标，右键可「打开面板 / 立即轮询 / 退出」

> **推送须在本机浏览器（localhost）开启**——手机经局域网 IP 访问时不是安全上下文，Web Push 不可用，此时应改用 **微信 Server酱** 接收手机提醒。

### 打包分发（闭源收费）
双击 `build.bat`（或 `pyinstaller xrtj.spec`）→ 产物 `dist/夏日告急.exe`（单文件、窗口化，约 24MB），发给用户双击即用、无需装 Python。

打包相关的**路径与安全**要点：
- **路径**：`config.py` 用 `sys.frozen`/`sys._MEIPASS` 区分两类路径——只读资源 `static/` 经 `STATIC_DIR` 从解压目录读取；可写数据 `data/`（库/密钥/吊销缓存）经 `DATA_DIR` 锚定在 **exe 同级目录**，绝不放进会被清空的临时解压目录。新增任何文件读写都要走这两个常量，别用相对路径。
- **窗口化无控制台**：`app.py` 顶部检测 `sys.stdout is None` 时把输出重定向到 `data/run.log`（否则 `print` 会崩）。排查用户问题先看这个日志。
- **安全（务必）**：`xrtj.spec` 只把 `static/` 加进 `datas`；`secrets/`（私钥）、`data/`、`tools/`（签发工具）都**不被 import、不在 datas → 不进包**。改 spec 时切勿手滑把它们加进去。授权安全靠 Ed25519 非对称签名，**不依赖隐藏字节码**——解包 exe 也拿不到私钥、伪造不了许可证。
- 把 exe 放在**可写目录**（桌面/下载，别放 `Program Files`，那里默认不可写）。`data/`、`secrets/` 不要随 exe 发出去。

---

## 7. Debug 速查表

| 现象 | 先看这里 |
|------|----------|
| 「电表系统」下拉一直转「正在加载系统列表」 | `app.js: loadMeterSystems()`（占位文字未被替换说明请求失败或未返回）；后端 `app.py: /api/meter-systems`；`config.METER_SYSTEMS` 是否非空。该下拉只读本地配置，**不应**依赖学校网络 |
| 校区/楼/层/房 下拉空 | `buaa_api.fetch_meter_options`（学校 `PubBuaa/QueryIdData` 是否可达、返回字段名）；`app.js: populateCampusSelect` 起的级联链（注意该函数填完选项后须 `sel.disabled=false` 才能选） |
| **某个系统**校区「加载失败」/ 502（另一个系统却正常） | ①本机代理/VPN 把内网请求劫持了 → 确认 `buaa_api._SESSION.trust_env=False` 生效；②该系统 `base_url` 的 scheme 写错（`shsd` 必须 `http://`）。前端 toast 会显示后端真实原因（`app.js: loadMeterOptions` catch 已透出 `e.message`） |
| 电量不更新 | `scheduler.poll_all_meters` 日志；`buaa_api.fetch_meter_info` 重试 3 次是否全失败；间隔是否过长（`/api/tier` 看 `poll_interval`） |
| 充值报错 | `buaa_api.create_pay_order`（学校 `BuaaPay/Pay` 返回的错误文本会原样抛出）；确认 `system_key` 路由到正确 host |
| 收不到微信 | `notifier.ServerChanNotifier._endpoint`（SendKey 前缀判断）；`/api/serverchan/test` 返回的 message |
| 收不到浏览器推送 | 多半是 FCM 不可达（大陆网络）——这是已知限制，前端会回退提示；`cert_manager` 的 VAPID 密钥；`sw.js` 是否在根作用域注册 |
| 电量低却不告警 / 反复告警 | `database.should_notify` / `mark_notified` / `reset_notification` 的去重逻辑；阈值与 `remain` 关系 |
| 重复轮询 / 调度器双启 | 确认 `app.run(use_reloader=False)`；`scheduler` 全局单例 `_scheduler` |
| 中文乱码 | `app.json.ensure_ascii=False`；`buaa_api` HTTPError 分支有 gbk 兜底解码 |

通用：后端所有模块都用 `print("[标签] ...")` 打日志（`[轮询]`/`[API]`/`[推送]`/`[调度器]`/`[数据库]`/`[VAPID]`/`[通知]`/`[托盘]`），**直接看控制台输出最快定位**。

---

## 8. 设计约定 / 注意事项

- **返回结构统一**：后端 API 一律 `{"success": bool, "data": ...}` 或 `{"success": false, "error": "..."}`；前端 `api()` 会自动解包 `data`。
- **system_key 路由**：任何涉及学校接口的调用都要带正确 `system_key`，否则会打到错误的 host。新增电表系统只需在 `config.METER_SYSTEMS` 加一行（**勿改动已上线 key**，它存在数据库里）。
- **identity_no vs 学校内部 id**：列表用 `identityNo`，缴费要用 `fetch_meter_info` 返回的 `id`。别混。
- **无证书是有意为之**：不要试图加 HTTPS/自签名证书，那正是本项目要规避的坑。
- **`data/` 不应入库**：含本机数据库和密钥。
