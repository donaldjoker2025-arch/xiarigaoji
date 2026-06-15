# -*- coding: utf-8 -*-
"""
夏日告急 — 许可证签发工具（仅供版权方本地运行，**不随 App 分发**）

用买家提供的「机器码」+ 你的私钥，签发一张绑定该机器的许可证字符串，回发给买家粘贴激活。

前置：先运行过 tools/gen_keys.py 生成 secrets/license_private_key.pem。

用法示例（项目根目录）：
    # 永久 · 绑定某台机器
    python tools/gen_license.py --machine A1B2-C3D4-E5F6-7890 --note "buyer@x.com"

    # 一年期 · 绑定机器
    python tools/gen_license.py --machine A1B2C3D4E5F67890 --days 365

    # 浮动许可（任意机器可用，仅防伪造，不绑机器）
    python tools/gen_license.py --floating --note "团队授权"

签发记录会追加到 secrets/issued_licenses.csv（含 jti / 机器码 / 到期 / 备注），
吊销时把对应 jti 加进 Gitee 的 revoked.json 即可。
"""
import os
import sys
import csv
import time
import argparse
import secrets as pysecrets

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

# 让脚本能 import 项目根目录的 license_manager
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import license_manager as lm  # noqa: E402

PRIV_PATH = os.path.join('secrets', 'license_private_key.pem')
LEDGER_PATH = os.path.join('secrets', 'issued_licenses.csv')


def _load_private_key() -> Ed25519PrivateKey:
    if not os.path.exists(PRIV_PATH):
        print(f'[X] 找不到私钥 {PRIV_PATH}，请先运行：python tools/gen_keys.py')
        sys.exit(1)
    with open(PRIV_PATH, 'rb') as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def _normalize_machine(code: str) -> str:
    """买家可能带横线/小写，这里统一成 16 位大写十六进制。"""
    return (code or '').replace('-', '').replace(' ', '').strip().upper()


def _record_ledger(row: dict):
    os.makedirs('secrets', exist_ok=True)
    exists = os.path.exists(LEDGER_PATH)
    with open(LEDGER_PATH, 'a', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=['jti', 'tier', 'mid', 'iat', 'exp', 'note'],
                           extrasaction='ignore')
        if not exists:
            w.writeheader()
        w.writerow(row)


def main():
    ap = argparse.ArgumentParser(description='签发夏日告急赞助许可证')
    ap.add_argument('--machine', '-m', help='买家的机器码（16 位十六进制，可带横线）')
    ap.add_argument('--floating', action='store_true', help='浮动许可：不绑机器（mid=*）')
    ap.add_argument('--tier', default='sponsor', help="等级，默认 sponsor")
    ap.add_argument('--days', type=int, default=0, help='有效天数；0 或省略=永久')
    ap.add_argument('--note', default='', help='备注（买家邮箱/订单号等）')
    args = ap.parse_args()

    if not args.floating and not args.machine:
        ap.error('需要 --machine <机器码>，或用 --floating 签发浮动许可')

    mid = '*' if args.floating else _normalize_machine(args.machine)
    if mid != '*' and len(mid) != 16:
        ap.error(f'机器码应为 16 位十六进制，当前为 {len(mid)} 位：{mid}')

    iat = int(time.time())
    exp = (iat + args.days * 86400) if args.days and args.days > 0 else None

    payload = {
        'v': 1,
        'jti': pysecrets.token_hex(4),   # 8 位十六进制唯一编号，用于吊销
        'tier': args.tier,
        'mid': mid,
        'iat': iat,
        'exp': exp,
        'note': args.note or '',
    }

    priv = _load_private_key()
    signature = priv.sign(lm.canonical_payload(payload))
    license_str = lm.encode_license(payload, signature)

    _record_ledger(payload)

    exp_str = time.strftime('%Y-%m-%d', time.localtime(exp)) if exp else '永久'
    print('=== 许可证已签发 ===')
    print(f"  jti : {payload['jti']}   绑定: {mid}   到期: {exp_str}")
    if args.note:
        print(f"  备注: {args.note}")
    print('  许可证字符串（发给买家粘贴）：')
    print()
    print(license_str)


if __name__ == '__main__':
    main()
