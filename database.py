# -*- coding: utf-8 -*-
"""
夏日告急 - 数据库管理模块

使用 SQLite 存储电表配置、历史读数、推送订阅、通知记录和系统设置。
提供完整的 CRUD 操作和等级（免费/赞助）管理功能。
"""

import os
import json
import sqlite3
import time
from datetime import datetime
from typing import Optional, List, Dict, Any

import config
import license_manager


def _get_connection() -> sqlite3.Connection:
    """获取数据库连接，开启 WAL 模式提升并发性能"""
    # 确保 data 目录存在
    db_dir = os.path.dirname(config.DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row  # 以字典形式返回结果
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """
    初始化数据库，创建所有必要的表。
    使用 IF NOT EXISTS 确保幂等性，可重复调用。
    """
    conn = _get_connection()
    try:
        cursor = conn.cursor()

        # 系统设置表（键值对存储）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        # 电表配置表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS meters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campus TEXT NOT NULL,
                building TEXT NOT NULL,
                floor TEXT NOT NULL,
                room TEXT NOT NULL,
                meter_name TEXT,
                identity_no TEXT NOT NULL UNIQUE,
                threshold REAL DEFAULT 5.0,
                system_key TEXT DEFAULT 'xyl_ac',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 迁移：为早期版本创建的 meters 表补充 system_key 列
        cursor.execute("PRAGMA table_info(meters)")
        cols = [row['name'] for row in cursor.fetchall()]
        if 'system_key' not in cols:
            cursor.execute(
                "ALTER TABLE meters ADD COLUMN system_key TEXT DEFAULT 'xyl_ac'"
            )
            print("[数据库] 已为 meters 表添加 system_key 列")

        # 电量读数历史表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meter_id INTEGER NOT NULL,
                remain REAL,
                price REAL,
                reading_time TEXT,
                pay_status INTEGER,
                address TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (meter_id) REFERENCES meters(id)
            )
        """)

        # Web Push 推送订阅表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subscription_json TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 通知记录表（用于防重复通知）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meter_id INTEGER NOT NULL,
                level TEXT NOT NULL,
                remain REAL,
                notified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reset_at TIMESTAMP,
                FOREIGN KEY (meter_id) REFERENCES meters(id)
            )
        """)

        conn.commit()
        print("[数据库] 初始化完成")
    finally:
        conn.close()


# ========================
# 电表管理
# ========================

def add_meter(campus: str, building: str, floor: str, room: str,
              meter_name: str, identity_no: str,
              threshold: float = None, system_key: str = None) -> Dict[str, Any]:
    """
    添加电表配置。
    会根据用户等级检查电表数量限制：
    - 免费用户：最多 1 个电表
    - 赞助用户：最多 5 个电表

    Args:
        campus: 校区名称
        building: 楼栋名称
        floor: 楼层
        room: 房间号
        meter_name: 电表名称（可选别名）
        identity_no: 电表唯一标识号（学校系统中的 identityNo）
        threshold: 告警阈值，默认使用全局配置
        system_key: 电表所属系统 key（见 config.METER_SYSTEMS），默认默认系统

    Returns:
        dict: 包含新增电表信息或错误信息

    Raises:
        ValueError: 超出电表数量限制或电表已存在
    """
    if threshold is None:
        threshold = config.DEFAULT_THRESHOLD
    if not system_key:
        system_key = config.DEFAULT_SYSTEM_KEY

    conn = _get_connection()
    try:
        cursor = conn.cursor()

        # 检查电表数量限制
        sponsor = is_sponsor()
        max_meters = config.SPONSOR_MAX_METERS if sponsor else config.FREE_MAX_METERS
        cursor.execute("SELECT COUNT(*) as cnt FROM meters")
        current_count = cursor.fetchone()['cnt']

        if current_count >= max_meters:
            tier_name = "赞助用户" if sponsor else "免费用户"
            raise ValueError(
                f"{tier_name}最多可监控 {max_meters} 个电表，"
                f"当前已有 {current_count} 个"
            )

        # 检查是否已存在相同的 identity_no
        cursor.execute(
            "SELECT id FROM meters WHERE identity_no = ?", (identity_no,)
        )
        if cursor.fetchone():
            raise ValueError(f"电表 {identity_no} 已经添加过了")

        # 插入电表记录
        cursor.execute(
            """INSERT INTO meters
               (campus, building, floor, room, meter_name, identity_no, threshold, system_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (campus, building, floor, room, meter_name, identity_no, threshold, system_key)
        )
        conn.commit()

        meter_id = cursor.lastrowid
        return {
            'id': meter_id,
            'campus': campus,
            'building': building,
            'floor': floor,
            'room': room,
            'meter_name': meter_name,
            'identity_no': identity_no,
            'threshold': threshold,
            'system_key': system_key
        }
    finally:
        conn.close()


def get_meters() -> List[Dict[str, Any]]:
    """获取所有已配置的电表列表"""
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM meters ORDER BY created_at DESC"
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def get_system_key_by_identity(identity_no: str) -> Optional[str]:
    """
    根据电表 identity_no 查出它所属的系统 key（用于路由到正确的学校 API host）。

    Returns:
        str or None: 系统 key；电表不存在时返回 None
    """
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT system_key FROM meters WHERE identity_no = ?", (identity_no,)
        )
        row = cursor.fetchone()
        return row['system_key'] if row else None
    finally:
        conn.close()


def delete_meter(meter_id: int) -> bool:
    """
    删除指定电表及其关联的读数和通知记录。

    Args:
        meter_id: 电表 ID

    Returns:
        bool: 是否成功删除
    """
    conn = _get_connection()
    try:
        cursor = conn.cursor()

        # 先删除关联数据
        cursor.execute("DELETE FROM readings WHERE meter_id = ?", (meter_id,))
        cursor.execute("DELETE FROM notifications WHERE meter_id = ?", (meter_id,))

        # 删除电表本身
        cursor.execute("DELETE FROM meters WHERE id = ?", (meter_id,))
        deleted = cursor.rowcount > 0
        conn.commit()
        return deleted
    finally:
        conn.close()


def update_threshold(meter_id: int, threshold: float) -> bool:
    """
    更新电表的告警阈值。

    Args:
        meter_id: 电表 ID
        threshold: 新阈值（kWh）

    Returns:
        bool: 是否成功更新
    """
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE meters SET threshold = ? WHERE id = ?",
            (threshold, meter_id)
        )
        updated = cursor.rowcount > 0
        conn.commit()
        return updated
    finally:
        conn.close()


# ========================
# 读数管理
# ========================

def add_reading(meter_id: int, remain: float, price: float,
                reading_time: str, pay_status: int, address: str) -> int:
    """
    存储一条电量读数记录。

    Args:
        meter_id: 电表 ID
        remain: 剩余电量（kWh）
        price: 单价
        reading_time: 抄表时间（学校API返回的时间字符串）
        pay_status: 缴费状态
        address: 电表地址描述

    Returns:
        int: 新记录 ID
    """
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO readings 
               (meter_id, remain, price, reading_time, pay_status, address) 
               VALUES (?, ?, ?, ?, ?, ?)""",
            (meter_id, remain, price, reading_time, pay_status, address)
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_readings(meter_id: int, limit: int = 100) -> List[Dict[str, Any]]:
    """
    获取指定电表的历史读数，按时间倒序排列。

    Args:
        meter_id: 电表 ID
        limit: 返回记录数上限，默认 100

    Returns:
        list: 读数记录列表
    """
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT * FROM readings 
               WHERE meter_id = ? 
               ORDER BY created_at DESC 
               LIMIT ?""",
            (meter_id, limit)
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def get_latest_reading(meter_id: int) -> Optional[Dict[str, Any]]:
    """
    获取指定电表的最新一条读数。

    Args:
        meter_id: 电表 ID

    Returns:
        dict or None: 最新读数记录，没有则返回 None
    """
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT * FROM readings 
               WHERE meter_id = ? 
               ORDER BY created_at DESC 
               LIMIT 1""",
            (meter_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ========================
# Web Push 订阅管理
# ========================

def save_subscription(sub_json: str) -> bool:
    """
    保存 Web Push 推送订阅信息。
    如果已存在相同订阅则忽略。

    Args:
        sub_json: 订阅信息的 JSON 字符串

    Returns:
        bool: 是否成功保存（已存在返回 False）
    """
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO push_subscriptions (subscription_json) VALUES (?)",
                (sub_json,)
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            # 已存在相同订阅，忽略
            return False
    finally:
        conn.close()


def get_subscriptions() -> List[str]:
    """
    获取所有 Web Push 推送订阅。

    Returns:
        list: 订阅 JSON 字符串列表
    """
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT subscription_json FROM push_subscriptions")
        return [row['subscription_json'] for row in cursor.fetchall()]
    finally:
        conn.close()


def remove_subscription(sub_json: str) -> bool:
    """
    移除指定的 Web Push 推送订阅。

    Args:
        sub_json: 要移除的订阅 JSON 字符串

    Returns:
        bool: 是否成功移除
    """
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM push_subscriptions WHERE subscription_json = ?",
            (sub_json,)
        )
        deleted = cursor.rowcount > 0
        conn.commit()
        return deleted
    finally:
        conn.close()


# ========================
# 系统设置
# ========================

def get_setting(key: str) -> Optional[str]:
    """
    读取系统设置。

    Args:
        key: 设置键名

    Returns:
        str or None: 设置值，不存在返回 None
    """
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row['value'] if row else None
    finally:
        conn.close()


def set_setting(key: str, value: str):
    """
    写入系统设置（存在则更新，不存在则插入）。

    Args:
        key: 设置键名
        value: 设置值
    """
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )
        conn.commit()
    finally:
        conn.close()


# ========================
# 赞助等级管理（基于 Ed25519 许可证，见 license_manager.py）
# ========================

def _stored_license() -> Optional[str]:
    """读取已保存的许可证字符串。旧版对称激活码已废弃，不再兼容读取。"""
    return get_setting(config.LICENSE_SETTING_KEY)


def is_sponsor() -> bool:
    """当前是否为有效赞助用户：校验已保存许可证的签名 / 机器绑定 / 到期 / 吊销。"""
    lic = _stored_license()
    if not lic:
        return False
    res = license_manager.verify_license(lic, license_manager.get_machine_id())
    return bool(res.get('valid'))


def get_license_info() -> Dict[str, Any]:
    """返回当前许可证状态，供前端展示（含到期时间戳 expires_at）。"""
    lic = _stored_license()
    if not lic:
        return {'active': False, 'tier': None, 'expires_at': None, 'reason': '未激活'}
    res = license_manager.verify_license(lic, license_manager.get_machine_id())
    return {
        'active': bool(res.get('valid')),
        'tier': res.get('tier'),
        'expires_at': res.get('exp'),
        'jti': res.get('jti'),
        'reason': res.get('reason'),
    }


def activate_sponsor(license_str: str) -> Dict[str, Any]:
    """
    激活赞助：校验许可证（签名 + 机器绑定 + 到期 + 吊销），通过则保存。

    Args:
        license_str: 许可证字符串（形如 XRTJ.<payload>.<sig>）

    Returns:
        dict: {'success': bool, 'message': str}
    """
    license_str = (license_str or '').strip()
    if not license_str:
        return {'success': False, 'message': '请粘贴许可证'}

    res = license_manager.verify_license(license_str, license_manager.get_machine_id())
    if not res.get('valid'):
        return {'success': False, 'message': res.get('reason') or '许可证无效'}

    set_setting(config.LICENSE_SETTING_KEY, license_str)

    msg = '激活成功！已升级为赞助用户 🎉'
    exp = res.get('exp')
    if exp:
        msg += '（有效期至 ' + datetime.fromtimestamp(exp).strftime('%Y-%m-%d') + '）'
    return {'success': True, 'message': msg}


# ========================
# 通知防重复（Anti-Spam）
# ========================

def should_notify(meter_id: int, level: str) -> bool:
    """
    检查是否应该发送通知（防止重复通知轰炸）。

    规则：
    - 如果该电表该级别的通知没有被记录过，允许通知
    - 如果已通知且未重置（即电量未回升），不再通知
    - 通知级别示例：'warning'（低于阈值）、'critical'（接近耗尽）

    Args:
        meter_id: 电表 ID
        level: 通知级别（'warning' / 'critical'）

    Returns:
        bool: 是否应该发送通知
    """
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT id FROM notifications 
               WHERE meter_id = ? AND level = ? AND reset_at IS NULL
               ORDER BY notified_at DESC LIMIT 1""",
            (meter_id, level)
        )
        row = cursor.fetchone()
        # 如果没有未重置的通知记录，说明可以发送
        return row is None
    finally:
        conn.close()


def mark_notified(meter_id: int, level: str, remain: float):
    """
    记录已发送的通知，防止重复发送。

    Args:
        meter_id: 电表 ID
        level: 通知级别
        remain: 当时的剩余电量
    """
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO notifications (meter_id, level, remain) 
               VALUES (?, ?, ?)""",
            (meter_id, level, remain)
        )
        conn.commit()
    finally:
        conn.close()


def reset_notification(meter_id: int):
    """
    重置指定电表的所有通知状态（当检测到电量回升/充值后调用）。
    将所有未重置的通知记录标记 reset_at 时间。

    Args:
        meter_id: 电表 ID
    """
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute(
            """UPDATE notifications 
               SET reset_at = ? 
               WHERE meter_id = ? AND reset_at IS NULL""",
            (now, meter_id)
        )
        conn.commit()
    finally:
        conn.close()
