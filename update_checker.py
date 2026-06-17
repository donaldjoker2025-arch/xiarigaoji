# -*- coding: utf-8 -*-
"""
夏日告急 - 自动更新检查模块

定期查询 GitHub Releases API，检查是否有新版本发布。
如果有新版本，且用户尚未被通知过该版本，则调用 notifier 向所有渠道发送更新提醒。
"""

import requests
import config
import database
import notifier

def parse_version(v_str):
    """简单解析版本号为整数元组，例如 '2.0.1' -> (2, 0, 1)"""
    v_str = v_str.lstrip('vV')
    parts = []
    for part in v_str.split('.'):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)

def check_for_updates():
    """
    检查 GitHub Releases 是否有新版本。
    如果发现新版本且未曾提醒过，则触发告警通知。
    """
    try:
        url = f"https://api.github.com/repos/{config.GITHUB_REPO}/releases/latest"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        latest_tag = data.get('tag_name', '')
        if not latest_tag:
            return
            
        current_version = parse_version(config.APP_VERSION)
        latest_version = parse_version(latest_tag)
        
        if latest_version > current_version:
            # 发现新版本
            release_name = data.get('name', latest_tag)
            html_url = data.get('html_url', f"https://github.com/{config.GITHUB_REPO}/releases/latest")
            
            # 检查是否已经通知过该版本
            last_notified = database.get_setting('last_notified_update_version')
            if last_notified == latest_tag:
                return  # 已经通知过了
                
            # 发送更新通知
            title = f"🚀 夏日告急 发现新版本 {release_name}！"
            body = "开发者发布了新版本，强烈建议您前往 GitHub 下载最新版体验新功能并获得更好的稳定性。"
            
            print(f"[更新] 发现新版本: {latest_tag}，准备推送通知...")
            import cert_manager
            vapid_keys = cert_manager.get_vapid_keys()
            notifier.notify_all(title=title, body=body, url=html_url, vapid_keys=vapid_keys)
            
            # 记录已通知的版本号，避免重复骚扰
            database.set_setting('last_notified_update_version', latest_tag)

    except Exception as e:
        print(f"[更新] 检查更新失败: {e}")
