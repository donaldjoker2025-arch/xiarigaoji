# -*- coding: utf-8 -*-
"""
夏日告急 - 定时任务调度模块

使用 APScheduler 定时轮询所有已配置的电表，
获取最新电量数据并在低于阈值时触发通知。

轮询间隔根据用户等级动态调整：
- 免费用户：每 12 小时
- 赞助用户：可自定义 2-24 小时（2小时为最小步长）
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

import config
import database
import buaa_api
import notifier
import cert_manager

# 全局调度器实例
_scheduler = None

# Flask app 引用（用于在定时任务中访问应用上下文）
_flask_app = None

# 轮询任务 ID 常量
POLL_JOB_ID = 'poll_all_meters'


def start_scheduler(app):
    """
    启动定时轮询调度器。

    初始化 APScheduler，根据当前用户等级设置轮询间隔，
    并开始后台执行定时任务。

    Args:
        app: Flask 应用实例，用于在任务中获取应用上下文
    """
    global _scheduler, _flask_app
    _flask_app = app

    # 创建调度器（使用默认的线程池执行器）
    _scheduler = BackgroundScheduler(
        daemon=True,
        job_defaults={
            'coalesce': True,       # 合并错过的执行（防止堆积）
            'max_instances': 1,     # 同一任务最多1个实例在运行
            'misfire_grace_time': 3600  # 错过执行的容忍时间（秒）
        }
    )

    # 读取当前轮询间隔
    interval_hours = _get_poll_interval()

    # 添加轮询任务
    _scheduler.add_job(
        func=poll_all_meters,
        trigger=IntervalTrigger(hours=interval_hours),
        id=POLL_JOB_ID,
        name='电表数据轮询',
        replace_existing=True
    )

    _scheduler.start()
    print(f"[调度器] 已启动，轮询间隔: {interval_hours} 小时")


def poll_all_meters():
    """
    轮询所有已配置的电表，获取最新电量数据。

    对每个电表执行以下步骤：
    1. 调用学校API获取实时电量数据
    2. 将读数存入数据库
    3. 检查是否低于告警阈值
    4. 如果低于阈值且未重复通知，发送通知
    5. 如果电量回升（充值后），重置通知状态

    此函数可由定时任务自动调用，也可由手动触发。
    """
    global _flask_app

    print("[轮询] 开始轮询所有电表...")

    # 获取 VAPID 密钥（用于 Web Push）
    vapid_keys = cert_manager.get_vapid_keys()

    # 获取所有已配置的电表
    meters = database.get_meters()
    if not meters:
        print("[轮询] 没有配置任何电表，跳过")
        return

    success_count = 0
    fail_count = 0

    for meter in meters:
        identity_no = meter['identity_no']
        meter_id = meter['id']
        threshold = meter['threshold']
        meter_name = meter.get('meter_name') or meter.get('room', '未知')
        system_key = meter.get('system_key')

        print(f"[轮询] 查询电表: {meter_name} ({identity_no})")

        try:
            # 1. 调用学校API获取实时数据（按电表所属系统路由到正确 host）
            info = buaa_api.fetch_meter_info(identity_no, system_key)
            if info is None:
                print(f"[轮询] 电表 {meter_name} 查询失败，跳过")
                fail_count += 1
                continue

            remain = info.get('remain')
            price = info.get('price')
            reading_time = info.get('readingTime', '')
            pay_status = info.get('payStatus')
            address = info.get('address', '')

            if remain is None:
                print(f"[轮询] 电表 {meter_name} 剩余电量数据为空，跳过")
                fail_count += 1
                continue

            # 2. 存储读数
            database.add_reading(
                meter_id=meter_id,
                remain=remain,
                price=price,
                reading_time=reading_time,
                pay_status=pay_status,
                address=address
            )
            success_count += 1

            print(f"[轮询] {meter_name}: 剩余 {remain} kWh, 阈值 {threshold} kWh")

            # 3. 检查是否需要告警
            if remain < threshold:
                # 确定告警级别
                if remain <= 1.0:
                    level = 'critical'
                    title = '⚠️ 紧急：电量即将耗尽！'
                    body = f'{meter_name} 剩余电量仅 {remain:.1f} kWh，请立即充值！'
                else:
                    level = 'warning'
                    title = '⚡ 电量不足提醒'
                    body = f'{meter_name} 剩余电量 {remain:.1f} kWh，已低于阈值 {threshold:.1f} kWh'

                # 防重复通知检查
                if database.should_notify(meter_id, level):
                    print(f"[轮询] 触发 {level} 级别告警: {meter_name}")

                    # 发送通知
                    notifier.notify_all(
                        title=title,
                        body=body,
                        url='/',
                        db=database,
                        vapid_keys=vapid_keys
                    )

                    # 记录已通知
                    database.mark_notified(meter_id, level, remain)
                else:
                    print(f"[轮询] {meter_name} 已在 {level} 状态，不重复通知")

            # 4. 检查是否电量回升（充值后重置通知状态）
            # 当电量 >= 阈值 * 2 时认为已充值，重置通知
            if remain >= threshold * 2:
                database.reset_notification(meter_id)
                print(f"[轮询] {meter_name} 电量充足，已重置通知状态")

        except Exception as e:
            print(f"[轮询] 电表 {meter_name} 处理异常: {e}")
            fail_count += 1

    print(f"[轮询] 完成: {success_count} 成功, {fail_count} 失败")


def update_poll_interval(hours: int):
    """
    更新轮询间隔。赞助用户可以自定义轮询频率。

    Args:
        hours: 新的轮询间隔（小时）。
               赞助用户: 2-24 小时
               免费用户: 固定 12 小时

    Raises:
        ValueError: 间隔超出允许范围
    """
    global _scheduler

    # 验证权限和范围
    is_sponsor = database.is_sponsor()

    if is_sponsor:
        if hours < config.SPONSOR_MIN_POLL or hours > config.SPONSOR_MAX_POLL:
            raise ValueError(
                f"轮询间隔必须在 {config.SPONSOR_MIN_POLL}-{config.SPONSOR_MAX_POLL} 小时之间"
            )
        # 赞助用户要求以2小时为步长
        if hours % 2 != 0:
            raise ValueError("轮询间隔必须是2的倍数（如 2, 4, 6, ...）")
    else:
        # 免费用户只能使用默认间隔
        hours = config.FREE_POLL_INTERVAL

    # 保存设置
    database.set_setting('poll_interval', str(hours))

    # 重新调度任务
    if _scheduler and _scheduler.running:
        _scheduler.reschedule_job(
            POLL_JOB_ID,
            trigger=IntervalTrigger(hours=hours)
        )
        print(f"[调度器] 轮询间隔已更新为 {hours} 小时")
    else:
        print(f"[调度器] 设置已保存（{hours}小时），但调度器未运行")


def _get_poll_interval() -> int:
    """
    获取当前轮询间隔。

    优先从数据库设置读取，如果没有则根据用户等级返回默认值。

    Returns:
        int: 轮询间隔（小时）
    """
    saved = database.get_setting('poll_interval')
    if saved:
        try:
            interval = int(saved)
            # 验证范围
            if database.is_sponsor():
                return max(config.SPONSOR_MIN_POLL,
                           min(interval, config.SPONSOR_MAX_POLL))
            else:
                return config.FREE_POLL_INTERVAL
        except ValueError:
            pass

    return config.FREE_POLL_INTERVAL


def get_current_interval() -> int:
    """
    获取当前轮询间隔（公开接口）。

    Returns:
        int: 当前轮询间隔（小时）
    """
    return _get_poll_interval()
