# -*- mode: python ; coding: utf-8 -*-
"""
夏日告急 — PyInstaller 打包配置（单文件 / 窗口化）

安全要点：
- 只打包只读前端资源 static/；
- **绝不**打包 secrets/（签名私钥）、data/（数据库/密钥）、tools/（许可证签发工具）——
  它们都不会被 app.py import，PyInstaller 不会收集；下方也未将其加入 datas。
- 闭源安全不依赖隐藏字节码：授权用 Ed25519 非对称签名，私钥从不进包，
  即便有人解包 exe 拿到公钥与逻辑，也无法伪造许可证。

构建：  pyinstaller xrtj.spec      （或直接双击 build.bat）
产物：  dist/夏日告急.exe
"""
from PyInstaller.utils.hooks import collect_submodules, collect_all

# --- 隐藏导入：函数内/动态导入的包，静态分析可能漏掉 ---
hiddenimports = []
hiddenimports += collect_submodules('apscheduler')   # 调度器用动态导入
hiddenimports += collect_submodules('pywebpush')
hiddenimports += [
    'py_vapid',
    'pystray._win32',          # Windows 托盘后端
    'PIL.Image', 'PIL.ImageDraw',
    # 本项目自身模块（确保都被收进来）
    'config', 'database', 'license_manager', 'buaa_api',
    'scheduler', 'notifier', 'cert_manager',
]

datas = []
binaries = []

# win11toast 依赖 winsdk，但 winsdk 全量打包会使包体积膨胀数十MB。
# 这里仅收集 win11toast 及通知相关的 winsdk 子模块
try:
    d, b, h = collect_all('win11toast')
    datas += d
    binaries += b
    hiddenimports += h
except Exception:
    pass

# 手动添加 win11toast 运行所需的 winsdk 模块
hiddenimports += [
    'winsdk.windows.ui.notifications',
    'winsdk.windows.data.xml.dom',
    'winsdk.windows.foundation',
    'winsdk.windows.foundation.collections',
]

# 只读前端资源（运行时解压到 sys._MEIPASS/static）
datas += [('static', 'static')]


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'pytest', 'unittest', 'pydoc', 'xmlrpc', 'IPython', 'pandas', 'numpy', 'matplotlib', 'scipy', 'PyQt5', 'PySide6', 'setuptools', 'distutils', 'pkg_resources', 'pydoc_data', 'lib2to3', 'curses', 'idlelib', 'sqlite3.test', 'test', 'PySide2', 'wx', 'IPython', 'tornado', 'pygments', 'docutils', 'jedi', 'prompt_toolkit', 'notebook', 'nbconvert', 'jinja2', 'markupsafe', 'babel'],  # 用不到，减小体积
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='夏日告急',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                     # 窗口化：有托盘图标+自动开浏览器，无需黑窗
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='static/icon.ico',            # 应用图标（由 tools/make_icon.py 生成）
)
