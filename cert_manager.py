# -*- coding: utf-8 -*-
"""
夏日告急 - 密钥与网络工具模块

负责：
1. VAPID 密钥对的生成与读取（用于 Web Push 推送通知）
2. 获取本机局域网 IP（用于生成手机扫码访问地址）

说明：本应用以纯 HTTP 在 localhost 上提供服务。localhost 本身即为浏览器
认可的"安全上下文"，Service Worker / Web Push / 通知 API 全部可用，因此
不再需要任何 HTTPS 自签名证书——这从根本上规避了自签名证书导致的
"ServiceWorker SSL certificate error" 问题。
"""

import os
import json
import socket
from typing import Dict, Optional

import config


def ensure_vapid_keys() -> Dict[str, str]:
    """
    确保 VAPID 密钥对存在。
    如果密钥文件不存在，自动生成新的密钥对。

    VAPID (Voluntary Application Server Identification) 密钥用于 Web Push 协议，
    让推送服务验证发送者身份。

    Returns:
        dict: {'public_key': str, 'private_key': str, 'private_pem': str}
    """
    keys_path = os.path.join(config.CERT_DIR, 'vapid_keys.json')

    # 确保目录存在
    os.makedirs(config.CERT_DIR, exist_ok=True)

    # 如果密钥已存在，直接返回
    if os.path.exists(keys_path):
        print("[VAPID] 已存在 VAPID 密钥")
        return get_vapid_keys()

    print("[VAPID] 生成 VAPID 密钥对...")

    import base64
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid

    vapid = Vapid()
    vapid.generate_keys()

    # py_vapid 的私钥需要从 PEM 中提取
    raw_priv = vapid.private_pem()

    # 获取原始公钥字节（未压缩点格式）并编码为 base64url，
    # 这是浏览器 applicationServerKey 所需的格式。
    pub_key_bytes = vapid.public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint
    )
    public_key_b64 = base64.urlsafe_b64encode(pub_key_bytes).decode('utf-8').rstrip('=')

    # 获取私钥的原始 32 字节标量并编码为 base64url
    priv_key_bytes = vapid.private_key.private_numbers().private_value.to_bytes(32, 'big')
    private_key_b64 = base64.urlsafe_b64encode(priv_key_bytes).decode('utf-8').rstrip('=')

    keys = {
        'public_key': public_key_b64,
        'private_key': private_key_b64,
        'private_pem': raw_priv.decode('utf-8') if isinstance(raw_priv, bytes) else raw_priv,
    }

    # 保存密钥
    with open(keys_path, 'w', encoding='utf-8') as f:
        json.dump(keys, f, indent=2, ensure_ascii=False)

    print(f"[VAPID] 密钥对已生成: {keys_path}")
    print(f"  - 公钥: {public_key_b64[:20]}...")

    return keys


def get_vapid_keys() -> Optional[Dict[str, str]]:
    """
    加载并返回 VAPID 密钥对。

    Returns:
        dict: {'public_key': str, 'private_key': str, 'private_pem': str}
        None: 密钥文件不存在
    """
    keys_path = os.path.join(config.CERT_DIR, 'vapid_keys.json')

    if not os.path.exists(keys_path):
        return None

    with open(keys_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def get_local_ip() -> str:
    """
    获取本机的局域网 IP 地址。

    通过创建一个 UDP socket 连接到外部地址来确定本机IP，
    这种方法不会实际发送数据，但能获取正确的网络接口地址。

    Returns:
        str: 本机局域网 IP 地址，获取失败返回 '127.0.0.1'
    """
    try:
        # 创建 UDP socket，连接到一个外部地址（不会真正发送数据）
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except (socket.error, OSError):
        # 备选方案：通过 hostname 获取
        try:
            hostname = socket.gethostname()
            ip = socket.gethostbyname(hostname)
            if ip.startswith('127.'):
                return '127.0.0.1'
            return ip
        except socket.error:
            return '127.0.0.1'
