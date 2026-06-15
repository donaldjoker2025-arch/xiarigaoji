# -*- coding: utf-8 -*-
"""
夏日告急 — 许可证（授权）管理模块（客户端，随 App 分发）

职责：
- 计算本机稳定「机器码」（许可证按机器绑定用）
- 用内置 Ed25519 公钥验证许可证签名（只能验签，无法伪造）
- 解析许可证里的等级 / 到期 / 机器绑定 / 唯一编号(jti)
- 可选的在线吊销检查：从 Gitee raw JSON 拉取吊销名单（失败时软放行，不误伤付费用户）

安全模型（闭源收费命门）：
- 签发私钥只在版权方本机（secrets/license_private_key.pem），**绝不**随 App 分发；
- App 内只有公钥，反编译也造不出有效许可证；
- 许可证默认绑定机器码 → 一码多机共享会失败（mid 为 '*' 时为浮动许可，不绑机器）。

许可证字符串格式（便于粘贴）：
    XRTJ.<base64url(payload_json)>.<base64url(signature)>
payload 字段：
    v    版本号(=1)
    jti  许可证唯一编号（用于吊销）
    tier 等级（'sponsor'）
    mid  绑定的机器码；'*' 表示浮动（任意机器）
    iat  签发时间(unix秒)
    exp  到期时间(unix秒)；None/缺省 表示永久
    note 备注（买家邮箱/订单号等，可选）
"""
import os
import json
import time
import base64
import platform
import hashlib
import uuid
from typing import Dict, Any, Optional, Set, Tuple

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

import config

# ============================================================
#  内置验签公钥（base64url，无填充）。由 tools/gen_keys.py 生成后粘贴到此处。
#  公钥可随包分发——它只能验证签名，不能签发许可证。
# ============================================================
LICENSE_PUBLIC_KEY_B64 = "XrwTtTsGVWSrcujpNkTlL9_QJ11U1FeqmgJcYkYoa_c"

LICENSE_PREFIX = "XRTJ"

# 机器码盐值：换掉它会让所有“按机器绑定”的旧许可证失效，勿随意改动。
_MACHINE_SALT = "xiarigaoji::machine::v1"

# 吊销名单缓存（写在 data/ 下）
_REVOCATION_CACHE_PATH = os.path.join(config.CERT_DIR, 'revocation_cache.json')
# 进程内缓存： {'revoked': set(), 'ts': float}
_revocation_mem: Dict[str, Any] = {'revoked': None, 'ts': 0.0}


# ------------------------------------------------------------
#  base64url 辅助
# ------------------------------------------------------------
def _b64u_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode('ascii').rstrip('=')


def _b64u_decode(s: str) -> bytes:
    pad = '=' * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def canonical_payload(payload: Dict[str, Any]) -> bytes:
    """许可证签名/验签的规范字节序列——键排序、无多余空白，确保两端一致。"""
    return json.dumps(
        payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False
    ).encode('utf-8')


def encode_license(payload: Dict[str, Any], signature: bytes) -> str:
    return f"{LICENSE_PREFIX}.{_b64u_encode(canonical_payload(payload))}.{_b64u_encode(signature)}"


def decode_license(license_str: str) -> Tuple[Dict[str, Any], bytes]:
    """拆解许可证字符串 → (payload dict, signature bytes)。格式错误抛 ValueError。"""
    parts = (license_str or '').strip().split('.')
    if len(parts) != 3 or parts[0] != LICENSE_PREFIX:
        raise ValueError('许可证格式不正确')
    payload = json.loads(_b64u_decode(parts[1]).decode('utf-8'))
    signature = _b64u_decode(parts[2])
    return payload, signature


# ------------------------------------------------------------
#  机器码
# ------------------------------------------------------------
def _raw_machine_identifier() -> str:
    """取一个尽量稳定的本机标识原串（未哈希）。"""
    # Windows 首选注册表 MachineGuid——重装软件不变，最稳定
    try:
        if platform.system() == 'Windows':
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography'
            )
            val, _ = winreg.QueryValueEx(key, 'MachineGuid')
            winreg.CloseKey(key)
            if val:
                return 'win:' + str(val)
    except Exception:
        pass
    # 跨平台兜底：主机名 + 网卡 MAC（getnode）
    return 'gen:%s:%s' % (platform.node(), uuid.getnode())


def get_machine_id() -> str:
    """本机机器码：16 位十六进制（对原始标识做加盐 SHA-256，避免泄露原始 GUID）。"""
    raw = _raw_machine_identifier()
    digest = hashlib.sha256((_MACHINE_SALT + '|' + raw).encode('utf-8')).hexdigest().upper()
    return digest[:16]


def format_machine_id(code: str) -> str:
    """把 16 位机器码格式化成 ABCD-EFGH-IJKL-MNOP，便于用户复制转述。"""
    code = (code or '').upper()
    return '-'.join(code[i:i + 4] for i in range(0, len(code), 4))


# ------------------------------------------------------------
#  在线吊销名单（Gitee raw JSON，失败软放行）
# ------------------------------------------------------------
def _load_revocation_cache() -> Set[str]:
    try:
        with open(_REVOCATION_CACHE_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return set(data.get('revoked', []))
    except Exception:
        return set()


def _save_revocation_cache(revoked: Set[str]):
    try:
        os.makedirs(config.CERT_DIR, exist_ok=True)
        with open(_REVOCATION_CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump({'revoked': sorted(revoked), 'ts': time.time()}, f)
    except Exception:
        pass


def _refresh_revocation_list() -> Optional[Set[str]]:
    """从 config.REVOCATION_LIST_URL 拉取吊销名单。成功返回集合，失败返回 None。"""
    url = getattr(config, 'REVOCATION_LIST_URL', '') or ''
    if not url:
        return None
    try:
        import requests
        # Gitee 是公网普通站点，按系统默认（含代理）访问即可；超时要短，避免拖慢激活
        resp = requests.get(url, timeout=6, headers={'Cache-Control': 'no-cache'})
        resp.raise_for_status()
        data = resp.json()
        revoked = set(str(x) for x in data.get('revoked', []))
        _save_revocation_cache(revoked)
        return revoked
    except Exception as e:
        print(f"[授权] 拉取吊销名单失败（软放行）：{e}")
        return None


def is_revoked(jti: str) -> bool:
    """该许可证编号是否在吊销名单中。失败软放行（返回 False），不误伤付费用户。"""
    if not jti:
        return False
    url = getattr(config, 'REVOCATION_LIST_URL', '') or ''
    if not url:
        return False  # 未配置吊销名单 → 纯离线模式，不检查

    ttl = getattr(config, 'REVOCATION_TTL_HOURS', 6) * 3600
    now = time.time()

    # 进程内缓存未过期 → 直接用
    if _revocation_mem['revoked'] is not None and (now - _revocation_mem['ts']) < ttl:
        return jti in _revocation_mem['revoked']

    # 过期或首次：尝试刷新
    fresh = _refresh_revocation_list()
    if fresh is not None:
        _revocation_mem['revoked'] = fresh
        _revocation_mem['ts'] = now
        return jti in fresh

    # 刷新失败：退回磁盘缓存；再没有就软放行
    cached = _load_revocation_cache()
    _revocation_mem['revoked'] = cached
    _revocation_mem['ts'] = now  # 避免每次都重试网络
    return jti in cached


# ------------------------------------------------------------
#  验证许可证
# ------------------------------------------------------------
def _public_key() -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(_b64u_decode(LICENSE_PUBLIC_KEY_B64))


def verify_license(license_str: str, machine_id: str,
                   check_revocation: bool = True) -> Dict[str, Any]:
    """
    验证许可证。返回：
        {'valid': bool, 'tier': str|None, 'exp': int|None, 'jti': str|None, 'reason': str}
    reason 在 valid=False 时给出中文原因，便于前端直接展示。
    """
    # 1) 解析
    try:
        payload, signature = decode_license(license_str)
    except Exception:
        return {'valid': False, 'tier': None, 'exp': None, 'jti': None,
                'reason': '许可证格式不正确，请检查是否复制完整'}

    # 2) 验签（防伪造的核心）
    try:
        _public_key().verify(signature, canonical_payload(payload))
    except InvalidSignature:
        return {'valid': False, 'tier': None, 'exp': None, 'jti': payload.get('jti'),
                'reason': '许可证签名无效（伪造或被篡改）'}
    except Exception:
        return {'valid': False, 'tier': None, 'exp': None, 'jti': None,
                'reason': '许可证校验失败'}

    tier = payload.get('tier')
    exp = payload.get('exp')
    jti = payload.get('jti')
    mid = payload.get('mid', '*')

    # 3) 机器绑定
    if mid and mid != '*' and mid != machine_id:
        return {'valid': False, 'tier': tier, 'exp': exp, 'jti': jti,
                'reason': '许可证未授权给本机（机器码不匹配）'}

    # 4) 到期
    if exp is not None:
        try:
            if time.time() > float(exp):
                return {'valid': False, 'tier': tier, 'exp': exp, 'jti': jti,
                        'reason': '许可证已过期'}
        except (TypeError, ValueError):
            return {'valid': False, 'tier': tier, 'exp': exp, 'jti': jti,
                    'reason': '许可证到期字段异常'}

    # 5) 在线吊销
    if check_revocation and is_revoked(jti):
        return {'valid': False, 'tier': tier, 'exp': exp, 'jti': jti,
                'reason': '许可证已被吊销'}

    return {'valid': True, 'tier': tier, 'exp': exp, 'jti': jti, 'reason': 'ok'}
