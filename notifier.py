# -*- coding: utf-8 -*-
"""
夏日告急 - 通知管理模块

提供多渠道通知能力：
1. Windows 桌面 Toast 通知（使用 win11toast）
2. Web Push 浏览器推送通知（使用 pywebpush）
3. Server酱 微信推送（国内可达，无需加速器，手机锁屏后台可收）

当电量低于阈值时，同时触发桌面、浏览器推送、微信推送等所有已配置的通道。
"""

import json
import threading
from typing import Optional

import requests

import database


# ========================
# 桌面通知（Windows Toast）
# ========================

class DesktopNotifier:
    """
    Windows 桌面 Toast 通知管理器。
    使用 win11toast 库在 Windows 系统托盘弹出通知。
    非 Windows 系统下优雅降级（不报错）。
    """

    def __init__(self):
        """初始化桌面通知器，检测系统是否支持"""
        self._available = False
        try:
            from win11toast import notify
            self._notify = notify
            self._available = True
        except ImportError:
            print("[通知] win11toast 不可用，桌面通知已禁用")

    def show_toast(self, title: str, body: str, on_click_url: Optional[str] = None):
        """
        显示 Windows Toast 通知。

        Args:
            title: 通知标题
            body: 通知正文
            on_click_url: 点击通知时打开的URL（可选）
        """
        if not self._available:
            return

        try:
            # 在独立线程中发送通知，避免阻塞主线程
            def _send():
                try:
                    kwargs = {
                        'title': title,
                        'body': body,
                        'app_id': '夏日告急 - 电量监控',
                    }
                    if on_click_url:
                        kwargs['on_click'] = on_click_url
                    self._notify(**kwargs)
                except Exception as e:
                    print(f"[通知] 桌面通知发送失败: {e}")

            t = threading.Thread(target=_send, daemon=True)
            t.start()
        except Exception as e:
            print(f"[通知] 创建通知线程失败: {e}")

    def play_alert_sound(self):
        """
        播放系统告警声音。
        使用 Windows 内置的 winsound 模块播放提示音。
        """
        try:
            import winsound
            # 播放系统默认的"感叹号"提示音
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except ImportError:
            # 非 Windows 系统，忽略
            pass
        except Exception as e:
            print(f"[通知] 播放提示音失败: {e}")


# ========================
# Web Push 浏览器推送
# ========================

class WebPushNotifier:
    """
    Web Push 浏览器推送通知管理器。
    使用 VAPID 协议向已订阅的浏览器发送推送通知。
    支持多设备推送（电脑+手机浏览器同时接收）。
    """

    def __init__(self, vapid_keys: dict):
        """
        初始化 Web Push 推送器。

        Args:
            vapid_keys: VAPID 密钥对 {'private_pem': str, 'public_key': str}
        """
        self._vapid_keys = vapid_keys
        self._available = False

        try:
            from pywebpush import webpush, WebPushException
            self._webpush = webpush
            self._WebPushException = WebPushException
            self._available = True
        except ImportError:
            print("[通知] pywebpush 不可用，Web Push 推送已禁用")

    def send_push(self, subscription_json: str, title: str,
                  body: str, url: Optional[str] = None) -> bool:
        """
        向单个订阅发送 Web Push 通知。

        Args:
            subscription_json: 订阅信息 JSON 字符串
            title: 通知标题
            body: 通知正文
            url: 点击通知时跳转的URL

        Returns:
            bool: 是否发送成功
        """
        if not self._available:
            return False

        try:
            subscription_info = json.loads(subscription_json)

            # 构建通知数据
            # 注：图标由 Service Worker 的 push 处理器内置（内联 SVG），
            # 此处无需指定 icon/badge 路径。
            payload = json.dumps({
                'title': title,
                'body': body,
                'url': url or '/',
            }, ensure_ascii=False)

            # 构建 VAPID claims
            vapid_claims = {
                'sub': 'mailto:xrtj@buaa.edu.cn'
            }

            # 获取私钥 PEM
            private_key = self._vapid_keys.get('private_pem', '')

            self._webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=private_key,
                vapid_claims=vapid_claims,
            )
            return True

        except self._WebPushException as e:
            # 410 Gone 表示订阅已失效
            if hasattr(e, 'response') and e.response is not None:
                if e.response.status_code == 410:
                    print(f"[推送] 订阅已失效 (410 Gone)，将自动移除")
                    return False
            print(f"[推送] Web Push 发送失败: {e}")
            return False
        except Exception as e:
            print(f"[推送] Web Push 异常: {e}")
            return False

    def send_push_all(self, title: str, body: str,
                      url: Optional[str] = None, db=None):
        """
        向所有已保存的订阅发送 Web Push 通知。
        如果某个订阅返回 410 Gone，自动从数据库中移除。

        Args:
            title: 通知标题
            body: 通知正文
            url: 点击通知时跳转的URL
            db: 数据库模块引用（用于获取订阅列表和清理失效订阅）
        """
        if not self._available:
            return

        if db is None:
            db = database

        subscriptions = db.get_subscriptions()
        if not subscriptions:
            print("[推送] 没有活跃的 Web Push 订阅")
            return

        print(f"[推送] 向 {len(subscriptions)} 个订阅发送通知...")

        failed_subs = []

        for sub_json in subscriptions:
            success = self.send_push(sub_json, title, body, url)
            if not success:
                # 检查是否为失效订阅，标记移除
                failed_subs.append(sub_json)

        # 移除失效的订阅
        for sub_json in failed_subs:
            try:
                db.remove_subscription(sub_json)
                print(f"[推送] 已移除失效订阅")
            except Exception as e:
                print(f"[推送] 移除失效订阅失败: {e}")

        success_count = len(subscriptions) - len(failed_subs)
        print(f"[推送] 发送完成: {success_count}/{len(subscriptions)} 成功")


# ========================
# Server酱 微信推送
# ========================

class ServerChanNotifier:
    """
    Server酱 微信推送通知器。

    通过 Server酱（ServerChan）的 HTTP 接口把告警推送到用户微信。
    国内域名可达，无需加速器，手机锁屏/后台也能收到——这是大陆网络下
    最可靠的手机提醒方式（标准 Web Push 依赖境外推送服务，在此不可用）。

    自动兼容两套 sendkey：
    - Turbo 版（key 以 "SCT" 开头）：POST https://sctapi.ftqq.com/<key>.send
    - Server酱³（key 以 "sctp{uid}t" 开头）：POST https://<uid>.push.ft07.com/send/<key>.send
    """

    TIMEOUT = 10

    @staticmethod
    def _endpoint(sendkey: str) -> Optional[str]:
        """根据 sendkey 前缀推断正确的推送端点 URL。"""
        if not sendkey:
            return None
        key = sendkey.strip()
        if key.startswith('sctp'):
            # Server酱³：从 key 中提取 uid（sctp{uid}t...）
            import re
            m = re.match(r'^sctp(\d+)t', key)
            if not m:
                return None
            uid = m.group(1)
            return f"https://{uid}.push.ft07.com/send/{key}.send"
        if key.startswith('SCT'):
            # Turbo 版
            return f"https://sctapi.ftqq.com/{key}.send"
        return None

    @classmethod
    def send(cls, sendkey: str, title: str, body: str,
             url: Optional[str] = None) -> tuple:
        """
        发送一条 Server酱 微信推送。

        Args:
            sendkey: Server酱 SendKey
            title: 通知标题（微信消息标题）
            body: 通知正文（支持 markdown）
            url: 可选的跳转链接，会附在正文末尾

        Returns:
            tuple: (success: bool, message: str) 便于"测试发送"返回明确结果
        """
        endpoint = cls._endpoint(sendkey)
        if not endpoint:
            return False, 'SendKey 格式无法识别（应以 SCT 或 sctp 开头）'

        desp = body
        if url and url.startswith('http'):
            desp = f"{body}\n\n[打开监控面板]({url})"

        try:
            resp = requests.post(
                endpoint,
                data={'title': title, 'desp': desp},
                timeout=cls.TIMEOUT,
            )
            # Server酱 返回 JSON：{"code":0,...} 表示成功
            try:
                payload = resp.json()
            except ValueError:
                payload = {}

            code = payload.get('code')
            if resp.status_code == 200 and (code == 0 or code is None):
                return True, '发送成功'

            msg = payload.get('message') or payload.get('info') or f'HTTP {resp.status_code}'
            return False, f'Server酱返回错误: {msg}'
        except requests.exceptions.Timeout:
            return False, '连接 Server酱 超时'
        except requests.exceptions.RequestException as e:
            return False, f'网络错误: {e}'


def send_serverchan(title: str, body: str, url: Optional[str] = None,
                    db=None) -> tuple:
    """
    便捷函数：从数据库读取已保存的 Server酱 SendKey 并发送推送。

    Returns:
        tuple: (success, message)。未配置 SendKey 时返回 (False, 提示)。
    """
    if db is None:
        db = database

    sendkey = db.get_setting('serverchan_key')
    if not sendkey:
        return False, '未配置 Server酱 SendKey'

    return ServerChanNotifier.send(sendkey, title, body, url)


# ========================
# 全局通知器实例
# ========================

# 桌面通知器（单例）
_desktop_notifier = None


def _get_desktop_notifier() -> DesktopNotifier:
    """获取桌面通知器单例"""
    global _desktop_notifier
    if _desktop_notifier is None:
        _desktop_notifier = DesktopNotifier()
    return _desktop_notifier


def notify_all(title: str, body: str, url: Optional[str] = None,
               db=None, vapid_keys: Optional[dict] = None):
    """
    便捷函数：同时触发桌面通知和 Web Push 推送。

    这是外部模块调用通知功能的统一入口。
    会同时发送：
    1. Windows 桌面 Toast 通知
    2. Web Push 浏览器推送（发送到所有订阅的设备）

    Args:
        title: 通知标题
        body: 通知正文
        url: 点击通知时跳转的URL
        db: 数据库模块引用
        vapid_keys: VAPID 密钥对
    """
    if db is None:
        db = database

    print(f"[通知] 发送通知: {title} - {body}")

    # 1. 桌面 Toast 通知
    desktop = _get_desktop_notifier()
    desktop.show_toast(title, body, url)
    desktop.play_alert_sound()

    # 2. Web Push 推送（浏览器，依赖境外推送服务，大陆需加速器）
    if vapid_keys:
        web_push = WebPushNotifier(vapid_keys)
        # 在独立线程中发送，避免阻塞
        def _push():
            web_push.send_push_all(title, body, url, db)

        t = threading.Thread(target=_push, daemon=True)
        t.start()
    else:
        print("[通知] 未提供 VAPID 密钥，跳过 Web Push 推送")

    # 3. Server酱 微信推送（国内可达，手机锁屏/后台可收）
    def _serverchan():
        ok, msg = send_serverchan(title, body, url, db)
        if ok:
            print("[推送] Server酱 微信推送成功")
        else:
            print(f"[推送] Server酱 微信推送跳过/失败: {msg}")

    t2 = threading.Thread(target=_serverchan, daemon=True)
    t2.start()
