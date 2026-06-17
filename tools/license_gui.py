# -*- coding: utf-8 -*-
"""
夏日告急 — 许可证签发工具（图形版，仅供版权方本地运行，**不随 App 分发**）

输入买家机器码 → 点「签发并复制」→ 得到许可证字符串，自动复制回发给买家。
底层复用 tools/gen_license.py 的同一套签名逻辑，产出与命令行完全一致，
并写入同一个售出台账 secrets/issued_licenses.csv。

前置：先运行过 tools/gen_keys.py 生成 secrets/license_private_key.pem。
运行：在项目根目录执行  python tools/license_gui.py
     或双击 tools/签发许可证.bat（无黑框）
"""
import os
import sys
import time
import secrets as pysecrets
import tkinter as tk
from tkinter import ttk, messagebox

# 切到项目根目录，保证 secrets/ 等相对路径可用
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(_ROOT)
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, 'tools'))

# 主题色
ACCENT = '#2563eb'        # 主按钮蓝
ACCENT_DARK = '#1d4ed8'
OK_GREEN = '#0a7d4f'
MUTED = '#8a8f99'
BG = '#f5f6f8'
CARD = '#ffffff'

# 重依赖（cryptography 等）延迟到 main() 里导入，便于用对话框兜底报错
lm = None
gl = None


def _lazy_imports():
    """导入签名所需模块；失败时返回错误信息字符串，成功返回 None。"""
    global lm, gl
    try:
        import license_manager as _lm
        import gen_license as _gl
        lm, gl = _lm, _gl
        return None
    except ImportError as e:
        return ('缺少运行依赖：%s\n\n'
                '请在项目根目录执行：\n'
                '    .venv\\Scripts\\pip install cryptography' % e)
    except Exception as e:
        return '加载签名模块失败：%s' % e


def sign_license(machine_code, days, tier, floating, note):
    """返回 (license_str, payload, exp_str)。失败抛异常。"""
    mid = '*' if floating else gl._normalize_machine(machine_code)
    if mid != '*' and len(mid) != 16:
        raise ValueError(f'机器码应为 16 位十六进制，当前 {len(mid)} 位')

    iat = int(time.time())
    exp = (iat + days * 86400) if days and days > 0 else None

    payload = {
        'v': 1,
        'jti': pysecrets.token_hex(4),
        'tier': tier or 'sponsor',
        'mid': mid,
        'iat': iat,
        'exp': exp,
        'note': note or '',
    }

    priv = gl._load_private_key()
    signature = priv.sign(lm.canonical_payload(payload))
    license_str = lm.encode_license(payload, signature)
    gl._record_ledger(payload)

    exp_str = time.strftime('%Y-%m-%d', time.localtime(exp)) if exp else '永久'
    return license_str, payload, exp_str


class App:
    FONT = 'Microsoft YaHei UI'

    def __init__(self, root):
        self.root = root
        root.title('夏日告急 · 许可证签发')
        root.geometry('600x640')
        root.minsize(560, 600)
        root.configure(bg=BG)
        self._set_icon()
        self._setup_style()

        # ===== 顶部标题区 =====
        head = tk.Frame(root, bg=ACCENT)
        head.pack(fill='x')
        tk.Label(head, text='⚡  夏日告急 · 许可证签发', bg=ACCENT, fg='white',
                 font=(self.FONT, 15, 'bold')).pack(anchor='w', padx=20, pady=(14, 2))
        tk.Label(head, text='粘贴买家机器码 → 一键生成并复制许可证', bg=ACCENT,
                 fg='#dbeafe', font=(self.FONT, 9)).pack(anchor='w', padx=20, pady=(0, 14))

        body = tk.Frame(root, bg=BG)
        body.pack(fill='both', expand=True, padx=18, pady=14)

        # ===== 卡片①：签发参数 =====
        card1 = self._card(body, '① 签发参数')
        card1.pack(fill='x')
        grid = tk.Frame(card1, bg=CARD)
        grid.pack(fill='x', padx=16, pady=(4, 14))
        grid.columnconfigure(1, weight=1)

        # 机器码
        tk.Label(grid, text='买家机器码', bg=CARD, font=(self.FONT, 10, 'bold')).grid(
            row=0, column=0, sticky='w', pady=(8, 2))
        self.machine = ttk.Entry(grid, font=('Consolas', 11))
        self.machine.grid(row=1, column=0, columnspan=2, sticky='ew', ipady=3)
        tk.Label(grid, text='16 位十六进制，可带横线，如 A1B2-C3D4-E5F6-7890',
                 bg=CARD, fg=MUTED, font=(self.FONT, 8)).grid(
            row=2, column=0, columnspan=2, sticky='w', pady=(2, 10))

        # 有效期（预设 + 自定义）
        tk.Label(grid, text='有效期', bg=CARD, font=(self.FONT, 10, 'bold')).grid(
            row=3, column=0, sticky='w', pady=(0, 2))
        period = tk.Frame(grid, bg=CARD)
        period.grid(row=4, column=0, columnspan=2, sticky='w', pady=(0, 10))
        self.preset = tk.IntVar(value=0)   # 0=永久 365=一年 180=半年 -1=自定义
        for txt, val in [('永久', 0), ('一年', 365), ('半年', 180), ('自定义', -1)]:
            ttk.Radiobutton(period, text=txt, value=val, variable=self.preset,
                            command=self._toggle_custom).pack(side='left', padx=(0, 12))
        self.custom_days = ttk.Entry(period, width=6, font=(self.FONT, 10))
        self.custom_days.pack(side='left')
        tk.Label(period, text='天', bg=CARD, fg=MUTED, font=(self.FONT, 9)).pack(side='left', padx=(3, 0))
        self.custom_days.config(state='disabled')

        # 备注
        tk.Label(grid, text='备注（买家邮箱 / 订单号，仅记台账）', bg=CARD,
                 font=(self.FONT, 10, 'bold')).grid(row=5, column=0, columnspan=2, sticky='w', pady=(0, 2))
        self.note = ttk.Entry(grid, font=(self.FONT, 10))
        self.note.grid(row=6, column=0, columnspan=2, sticky='ew', ipady=3, pady=(0, 10))

        # 浮动 + 等级（次要选项）
        opt = tk.Frame(grid, bg=CARD)
        opt.grid(row=7, column=0, columnspan=2, sticky='w')
        self.floating = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt, text='浮动许可（不绑机器，仅自用测试，勿出售）',
                        variable=self.floating, command=self._toggle_floating).pack(side='left')
        tk.Label(opt, text='等级', bg=CARD, fg=MUTED, font=(self.FONT, 9)).pack(side='left', padx=(16, 4))
        self.tier = ttk.Entry(opt, width=12, font=(self.FONT, 9))
        self.tier.insert(0, 'sponsor')
        self.tier.pack(side='left')

        # ===== 操作按钮 =====
        btns = tk.Frame(body, bg=BG)
        btns.pack(fill='x', pady=12)
        self.btn_sign = tk.Button(btns, text='签发并复制', command=self.do_sign,
                                  bg=ACCENT, fg='white', activebackground=ACCENT_DARK,
                                  activeforeground='white', relief='flat', cursor='hand2',
                                  font=(self.FONT, 11, 'bold'), padx=22, pady=8, bd=0)
        self.btn_sign.pack(side='left')
        tk.Button(btns, text='清空', command=self.clear, relief='flat', cursor='hand2',
                  bg='#e5e7eb', activebackground='#d1d5db', font=(self.FONT, 10),
                  padx=16, pady=8, bd=0).pack(side='left', padx=10)

        # ===== 卡片②：结果 =====
        card2 = self._card(body, '② 许可证结果')
        card2.pack(fill='both', expand=True)
        inner = tk.Frame(card2, bg=CARD)
        inner.pack(fill='both', expand=True, padx=16, pady=(4, 14))

        self.summary = tk.Label(inner, text='填入机器码后点「签发并复制」', bg=CARD,
                                 fg=MUTED, font=(self.FONT, 9), anchor='w', justify='left')
        self.summary.pack(fill='x', pady=(4, 6))

        self.out = tk.Text(inner, height=4, wrap='char', font=('Consolas', 10),
                           bg='#f0f4ff', relief='flat', padx=10, pady=8,
                           highlightthickness=1, highlightbackground='#c7d2fe')
        self.out.pack(fill='both', expand=True)
        self.out.config(state='disabled')

        tk.Button(inner, text='复制许可证', command=self.copy, relief='flat', cursor='hand2',
                  bg='#e5e7eb', activebackground='#d1d5db', font=(self.FONT, 10),
                  padx=16, pady=6, bd=0).pack(anchor='w', pady=(10, 0))

        self.machine.focus_set()
        self.root.bind('<Return>', lambda e: self.do_sign())

    # ---------- 外观辅助 ----------
    def _set_icon(self):
        try:
            self.root.iconbitmap(os.path.join(_ROOT, 'static', 'icon.ico'))
        except Exception:
            pass

    def _setup_style(self):
        style = ttk.Style()
        try:
            style.theme_use('vista')
        except tk.TclError:
            pass
        style.configure('TRadiobutton', background=CARD, font=(self.FONT, 10))
        style.configure('TCheckbutton', background=CARD, font=(self.FONT, 10))

    def _card(self, parent, title):
        wrap = tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground='#e3e6ea')
        tk.Label(wrap, text=title, bg=CARD, fg='#374151',
                 font=(self.FONT, 11, 'bold')).pack(anchor='w', padx=16, pady=(12, 0))
        return wrap

    # ---------- 交互 ----------
    def _toggle_custom(self):
        self.custom_days.config(state='normal' if self.preset.get() == -1 else 'disabled')
        if self.preset.get() == -1:
            self.custom_days.focus_set()

    def _toggle_floating(self):
        self.machine.config(state='disabled' if self.floating.get() else 'normal')

    def _resolve_days(self):
        p = self.preset.get()
        if p == -1:
            txt = (self.custom_days.get() or '').strip()
            if not txt.isdigit():
                raise ValueError('自定义天数请填正整数')
            return int(txt)
        return p

    def _set_output(self, text):
        self.out.config(state='normal')
        self.out.delete('1.0', 'end')
        self.out.insert('1.0', text)
        self.out.config(state='disabled')

    def do_sign(self):
        try:
            days = self._resolve_days()
        except ValueError as e:
            messagebox.showwarning('输入有误', str(e))
            return
        try:
            license_str, payload, exp_str = sign_license(
                self.machine.get(), days, self.tier.get().strip(),
                self.floating.get(), self.note.get().strip())
        except FileNotFoundError:
            messagebox.showerror('缺少私钥',
                                 '找不到 secrets/license_private_key.pem\n\n'
                                 '请先运行：.venv\\Scripts\\python tools\\gen_keys.py')
            return
        except Exception as e:
            messagebox.showerror('签发失败', str(e))
            return

        self.summary.config(
            fg=OK_GREEN,
            text='✓ 已签发并复制   jti=%s   绑定=%s   到期=%s'
                 % (payload['jti'], payload['mid'], exp_str))
        self._set_output(license_str)
        self._copy_text(license_str)

    def _copy_text(self, s):
        self.root.clipboard_clear()
        self.root.clipboard_append(s)

    def copy(self):
        s = self.out.get('1.0', 'end').strip()
        if not s:
            return
        self._copy_text(s)
        self.summary.config(fg=OK_GREEN, text='✓ 许可证已复制到剪贴板')

    def clear(self):
        self.floating.set(False)
        self.machine.config(state='normal')
        self.machine.delete(0, 'end')
        self.note.delete(0, 'end')
        self.preset.set(0)
        self.custom_days.delete(0, 'end')
        self.custom_days.config(state='disabled')
        self._set_output('')
        self.summary.config(fg=MUTED, text='填入机器码后点「签发并复制」')
        self.machine.focus_set()


def main():
    root = tk.Tk()
    err = _lazy_imports()
    if err:
        root.withdraw()
        messagebox.showerror('夏日告急 · 启动失败', err)
        root.destroy()
        return
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
