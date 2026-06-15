# -*- coding: utf-8 -*-
"""
夏日告急 - 北航校园API封装模块

封装学校电量查询系统的HTTP接口，提供：
- 电表选项查询（级联选择：校区 > 楼栋 > 楼层 > 房间 > 电表）
- 电表实时数据查询（剩余电量、单价、缴费状态等）
- 创建缴费订单
"""

import requests
from typing import Dict, Any, Optional
from collections import OrderedDict

import config


# 请求超时时间（秒）
REQUEST_TIMEOUT = 10

# 请求头，模拟浏览器访问
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'zh-CN,zh;q=0.9',
}

# 学校的电量系统都部署在校园内网（10.x.x.x 私有地址）。如果本机开着
# 代理 / VPN（如 Clash 的 127.0.0.1:7897），requests 默认会读取系统代理设置
# 并把这些内网请求也丢给代理隧道，导致内网 IP 不可达、返回 502。
# 这里用一个 trust_env=False 的共享 Session 彻底绕过系统代理，
# 让所有校内接口请求直连——内网地址本就不应该走任何代理。
_SESSION = requests.Session()
_SESSION.trust_env = False



def resolve_base_url(system_key: Optional[str] = None) -> str:
    """
    根据系统 key 解析出对应的 API 根地址。

    Args:
        system_key: config.METER_SYSTEMS 中的系统 key；None 或未知时回退到默认系统。

    Returns:
        str: 该系统的 base_url
    """
    if system_key:
        for sys in config.METER_SYSTEMS:
            if sys['key'] == system_key:
                return sys['base_url']
    # 回退：默认系统，再不行用旧的 AC 常量，确保向后兼容
    for sys in config.METER_SYSTEMS:
        if sys['key'] == config.DEFAULT_SYSTEM_KEY:
            return sys['base_url']
    return config.BUAA_API_BASE_AC


def fetch_meter_options(system_key: Optional[str] = None) -> Dict[str, Any]:
    """
    从指定电表系统获取所有电表数据，并按级联结构组织。

    调用接口：GET {base_url}/PubBuaa/QueryIdData

    Args:
        system_key: 电表系统 key（见 config.METER_SYSTEMS）；None 时用默认系统。

    Returns:
        dict: 按 校区 > 楼栋 > 楼层 > 房间 > 电表列表 组织的嵌套字典

    Raises:
        ConnectionError / ValueError: 网络或数据格式异常
    """
    base_url = resolve_base_url(system_key)
    url = f"{base_url}/PubBuaa/QueryIdData"

    try:
        resp = _SESSION.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        raw_data = resp.json()
    except requests.exceptions.Timeout:
        raise ConnectionError("学校服务器响应超时，请稍后重试")
    except requests.exceptions.ConnectionError:
        raise ConnectionError("无法连接到学校服务器，请检查网络连接")
    except requests.exceptions.HTTPError as e:
        raise ConnectionError(f"学校服务器返回错误: {e.response.status_code}")
    except ValueError:
        raise ValueError("学校服务器返回的数据格式异常")

    # 将扁平列表转为级联结构
    cascaded = OrderedDict()

    for item in raw_data:
        campus = item.get('campus', '未知校区')
        building = item.get('building', '未知楼栋')
        floor = item.get('floor', '未知楼层')
        room = item.get('room', '未知房间')

        # 构建级联结构
        if campus not in cascaded:
            cascaded[campus] = OrderedDict()
        if building not in cascaded[campus]:
            cascaded[campus][building] = OrderedDict()
        if floor not in cascaded[campus][building]:
            cascaded[campus][building][floor] = OrderedDict()
        if room not in cascaded[campus][building][floor]:
            cascaded[campus][building][floor][room] = []

        # 添加电表信息
        meter_info = {
            'meterNo': item.get('meterNo', ''),
            'identityNo': item.get('identityNo', ''),
            'address': item.get('address', ''),
        }
        cascaded[campus][building][floor][room].append(meter_info)

    return cascaded


def fetch_meter_info(identity_no: str, system_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    查询指定电表的实时信息（剩余电量、单价、缴费状态等）。

    调用接口：GET {base_url}/BuaaPay/Meter?id={identityNo}

    Args:
        identity_no: 电表唯一标识号
        system_key: 电表所属系统 key（见 config.METER_SYSTEMS）

    Returns:
        dict: 电表信息（字段见下）；None: 查询失败
    """
    base_url = resolve_base_url(system_key)
    url = f"{base_url}/BuaaPay/Meter"
    params = {'id': identity_no}

    import time
    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = _SESSION.get(
                url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            data = resp.json()

            # 标准化字段名，确保返回一致的结构
            result = {
                'id': data.get('id'),
                'address': data.get('address', ''),
                'price': _safe_float(data.get('price')),
                'readingTime': data.get('readingTime', ''),
                'remain': _safe_float(data.get('remain')),
                'payStatus': data.get('payStatus'),
                'campus': data.get('campus', ''),
                'building': data.get('building', ''),
                'room': data.get('room', ''),
                'ct': data.get('ct', ''),
                'payUrl': data.get('payUrl', ''),
                'serial': data.get('serial', ''),
                'money': _safe_float(data.get('money')),
            }
            return result

        except requests.exceptions.Timeout:
            print(f"[API] 查询电表 {identity_no} 超时 (尝试 {attempt+1}/{max_retries})")
        except requests.exceptions.ConnectionError:
            print(f"[API] 无法连接学校服务器，查询电表 {identity_no} 失败 (尝试 {attempt+1}/{max_retries})")
        except requests.exceptions.HTTPError as e:
            print(f"[API] 查询电表 {identity_no} HTTP错误: {e.response.status_code} (尝试 {attempt+1}/{max_retries})")
        except (ValueError, KeyError) as e:
            print(f"[API] 解析电表 {identity_no} 数据失败: {e}")
            return None
            
        if attempt < max_retries - 1:
            time.sleep(2)
            
    return None


def create_pay_order(meter_id_from_api: str, write_power: float,
                     system_key: Optional[str] = None) -> Optional[str]:
    """
    创建充值/缴费订单，返回支付页面URL。

    调用接口：POST {base_url}/BuaaPay/Pay
    请求体：{id: meter_id_from_api, writePower: write_power}

    Args:
        meter_id_from_api: 学校API中的电表ID（不是本地数据库ID）
        write_power: 充值电量（度/kWh）
        system_key: 电表所属系统 key（见 config.METER_SYSTEMS）

    Returns:
        str: 支付页面URL
        None: 创建失败返回 None
    """
    # 强制将购买度数转为整数，学校接口可能拒绝浮点数
    try:
        write_power = int(float(write_power))
        if write_power <= 0:
            raise ValueError("充值金额必须大于0")
    except (ValueError, TypeError):
        raise Exception("无效的充值金额")

    base_url = resolve_base_url(system_key)
    url = f"{base_url}/BuaaPay/Pay"
    payload = {
        'id': meter_id_from_api,
        'writePower': write_power
    }

    try:
        resp = _SESSION.post(
            url, data=payload, headers=HEADERS, timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()

        # 接口直接返回支付URL字符串
        pay_url = resp.text.strip()

        # 如果返回的是JSON格式，尝试解析
        if pay_url.startswith('{') or pay_url.startswith('"'):
            try:
                import json
                parsed = json.loads(pay_url)
                if isinstance(parsed, str):
                    pay_url = parsed
                elif isinstance(parsed, dict):
                    pay_url = parsed.get('url', parsed.get('payUrl', pay_url))
            except (ValueError, json.JSONDecodeError):
                pass

        return pay_url if pay_url else None

    except requests.exceptions.Timeout:
        raise Exception("连接学校服务器超时，请稍后重试")
    except requests.exceptions.ConnectionError:
        raise Exception("无法连接学校服务器")
    except requests.exceptions.HTTPError as e:
        # 尝试读取并解码返回的错误信息
        error_msg = ""
        try:
            error_msg = e.response.content.decode('utf-8').strip()
        except UnicodeDecodeError:
            try:
                error_msg = e.response.content.decode('gbk', errors='ignore').strip()
            except:
                pass
        if error_msg:
            raise Exception(f"学校接口返回错误: {error_msg}")
        raise Exception(f"学校接口无响应 (HTTP {e.response.status_code})")
    except Exception as e:
        raise Exception(f"创建缴费订单异常: {str(e)}")


def _safe_float(value) -> Optional[float]:
    """
    安全转换数值，处理 None、空字符串等异常情况。

    Args:
        value: 需要转换的值

    Returns:
        float or None: 转换后的浮点数
    """
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
