# -*- coding: utf-8 -*-
"""
夏日告急 - 配置管理模块

存储所有全局配置常量，包括服务器、数据库、学校API地址、
用户等级（免费/赞助）相关参数等。
"""

import os
import sys


# ========================
# 路径解析（兼容 PyInstaller 打包）
# ========================
# 打包成 exe 后路径有两类，必须分开处理：
#   - 只读资源（static/）：被打进 exe，运行时解压到临时目录 sys._MEIPASS；
#   - 可写数据（data/：数据库、VAPID 密钥、吊销缓存）：必须放在一个**稳定且可写**
#     的目录，绝不能放进临时解压目录（每次退出会被清空 → 丢库）。
# 这里把可写数据锚定到 **exe 所在目录**（便携、数据随程序走）；源码运行时则用项目目录。

def _writable_base_dir() -> str:
    """可写运行时数据的基准目录（绝对路径，与当前工作目录无关）。"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包：exe 所在目录（不是临时解压目录）
        return os.path.dirname(sys.executable)
    # 源码运行：本文件所在的项目根目录
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(rel: str) -> str:
    """只读打包资源的绝对路径：打包后在 sys._MEIPASS，否则在项目目录。"""
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


BASE_DIR = _writable_base_dir()
DATA_DIR = os.path.join(BASE_DIR, 'data')   # 运行时可写数据目录
STATIC_DIR = resource_path('static')         # 只读前端资源目录

# ========================
# 服务器配置
# ========================
SERVER_HOST = '0.0.0.0'          # 监听地址，0.0.0.0 允许局域网访问
SERVER_PORT = 5000               # 服务端口

# ========================
# 数据库配置
# ========================
DB_PATH = os.path.join(DATA_DIR, 'buaa_power.db')  # SQLite 数据库路径（绝对）

# ========================
# 密钥目录
# ========================
CERT_DIR = DATA_DIR              # VAPID 密钥 / 吊销缓存存储目录（绝对）

# ========================
# 北航校园API地址
# ========================
# 学校 API 基础配置（学院路空调，向后兼容的默认系统）
BUAA_API_BASE_AC = 'https://xylktsd.buaa.edu.cn'

# ========================
# 多电表系统配置
# ========================
# 学校把不同校区/用途的电表拆成多个独立子系统，各自有独立的 API host，
# 但接口路径一致（PubBuaa/QueryIdData、BuaaPay/Meter、BuaaPay/Pay）。
# 在此登记各系统即可；前端「电表配置」会先让用户选系统，再级联选电表。
#
# 字段：
#   key      系统唯一标识（存入数据库，勿随意改动已上线的 key）
#   name     前端显示名
#   base_url 该系统 API 根地址
#
# 注：'shsd' 沙河系统的 host 由用户提供；若某系统同时覆盖空调与照明，
#     学校通常仍以 host 区分，照明 host 待补充后在此追加一行即可。
METER_SYSTEMS = [
    {'key': 'xyl_ac',  'name': '学院路空调表', 'base_url': 'https://xylktsd.buaa.edu.cn'},
    # 注意：shsd 这台内网服务器只提供 HTTP（443 端口无有效 TLS），必须用 http://，
    # 否则握手失败；且内网地址需绕过本机代理（见 buaa_api._SESSION）。
    {'key': 'shahe',   'name': '学院路照明表及沙河照明空调表', 'base_url': 'http://shsd.buaa.edu.cn'},
]

# 默认系统 key（用于历史数据/未标注系统的电表，保持向后兼容）
DEFAULT_SYSTEM_KEY = 'xyl_ac'

# ========================
# 电量告警阈值
# ========================
DEFAULT_THRESHOLD = 5.0          # 默认告警阈值（kWh），低于此值触发通知

# ========================
# 免费用户配置
# ========================
FREE_POLL_INTERVAL = 12          # 免费用户轮询间隔（小时）
FREE_MAX_METERS = 1              # 免费用户最多监控电表数

# ========================
# 赞助用户配置
# ========================
SPONSOR_MIN_POLL = 2             # 赞助用户最小轮询间隔（小时）
SPONSOR_MAX_POLL = 24            # 赞助用户最大轮询间隔（小时）
SPONSOR_MAX_METERS = 5           # 赞助用户最多监控电表数

# ========================
# 赞助激活 / 许可证（授权）
# ========================
# 授权改用 Ed25519 非对称签名（见 license_manager.py）：
#   - 签发私钥只在版权方本机 secrets/license_private_key.pem（已 .gitignore，绝不入库/打包）；
#   - App 内只内置公钥（license_manager.LICENSE_PUBLIC_KEY_B64），只能验签、无法伪造；
#   - 不再有任何「随包分发的密钥」，旧的对称 SPONSOR_SECRET 已废弃删除。

# settings 表里存许可证字符串的键
LICENSE_SETTING_KEY = 'license_key'

# 在线吊销名单（Gitee 仓库的 raw JSON，格式：{"revoked": ["<jti>", ...]}）。
# 留空 = 纯离线模式，不做吊销检查。示例：
#   https://gitee.com/<用户名>/<仓库>/raw/master/revoked.json
REVOCATION_LIST_URL = 'https://gitee.com/donaldjoker/xrtj-license/raw/master/revoked.json'
# 吊销名单缓存有效期（小时）：未过期就用本地缓存，过期才联网刷新；联网失败软放行。
REVOCATION_TTL_HOURS = 6
