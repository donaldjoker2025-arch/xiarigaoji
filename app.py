# -*- coding: utf-8 -*-
"""
夏日告急 - Flask 主应用

北航空调电量监控系统的后端服务入口。
提供 RESTful API、静态文件服务、系统托盘图标等功能。

启动流程：
1. 确保 data/ 目录存在
2. 初始化数据库
3. 生成 VAPID 密钥（用于 Web Push）
4. 启动定时轮询调度器
5. 启动系统托盘图标（后台线程）
6. 启动 Flask HTTP 服务
7. 自动打开浏览器

关于传输协议：
本服务以纯 HTTP 在 localhost 上提供。根据 W3C "Secure Contexts" 规范，
http://localhost 与 http://127.0.0.1 被所有现代浏览器视为安全上下文，
因此 Service Worker / Web Push / 通知 API 全部可用，且无需任何证书——
这彻底规避了自签名证书导致的 "ServiceWorker SSL certificate error" 顽疾。
"""

import os
import io
import json
import threading
import webbrowser

from flask import (
    Flask, request, jsonify, send_from_directory,
    send_file, Response
)

import config
import database
import buaa_api
import cert_manager
import scheduler
import notifier

# ========================
# Flask 应用初始化
# ========================

app = Flask(
    __name__,
    static_folder='static',
    static_url_path='/static'
)

# JSON 输出配置（Flask 3.x 通过 app.json 提供器设置）
# - 不排序键名，保持插入顺序
# - 不转义非 ASCII 字符，让中文以原文输出
app.json.sort_keys = False
app.json.ensure_ascii = False


# ========================
# CORS 跨域支持
# ========================

@app.after_request
def add_cors_headers(response):
    """
    为所有响应添加 CORS 头，允许局域网内的手机浏览器访问。
    """
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response


@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_static(path):
    """
    服务前端静态文件。
    默认返回 index.html（SPA 路由支持）。
    """
    if path and os.path.exists(os.path.join(app.static_folder or 'static', path)):
        return send_from_directory(app.static_folder or 'static', path)
    
    index_path = os.path.join(app.static_folder or 'static', 'index.html')
    if os.path.exists(index_path):
        return send_from_directory(app.static_folder or 'static', 'index.html')
    
    # 如果前端还没构建，返回占位页
    return Response(
        """<!DOCTYPE html>
        <html lang="zh-CN">
        <head><meta charset="utf-8"><title>夏日告急</title></head>
        <body style="font-family:sans-serif;text-align:center;padding:60px;">
            <h1>⚡ 夏日告急 - 空调电量监控</h1>
            <p>后端服务已启动，前端页面尚未部署。</p>
            <p>请将前端文件放置在 <code>static/</code> 目录下。</p>
            <hr>
            <p><a href="/api/tier">查看 API 状态</a></p>
        </body></html>""",
        content_type='text/html; charset=utf-8'
    )


# ========================
# 电表选项 API（代理学校接口）
# ========================

@app.route('/sw.js')
def serve_sw():
    return send_from_directory('static', 'sw.js', mimetype='application/javascript')

@app.route('/api/meter-systems', methods=['GET'])
def api_meter_systems():
    """
    列出可用的电表系统（学院路 / 沙河 等），供前端「电表配置」顶层选择。
    """
    systems = [
        {'key': s['key'], 'name': s['name']}
        for s in config.METER_SYSTEMS
    ]
    return jsonify({'success': True, 'data': systems})


@app.route('/api/meter-options', methods=['GET'])
def api_meter_options():
    """
    获取电表级联选项（校区 > 楼栋 > 楼层 > 房间 > 电表）。
    代理学校 API 的 QueryIdData 接口。

    查询参数:
        system: 电表系统 key（见 config.METER_SYSTEMS），缺省用默认系统。
    """
    system_key = request.args.get('system')
    try:
        options = buaa_api.fetch_meter_options(system_key)
        return jsonify({'success': True, 'data': options})
    except (ConnectionError, ValueError) as e:
        return jsonify({'success': False, 'error': str(e)}), 502
    except Exception as e:
        return jsonify({'success': False, 'error': f'获取电表列表失败: {e}'}), 500


@app.route('/api/meter-info/<identity_no>', methods=['GET'])
def api_meter_info(identity_no):
    """
    查询指定电表的实时信息。
    代理学校 API 的 BuaaPay/Meter 接口。

    Args:
        identity_no: 电表唯一标识号

    查询参数:
        system: 电表系统 key；缺省时若该表已入库则按库内系统，否则用默认系统。
    """
    system_key = request.args.get('system') or database.get_system_key_by_identity(identity_no)
    try:
        info = buaa_api.fetch_meter_info(identity_no, system_key)
        if info:
            return jsonify({'success': True, 'data': info})
        else:
            return jsonify({'success': False, 'error': '查询电表信息失败'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ========================
# 电表配置 CRUD API
# ========================

@app.route('/api/meters', methods=['POST'])
def api_add_meter():
    """
    添加电表监控配置。
    
    请求体 JSON:
    {
        "campus": "学院路校区",
        "building": "X号楼",
        "floor": "1",
        "room": "101",
        "meter_name": "我的宿舍",
        "identity_no": "xxx",
        "threshold": 5.0,
        "system_key": "xyl_ac"
    }
    """
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': '请求体不能为空'}), 400

    # 必填字段验证
    required = ['campus', 'building', 'floor', 'room', 'identity_no']
    for field in required:
        if not data.get(field):
            return jsonify({'success': False, 'error': f'缺少必填字段: {field}'}), 400

    try:
        meter = database.add_meter(
            campus=data['campus'],
            building=data['building'],
            floor=data['floor'],
            room=data['room'],
            meter_name=data.get('meter_name', ''),
            identity_no=data['identity_no'],
            threshold=data.get('threshold', config.DEFAULT_THRESHOLD),
            system_key=data.get('system_key')
        )
        return jsonify({'success': True, 'data': meter}), 201
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': f'添加电表失败: {e}'}), 500


@app.route('/api/meters', methods=['GET'])
def api_list_meters():
    """
    获取所有已配置的电表列表，每个电表附带最新读数。
    """
    try:
        meters = database.get_meters()

        # 为每个电表附加最新读数
        result = []
        for meter in meters:
            meter_data = dict(meter)
            latest = database.get_latest_reading(meter['id'])
            meter_data['latest_reading'] = latest
            result.append(meter_data)

        return jsonify({'success': True, 'data': result})
    except Exception as e:
        return jsonify({'success': False, 'error': f'获取电表列表失败: {e}'}), 500


@app.route('/api/meters/<int:meter_id>', methods=['DELETE'])
def api_delete_meter(meter_id):
    """
    删除指定电表配置及其历史数据。

    Args:
        meter_id: 电表 ID
    """
    try:
        deleted = database.delete_meter(meter_id)
        if deleted:
            return jsonify({'success': True, 'message': '电表已删除'})
        else:
            return jsonify({'success': False, 'error': '电表不存在'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': f'删除失败: {e}'}), 500


@app.route('/api/meters/<int:meter_id>/threshold', methods=['PUT'])
def api_update_threshold(meter_id):
    """
    更新指定电表的告警阈值。

    请求体 JSON:
    {
        "threshold": 10.0
    }
    """
    data = request.get_json()
    if not data or 'threshold' not in data:
        return jsonify({'success': False, 'error': '缺少 threshold 参数'}), 400

    try:
        threshold = float(data['threshold'])
        if threshold <= 0:
            return jsonify({'success': False, 'error': '阈值必须大于 0'}), 400

        updated = database.update_threshold(meter_id, threshold)
        if updated:
            return jsonify({'success': True, 'message': f'阈值已更新为 {threshold} kWh'})
        else:
            return jsonify({'success': False, 'error': '电表不存在'}), 404
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': '阈值必须是数字'}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': f'更新失败: {e}'}), 500


# ========================
# 历史读数 API
# ========================

@app.route('/api/readings/<int:meter_id>', methods=['GET'])
def api_get_readings(meter_id):
    """
    获取指定电表的历史读数。

    查询参数:
        limit: 返回记录数上限，默认 100
    """
    try:
        limit = request.args.get('limit', 100, type=int)
        limit = max(1, min(limit, 1000))  # 限制在 1-1000 之间

        readings = database.get_readings(meter_id, limit)
        return jsonify({'success': True, 'data': readings})
    except Exception as e:
        return jsonify({'success': False, 'error': f'获取读数失败: {e}'}), 500


# ========================
# Web Push 推送 API
# ========================

@app.route('/api/push/subscribe', methods=['POST'])
def api_push_subscribe():
    """
    保存 Web Push 推送订阅。

    请求体: 浏览器 PushSubscription 对象的 JSON
    """
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': '请求体不能为空'}), 400

    try:
        sub_json = json.dumps(data, sort_keys=True)
        saved = database.save_subscription(sub_json)
        msg = '订阅成功' if saved else '已经订阅过了'
        return jsonify({'success': True, 'message': msg})
    except Exception as e:
        return jsonify({'success': False, 'error': f'订阅失败: {e}'}), 500


@app.route('/api/push/subscribe', methods=['DELETE'])
def api_push_unsubscribe():
    """
    移除 Web Push 推送订阅。

    请求体: 浏览器 PushSubscription 对象的 JSON
    """
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': '请求体不能为空'}), 400

    try:
        sub_json = json.dumps(data, sort_keys=True)
        removed = database.remove_subscription(sub_json)
        msg = '已取消订阅' if removed else '未找到该订阅'
        return jsonify({'success': True, 'message': msg})
    except Exception as e:
        return jsonify({'success': False, 'error': f'取消订阅失败: {e}'}), 500


@app.route('/api/vapid-public-key', methods=['GET'])
def api_vapid_public_key():
    """
    获取 VAPID 公钥（前端 Service Worker 注册推送时需要）。
    """
    keys = cert_manager.get_vapid_keys()
    if keys:
        return jsonify({
            'success': True,
            'public_key': keys.get('public_key', '')
        })
    else:
        return jsonify({'success': False, 'error': 'VAPID 密钥未生成'}), 500


# ========================
# Server酱 微信推送 API
# ========================

@app.route('/api/serverchan', methods=['GET'])
def api_get_serverchan():
    """
    获取 Server酱 微信推送配置状态。
    出于安全，只返回是否已配置及 key 的脱敏预览，不回传完整 key。
    """
    key = database.get_setting('serverchan_key') or ''
    masked = ''
    if key:
        masked = (key[:6] + '****' + key[-4:]) if len(key) > 12 else (key[:3] + '****')
    return jsonify({
        'success': True,
        'data': {'configured': bool(key), 'masked_key': masked}
    })


@app.route('/api/serverchan', methods=['PUT'])
def api_set_serverchan():
    """
    保存 Server酱 SendKey。

    请求体 JSON: { "sendkey": "SCT..." 或 "sctp..." }
    传入空字符串则视为清除配置。
    """
    data = request.get_json()
    if data is None or 'sendkey' not in data:
        return jsonify({'success': False, 'error': '缺少 sendkey 参数'}), 400

    sendkey = (data.get('sendkey') or '').strip()

    # 清除配置
    if not sendkey:
        database.set_setting('serverchan_key', '')
        return jsonify({'success': True, 'message': '已清除微信推送配置'})

    # 基本格式校验：必须以 SCT 或 sctp 开头
    if not (sendkey.startswith('SCT') or sendkey.startswith('sctp')):
        return jsonify({
            'success': False,
            'error': 'SendKey 格式不正确，应以 SCT（Turbo版）或 sctp（Server酱³）开头'
        }), 400

    database.set_setting('serverchan_key', sendkey)
    return jsonify({'success': True, 'message': 'SendKey 已保存'})


@app.route('/api/serverchan/test', methods=['POST'])
def api_test_serverchan():
    """
    发送一条测试微信推送，验证 SendKey 是否可用。
    优先使用请求体中临时传入的 sendkey（便于保存前先测），
    否则使用数据库中已保存的 key。
    """
    data = request.get_json(silent=True) or {}
    sendkey = (data.get('sendkey') or '').strip()

    if sendkey:
        ok, msg = notifier.ServerChanNotifier.send(
            sendkey,
            title='✅ 夏日告急 · 测试推送',
            body='如果你在微信收到这条消息，说明微信推送已配置成功！',
        )
    else:
        ok, msg = notifier.send_serverchan(
            title='✅ 夏日告急 · 测试推送',
            body='如果你在微信收到这条消息，说明微信推送已配置成功！',
            db=database,
        )

    status_code = 200 if ok else 400
    return jsonify({'success': ok, 'message': msg}), status_code


# ========================
# QR Code API
# ========================

@app.route('/api/qrcode', methods=['GET'])
def api_qrcode():
    """
    生成二维码 PNG 图片，内容为手机访问地址。
    手机扫码后可通过局域网访问监控面板。
    """
    try:
        import qrcode as qr_lib
        from PIL import Image

        # 获取局域网 IP 和端口
        lan_ip = cert_manager.get_local_ip()
        port = config.SERVER_PORT
        url = f"http://{lan_ip}:{port}"

        # 生成二维码
        qr = qr_lib.QRCode(
            version=1,
            error_correction=qr_lib.constants.ERROR_CORRECT_M,
            box_size=8,
            border=2,
        )
        qr.add_data(url)
        qr.make(fit=True)

        img = qr.make_image(fill_color='#1a1a2e', back_color='white')

        # 转换为字节流
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)

        return send_file(
            buf,
            mimetype='image/png',
            as_attachment=False,
            download_name='qrcode.png'
        )
    except ImportError:
        return jsonify({'success': False, 'error': 'qrcode 库未安装'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': f'生成二维码失败: {e}'}), 500


# ========================
# 充值 API
# ========================

@app.route('/api/charge/<identity_no>', methods=['POST'])
def api_charge(identity_no):
    """
    创建充值/缴费订单。
    先查询电表的 API ID，然后调用学校缴费接口。

    请求体 JSON:
    {
        "amount": 50  (充值金额/电量)
    }
    """
    data = request.get_json()
    if not data or 'amount' not in data:
        return jsonify({'success': False, 'error': '缺少 amount 参数'}), 400

    try:
        amount = int(float(data['amount']))
        if amount <= 0:
            return jsonify({'success': False, 'error': '充值金额必须大于 0'}), 400

        # 解析该电表所属系统，路由到正确的学校 API host
        system_key = database.get_system_key_by_identity(identity_no)

        # 先获取电表的 API 内部 ID
        meter_info = buaa_api.fetch_meter_info(identity_no, system_key)
        if not meter_info:
            return jsonify({'success': False, 'error': '查询电表信息失败'}), 404

        api_id = meter_info.get('id')
        if not api_id:
            return jsonify({'success': False, 'error': '无法获取电表缴费ID'}), 400

        # 如果存在未完成的订单 (payStatus == 0) 并且有 payUrl，则直接返回该链接，不需要重复创建
        if meter_info.get('payStatus') == 0 and meter_info.get('payUrl'):
            return jsonify({'success': True, 'url': meter_info.get('payUrl'), 'message': '检测到未完成的订单，已为您恢复跳转'})

        # 创建缴费订单
        pay_url = buaa_api.create_pay_order(api_id, amount, system_key)
        if pay_url:
            return jsonify({'success': True, 'url': pay_url})
        else:
            return jsonify({'success': False, 'error': '创建缴费订单失败'}), 502
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': '金额必须是数字'}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ========================
# 用户等级与设置 API
# ========================

@app.route('/api/tier', methods=['GET'])
def api_tier():
    """
    获取当前用户等级信息。
    """
    is_sponsor = database.is_sponsor()
    interval = scheduler.get_current_interval()
    max_meters = config.SPONSOR_MAX_METERS if is_sponsor else config.FREE_MAX_METERS
    meter_count = len(database.get_meters())
    lic = database.get_license_info()

    return jsonify({
        'success': True,
        'data': {
            'is_sponsor': is_sponsor,
            'poll_interval': interval,
            'max_meters': max_meters,
            'meter_count': meter_count,
            'min_poll': config.SPONSOR_MIN_POLL if is_sponsor else config.FREE_POLL_INTERVAL,
            'max_poll': config.SPONSOR_MAX_POLL if is_sponsor else config.FREE_POLL_INTERVAL,
            'expires_at': lic.get('expires_at'),
        }
    })


@app.route('/api/machine-id', methods=['GET'])
def api_machine_id():
    """
    返回本机机器码，供前端展示给用户（用户把它发给版权方以签发绑定该机的许可证）。
    """
    import license_manager
    code = license_manager.get_machine_id()
    return jsonify({
        'success': True,
        'data': {
            'machine_id': code,
            'display': license_manager.format_machine_id(code),
        }
    })


@app.route('/api/activate', methods=['POST'])
def api_activate():
    """
    激活赞助用户。

    请求体 JSON:
    {
        "code": "XRTJ.<payload>.<sig>"   # 许可证字符串（也接受 license 字段）
    }
    """
    data = request.get_json()
    license_str = (data or {}).get('code') or (data or {}).get('license')
    if not license_str:
        return jsonify({'success': False, 'error': '缺少许可证'}), 400

    result = database.activate_sponsor(license_str)
    status_code = 200 if result['success'] else 400
    return jsonify(result), status_code


@app.route('/api/settings', methods=['GET'])
def api_get_settings():
    """
    获取系统设置。
    """
    interval = scheduler.get_current_interval()
    is_sponsor = database.is_sponsor()

    return jsonify({
        'success': True,
        'data': {
            'poll_interval': interval,
            'is_sponsor': is_sponsor,
        }
    })


@app.route('/api/settings', methods=['PUT'])
def api_update_settings():
    """
    更新系统设置。

    请求体 JSON:
    {
        "poll_interval": 4  (小时，赞助用户 2-24，免费用户固定 12)
    }
    """
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': '请求体不能为空'}), 400

    try:
        if 'poll_interval' in data:
            hours = int(data['poll_interval'])
            scheduler.update_poll_interval(hours)

        return jsonify({'success': True, 'message': '设置已更新'})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': f'更新设置失败: {e}'}), 500


# ========================
# 手动轮询 API
# ========================

@app.route('/api/poll-now', methods=['POST'])
def api_poll_now():
    """
    手动触发一次全量轮询。
    在后台线程中执行，立即返回。
    """
    try:
        t = threading.Thread(target=scheduler.poll_all_meters, daemon=True)
        t.start()
        return jsonify({'success': True, 'message': '正在轮询，请稍后刷新查看结果'})
    except Exception as e:
        return jsonify({'success': False, 'error': f'触发轮询失败: {e}'}), 500


# ========================
# 系统托盘图标
# ========================

def _create_tray_icon():
    """
    创建 Windows 系统托盘图标。
    提供快捷菜单：打开面板、立即轮询、退出程序。
    """
    try:
        import pystray
        from PIL import Image, ImageDraw

        # 创建图标图像（简单的闪电符号⚡）
        size = 64
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # 绘制圆形背景
        draw.ellipse([2, 2, size-2, size-2], fill='#FFD700')

        # 绘制闪电符号
        lightning = [
            (32, 8),   # 顶部
            (20, 30),  # 左中
            (30, 30),  # 中
            (22, 56),  # 底部
            (44, 26),  # 右中
            (34, 26),  # 中
            (32, 8),   # 回到顶部
        ]
        draw.polygon(lightning, fill='#1a1a2e')

        def on_open_dashboard(icon, item):
            """打开浏览器面板"""
            webbrowser.open(f'http://localhost:{config.SERVER_PORT}')

        def on_poll_now(icon, item):
            """立即轮询"""
            t = threading.Thread(target=scheduler.poll_all_meters, daemon=True)
            t.start()

        def on_quit(icon, item):
            """退出程序"""
            icon.stop()
            os._exit(0)

        # 创建菜单
        menu = pystray.Menu(
            pystray.MenuItem('打开面板', on_open_dashboard, default=True),
            pystray.MenuItem('立即轮询', on_poll_now),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('退出', on_quit),
        )

        # 创建托盘图标
        icon = pystray.Icon(
            name='夏日告急',
            icon=img,
            title='夏日告急 - 空调电量监控',
            menu=menu
        )

        print("[托盘] 系统托盘图标已启动")
        icon.run()  # 这个会阻塞当前线程

    except ImportError:
        print("[托盘] pystray 不可用，系统托盘图标已禁用")
    except Exception as e:
        print(f"[托盘] 系统托盘图标启动失败: {e}")


# ========================
# 应用启动
# ========================

def main():
    """
    应用主入口，按顺序执行完整的启动流程。
    """
    print()
    print("=" * 50)
    print("    夏日告急 - 北航空调电量监控系统")
    print("=" * 50)
    print()

    # 1. 确保 data 目录存在
    os.makedirs('data', exist_ok=True)
    os.makedirs('static', exist_ok=True)
    print("[启动] data/ 目录已就绪")

    # 2. 初始化数据库
    database.init_db()

    # 3. 生成 VAPID 密钥（用于 Web Push 推送）
    vapid_keys = cert_manager.ensure_vapid_keys()

    # 4. 启动定时调度器
    scheduler.start_scheduler(app)

    # 5. 获取访问地址
    lan_ip = cert_manager.get_local_ip()
    port = config.SERVER_PORT
    local_url = f"http://localhost:{port}"
    lan_url = f"http://{lan_ip}:{port}"

    print()
    print("=" * 50)
    print("    服务已启动！访问地址：")
    print(f"  本机: {local_url}")
    print(f"  局域网: {lan_url}")
    print()
    print("    手机扫码访问:")
    print(f"  {lan_url}/api/qrcode")
    print()
    print("    推送通知请在本机浏览器 (localhost) 中开启")
    print("=" * 50)
    print()

    # 6. 启动系统托盘图标（后台线程）
    tray_thread = threading.Thread(target=_create_tray_icon, daemon=True)
    tray_thread.start()

    # 7. 自动打开浏览器
    def _open_browser():
        """延迟1.5秒后打开浏览器，确保服务已就绪"""
        import time
        time.sleep(1.5)
        webbrowser.open(local_url)

    browser_thread = threading.Thread(target=_open_browser, daemon=True)
    browser_thread.start()

    # 8. 启动 Flask HTTP 服务
    # 使用纯 HTTP：localhost 本身即安全上下文，Service Worker / 推送均可用，
    # 且无需任何证书，从根本上规避自签名证书的 SSL 错误。
    app.run(
        host=config.SERVER_HOST,
        port=config.SERVER_PORT,
        debug=False,
        use_reloader=False,  # 禁用自动重载（避免双重启动调度器）
        threaded=True
    )


if __name__ == '__main__':
    main()
