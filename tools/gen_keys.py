# -*- coding: utf-8 -*-
"""
夏日告急 — 许可证签名密钥对生成工具（一次性，仅供版权方本地运行）

生成 Ed25519 密钥对：
  - 私钥写入  secrets/license_private_key.pem  （已被 .gitignore；签发许可证用，绝不可泄露/入库/打包进 exe）
  - 公钥打印到控制台                            （粘贴到 license_manager.LICENSE_PUBLIC_KEY_B64，随 App 分发，只能验签不能签发）

非对称签名是闭源收费的命门：App 里只放公钥，反编译也无法伪造许可证。

用法（在项目根目录）：
    python tools/gen_keys.py
"""
import os
import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

SECRETS_DIR = 'secrets'
PRIV_PATH = os.path.join(SECRETS_DIR, 'license_private_key.pem')


def _b64u(b: bytes) -> str:
    """base64url 编码且去掉 '=' 填充。"""
    return base64.urlsafe_b64encode(b).decode('ascii').rstrip('=')


def main():
    os.makedirs(SECRETS_DIR, exist_ok=True)

    if os.path.exists(PRIV_PATH):
        print(f'[!] 私钥已存在：{PRIV_PATH}')
        print('    若重新生成，所有已签发的许可证都会失效。如确需重置，请先手动删除该文件。')
        return

    priv = Ed25519PrivateKey.generate()
    pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    with open(PRIV_PATH, 'wb') as f:
        f.write(pem)

    pub_raw = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    print('[OK] 私钥已写入：', PRIV_PATH, '（保密！）')
    print()
    print('=== 公钥（base64url）——粘贴到 license_manager.py 的 LICENSE_PUBLIC_KEY_B64 ===')
    print(_b64u(pub_raw))


if __name__ == '__main__':
    main()
