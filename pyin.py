#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pyin —— 「更有趣（存疑）的输入方式」桌面原型（macOS · 微信 · Python）

流程（想法原述与可行性调研见作者工作区文档《想法记录》《调研笔记》，未随仓库分发；方案 = 调研笔记 §1.1 档位 A · MVP）：
  1. 呼出置顶悬浮编辑器（全局热键 ⌘⌥E，或点击窗口/Dock）
  2. 编辑器里输入 Python：Enter = 换行（键位为作者 2026-11-17 指定）
  3. Shift+Enter = 运行（子进程 `python3 -`，捕获 stdout/stderr，超时强制终止）
  4. 运行结果（正常输出或报错均算"结果"，见想法原述）**不加任何头原样直发**
     （作者 2026-11-17 指定；无输出则跳过发送）写剪贴板 → 激活微信 →
     核实微信确为前台（否则中止，防止发错应用）→ Cmd+V 粘贴 → Enter 发送

依赖：Python 3 标准库（tkinter）+ macOS 自带 pbcopy/pbpaste/osascript 即可运行；
     可选增强：pyobjc-framework-Quartz（提供全局热键与 CGEvent 注入后端），
     缺失时自动降级（无热键、注入走 osascript），功能不受影响。

权限：真实注入按键需要「辅助功能」授权（osascript keystroke 与 CGEventPost 同源要求），
     系统设置 → 隐私与安全性 → 辅助功能 → 给运行本脚本的宿主 App（终端/iTerm/VSCode…）授权。
     授权按宿主 App 归因：换一个终端启动就要重新授权。未授权时旧版会被系统"静默丢弃"
     造成假成功——现版本未授权即显式失败并取消；`./run.sh --doctor` 可只读体检；
     未授权时也可用 --dry-run 完整体验运行+格式化流程（注入改为打印）。
     注入链每一步均落 code/pyin.log（.gitignore 已忽略），排查以它为准。

出处标注（AGENTS.md 引用要求）：
  - 悬浮编辑器 + 显式触发运行 + 剪贴板注入 + 回车发送：方案出自作者工作区《调研笔记》§1.1/§4，
    注入先例为 Espanso（espanso.org）。
  - CGEventTap / CGEventPost / 键位常量（kVK_ANSI_V=9、kVK_Return=36、kVK_ANSI_E=14）：
    Apple 官方 API（非工作区来源）。
  - 微信 macOS「Enter 发送消息（默认）」「粘贴进入输入框」「激活后焦点回到输入框」
    为产品行为假设，工作区内无核验依据，未逐版本验证（见 code/README.md 已知限制）。
"""

import argparse
import os
import queue
import signal
import subprocess
import sys
import threading
import time

# ---- 可调常量（详见 code/README.md 常量表） ---------------------------------
RUN_TIMEOUT = 5.0            # 单次代码运行上限（秒），超时杀进程并照常把已有输出发出去
FOCUS_DELAY = 0.5            # 激活微信后等待窗口/输入框获焦的时延
PASTE_DELAY = 0.5            # 粘贴→回车时延：与探针 A/B（实测可发送）完全一致
SEND_KEY_DEFAULT = "enter"   # 发送键：enter / cmd_enter / ctrl_enter（见 send_key_spec）
CLIPBOARD_RESTORE_DELAY = 1.5  # 发送后恢复原剪贴板内容的时延
MAX_RESULT_CHARS = 2000      # 发送文本上限（超出则中间截断）
HOTKEY_KEYCODE = 14          # kVK_ANSI_E（非工作区来源：Apple HIToolbox 常量）
HOTKEY_LABEL = "⌘⌥E"
WECHAT_BUNDLE_IDS = ("com.tencent.xinWeChat", "com.tencent.wechat")
WECHAT_NAMES = ("微信", "WeChat", "Weixin")

# ---- 可选后端：pyobjc（全局热键 + CGEvent 注入） -----------------------------
try:
    import Quartz  # type: ignore
    HAVE_QUARTZ = True
except ImportError:
    Quartz = None
    HAVE_QUARTZ = False


LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pyin.log")


def log(msg):
    """同时写 stdout 与 code/pyin.log（.gitignore 已忽略）——GUI 模式下控制台
    往往看不到，这份日志是排查注入链断在哪一环的黑匣子。"""
    line = f"[pyin {time.strftime('%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# =============================================================================
# 执行层：跑代码 + 把结果格式化成"要发进聊天的那条消息"
# =============================================================================

def run_code(code, timeout=RUN_TIMEOUT, python=None):
    """子进程执行 Python 代码。stdout/stderr/超时一律捕获，不抛出。

    超时处理：SIGKILL 整个进程组（防止子代码再 spawn 的孙进程占住管道），
    再二次 communicate() 排空管道——POSIX 上 TimeoutExpired.stdout 为 None，
    不排空会丢掉超时前已产生的输出。
    解释器 = 启动 pyin 的那个 Python（sys.executable），因此安装了哪些第三方库
    取决于用哪个 Python 启动 pyin（见 code/README.md 已知限制）。
    """
    # -u（无缓冲）是超时链路的必要条件：stdout 接管道时为块缓冲，不加 -u 的话
    # print 的内容滞留在子进程缓冲区，SIGKILL 时随缓冲区一起丢失。
    proc = subprocess.Popen([python or sys.executable, "-u", "-"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, encoding="utf-8",
                            errors="replace", start_new_session=True)
    try:
        out, err = proc.communicate(input=code, timeout=timeout)
        timed_out, rc = False, proc.returncode
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        out, err = proc.communicate()      # 排空管道，保留超时前的输出
        timed_out, rc = True, None
    return {"timed_out": timed_out, "ok": (not timed_out) and rc == 0,
            "rc": rc, "out": out or "", "err": err or "", "timeout": timeout}


def format_result(res):
    """把运行结果整理为将发送的文本：**不加任何头，输出原样直发**（作者 2026-11-17 指定）。
    成功→stdout（有 stderr 则换行续接）；报错→stderr（traceback 即输出）；超时→已捕获输出。
    返回空串表示无输出（调用方跳过发送）。报错也照常发送（想法原述明确要求）。"""
    if res["timed_out"]:
        text = ((res["out"] or "") + (res["err"] or "")).strip("\n")
    elif res["ok"]:
        text = res["out"].rstrip("\n")
        if res["err"].strip():
            err = res["err"].rstrip()
            text = f"{text}\n{err}" if text else err
    else:
        text = (res["err"] or res["out"] or "").rstrip()
    if len(text) > MAX_RESULT_CHARS:
        keep = MAX_RESULT_CHARS // 2 - 20
        text = text[:keep] + "\n…（过长，中间内容已截断）…\n" + text[-keep:]
    return text


def has_effective_code(code):
    """只有注释/空行 → 视为无代码，不运行也不发送（防止误触）。"""
    for line in code.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            return True
    return False


# =============================================================================
# 注入层（调研笔记.md §1.1 档位 A：结果回注链——激活 → 粘贴 → 回车）
# =============================================================================

def set_clipboard(text):
    p = subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False)
    return p.returncode == 0


def get_clipboard():
    return subprocess.run(["pbpaste"], capture_output=True).stdout.decode("utf-8", "replace")


# 激活微信：优先按 bundle id（仅在"已在运行"时激活，避免顺手拉起微信），
# 都没有再按进程名找。找到后核实微信确为前台，否则中止注入——防止把结果发进别的应用。
ACTIVATE_SCRIPT = '''
on run
	repeat with bid in {"com.tencent.xinWeChat", "com.tencent.wechat"}
		try
			if application id (bid as text) is running then
				tell application id (bid as text) to activate
				return "ok:" & (bid as text)
			end if
		end try
	end repeat
	tell application "System Events"
		repeat with n in {"微信", "WeChat", "Weixin"}
			try
				set frontmost of (first application process whose name is (n as text)) to true
				return "ok:proc:" & (n as text)
			end try
		end repeat
	end tell
	return "notfound"
end run
'''
FRONTMOST_SCRIPT = ('tell application "System Events" to get name of '
                    'first application process whose frontmost is true')
WECHAT_RUNNING_SCRIPT = '''
on run
	repeat with bid in {"com.tencent.xinWeChat", "com.tencent.wechat"}
		try
			if application id (bid as text) is running then return "running:" & (bid as text)
		end try
	end repeat
	return "notrunning"
end run
'''


def send_key_spec(name):
    """发送键规格 → (keycode, CGEvent flags, osascript 修饰串)。

    微信 Mac 的「按 Enter 键发送消息」关闭时，Enter 只换行不发送（2026-11-17
    实机反馈：粘贴成功但 Enter 未发送）——此时改用 cmd_enter / ctrl_enter。
    36 = kVK_Return（非工作区来源：Apple HIToolbox 常量）。"""
    osa = {"enter": "", "cmd_enter": " using {command down}",
           "ctrl_enter": " using {control down}"}[name]
    flags = 0
    if HAVE_QUARTZ and name == "cmd_enter":
        flags = Quartz.kCGEventFlagMaskCommand
    elif HAVE_QUARTZ and name == "ctrl_enter":
        flags = Quartz.kCGEventFlagMaskControl
    return 36, flags, osa


class Injector:
    """把文本"输入"当前焦点应用：写剪贴板 → 激活微信并核实前台 → Cmd+V → Enter。

    后端：有 pyobjc 用 CGEventPost（键位常量为 Apple HIToolbox 定义，非工作区来源）；
    否则 osascript（System Events keystroke / key code）。两者都需要辅助功能授权。
    """

    def __init__(self, dry_run=False, send_key=SEND_KEY_DEFAULT, send_engine="cgevent"):
        self.dry = dry_run
        self.send_key = send_key
        self.send_engine = send_engine
        self.backend = "CGEvent (pyobjc)" if HAVE_QUARTZ else "osascript (System Events)"
        # 显式私有事件源（kCGEventSourceStatePrivate）：CGEventCreateKeyboardEvent
        # 会继承事件源当时的修饰键状态（Apple 文档行为，非工作区来源）——探针路径
        # 双手离键、事件干净所以能发送；编辑器路径用户刚按完 Shift+Enter，合成
        # Return 曾可能带 Shift 残留 → 被微信按惯例解释为换行。私有源事件只携带
        # 我们显式设置的 flags，物理键盘状态无法渗入（2026-11-17 第六轮联调）。
        self._src = (Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)
                     if HAVE_QUARTZ else None)

    # -- 按键 -----------------------------------------------------------------
    def _key(self, keycode, flags=0):
        for down in (True, False):
            ev = Quartz.CGEventCreateKeyboardEvent(self._src, keycode, down)
            # flags 必须无条件显式设置（含 0）：不设置则事件继承源状态的修饰键残留
            Quartz.CGEventSetFlags(ev, flags)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
            time.sleep(0.015)

    def _osa(self, script):
        p = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if p.returncode != 0:
            err = (p.stderr or "").strip().replace("\n", " ")[:160] or "未知错误"
            raise RuntimeError(f"osascript 注入失败：{err}（多为辅助功能未授权）")

    def paste(self):
        if HAVE_QUARTZ:
            self._key(9, Quartz.kCGEventFlagMaskCommand)   # kVK_ANSI_V
        else:
            self._osa('tell application "System Events" to keystroke "v" using command down')

    def enter(self):
        keycode, flags, osa_mods = send_key_spec(self.send_key)
        if HAVE_QUARTZ and self.send_engine == "cgevent":
            self._key(keycode, flags)
        else:
            # osascript 引擎：另一条合成路径（部分应用对 CGEvent 合成 Return 不响应时试它）
            self._osa(f'tell application "System Events" to key code {keycode}{osa_mods}')

    def click_at(self, px, py):
        """在全局屏幕坐标 (px, py)（点）合成左键单击——保证目标输入框成为第一响应者。
        事件源与按键注入一致（私有 HID 源）。原位覆盖模式（档位 B）注入链使用。"""
        if HAVE_QUARTZ:
            move = Quartz.CGEventCreateMouseEvent(self._src, Quartz.kCGEventMouseMoved,
                                                  (px, py), Quartz.kCGMouseButtonLeft)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, move)
            time.sleep(0.05)
            for etype in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
                ev = Quartz.CGEventCreateMouseEvent(self._src, etype, (px, py),
                                                    Quartz.kCGMouseButtonLeft)
                Quartz.CGEventSetFlags(ev, 0)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
                time.sleep(0.03)
        else:
            self._osa(f'tell application "System Events" to click at {{{int(px)}, {int(py)}}}')

    # -- 激活与核实 -------------------------------------------------------------
    def activate_wechat(self):
        p = subprocess.run(["osascript", "-e", ACTIVATE_SCRIPT],
                           capture_output=True, text=True, timeout=5)
        if p.returncode != 0:
            return False, f"激活微信失败：{(p.stderr or '').strip()[:120]}"
        if (p.stdout or "").strip() == "notfound":
            return False, "未找到正在运行的微信（先打开微信并停留在某个聊天）"
        # 前台核实（尽力而为）：前台不是微信就中止，宁可不发也不发错地方
        try:
            q = subprocess.run(["osascript", "-e", FRONTMOST_SCRIPT],
                               capture_output=True, text=True, timeout=3)
            name = (q.stdout or "").strip()
            if q.returncode == 0 and name and \
                    not any(w.lower() in name.lower() for w in WECHAT_NAMES):
                return False, f"微信未成为前台（前台是 {name}），已中止注入"
        except Exception:
            pass
        return True, ""

    # -- 主链 -------------------------------------------------------------------
    def send_result(self, text, click_rect=None):
        if self.dry:
            log("—— dry-run：以下内容将经「激活微信 → Cmd+V → Enter」注入发送 ——")
            print(text, flush=True)
            return True, "dry-run 完成（未真实注入）"
        # 未授权即显式失败：CGEventPost 在未授权时被系统**静默丢弃**、osascript
        # keystroke 被拒绝——宁可明确报错，绝不制造"状态栏成功、实际没发"的假象
        if accessibility_trusted() is False:
            log("注入取消：辅助功能未授权（CGEventPost/osascript 会被系统丢弃或拒绝）")
            return False, ("辅助功能未授权——注入按键会被系统丢弃，已取消发送。"
                           "系统设置→隐私与安全性→辅助功能，给运行 pyin 的宿主 App 授权"
                           "（授权按宿主 App 归因；可用 ./run.sh --doctor 复核）")
        old = None
        try:
            old = get_clipboard()
        except Exception:
            pass
        if not set_clipboard(text):
            return False, "写剪贴板失败（pbcopy）"
        if get_clipboard() != text:
            return False, "剪贴板写入校验失败（pbcopy/pbpaste 回读不一致）"
        ok, err = self.activate_wechat()
        if not ok:
            return False, err
        time.sleep(FOCUS_DELAY)
        try:
            if click_rect:
                # 原位覆盖模式：先点一下真实输入框中心，确保它是第一响应者，
                # 粘贴与回车才不会落空（覆盖框此刻已隐藏）
                self.click_at(click_rect[0] + click_rect[2] / 2,
                              click_rect[1] + click_rect[3] / 2)
                time.sleep(0.25)
            self.paste()
            time.sleep(PASTE_DELAY)
            self.enter()
        except RuntimeError as e:
            return False, str(e)
        log(f"注入链已执行：backend={self.backend}，{len(text)} 字符，前台已核实为微信")
        if old is not None:
            threading.Thread(target=self._restore_clipboard, args=(old,),
                             daemon=True).start()
        return True, "已粘贴并回车发送"

    def _restore_clipboard(self, old):
        time.sleep(CLIPBOARD_RESTORE_DELAY)
        try:
            set_clipboard(old)
        except Exception:
            pass


# =============================================================================
# 全局热键（可选，需 pyobjc + 辅助功能权限；失败只禁用热键，不影响其余功能）
# =============================================================================

def start_hotkey(on_hotkey, on_log, desc=None):
    """⌘⌥E 全局热键（ListenOnly tap，不吞键）。回调只做 queue.put（线程安全）。"""
    if not HAVE_QUARTZ:
        on_log("未安装 pyobjc Quartz：全局热键不可用（用 Dock/点击窗口代替）")
        return False

    def _worker():
        try:
            def cb(proxy, etype, event, refcon):
                try:
                    if etype == Quartz.kCGEventKeyDown and \
                            Quartz.CGEventGetIntegerValueField(
                                event, Quartz.kCGKeyboardEventKeycode) == HOTKEY_KEYCODE:
                        want = Quartz.kCGEventFlagMaskCommand | Quartz.kCGEventFlagMaskAlternate
                        if Quartz.CGEventGetFlags(event) & want == want:
                            on_hotkey()
                except Exception:
                    pass
                return event

            tap = Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                Quartz.kCGEventTapOptionListenOnly,
                Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown), cb, None)
            if tap is None:
                on_log("全局热键创建失败（辅助功能未授权时 CGEventTapCreate 返回 None）"
                       "——热键禁用，其余功能不受影响")
                return
            src = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
            Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), src,
                                      Quartz.kCFRunLoopDefaultMode)
            Quartz.CGEventTapEnable(tap, True)
            on_log(f"全局热键已启用：{HOTKEY_LABEL} {desc or '呼出/隐藏编辑器'}")
            Quartz.CFRunLoopRun()
        except Exception as e:
            on_log(f"热键线程异常退出：{e!r}（其余功能不受影响）")

    threading.Thread(target=_worker, daemon=True).start()
    return True


def accessibility_trusted():
    """尽力探测辅助功能授权状态（AXIsProcessTrusted）。True/False；None = 无法判定。
    ctypes 直调 ApplicationServices 框架——不依赖 pyobjc（非工作区来源：Apple 官方 API）。"""
    try:
        import ctypes
        fs = ctypes.CDLL("/System/Library/Frameworks/"
                         "ApplicationServices.framework/ApplicationServices")
        fs.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(fs.AXIsProcessTrusted())
    except Exception:
        return None


# =============================================================================
# GUI：置顶悬浮编辑器（tkinter，标准库）
# =============================================================================

PREFILL = """\
# pyin 原型：在这里写 Python，Shift+Enter 运行并把输出发进微信
# Enter 换行；Esc 收起窗口；只含注释/空行时不会发送
# 试试直接按 Shift+Enter：
print("hello from pyin", 1 + 1)
"""


class Editor:
    def __init__(self, dry_run=False, send_key=SEND_KEY_DEFAULT, send_engine="cgevent"):
        import tkinter as tk
        self._tk = tk
        self.injector = Injector(dry_run, send_key=send_key, send_engine=send_engine)
        self.q = queue.Queue()
        self.busy = False
        self.hidden = False

        self.root = tk.Tk()
        self.root.title("pyin · 代码 → 微信（原型）")
        self.root.attributes("-topmost", True)   # 置顶悬浮（档位 A 的"近似原位"形态）
        self.root.geometry("+120+120")

        top = tk.Frame(self.root)
        top.pack(fill="both", expand=True, padx=8, pady=(8, 2))
        # 黑底浅字（作者 2026-11-17 指定）：fg/insertbackground 必须显式给定——
        # macOS 深色模式下 Tk 默认文字色取系统浅色，配浅底会白底白字不可见
        self.text = tk.Text(top, width=64, height=14, undo=True, wrap="word",
                            font=("Menlo", 13), bg="#101010", fg="#e8e8e8",
                            insertbackground="#e8e8e8",
                            selectbackground="#2f4a78", selectforeground="#ffffff")
        scroll = tk.Scrollbar(top, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.text.insert("1.0", PREFILL)

        # 键位（作者指定）：Enter=换行（Text 默认行为，不改）；
        # Shift+Enter=运行（类绑定不响应 Shift-Return，此处显式绑定并 break）。
        self.text.bind("<Shift-Return>", self._on_run)
        self.text.bind("<Shift-KP_Enter>", self._on_run)   # 小键盘 Enter 兜底
        self.text.bind("<Escape>", self._on_escape)
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)

        bottom = tk.Frame(self.root)
        bottom.pack(fill="x", padx=8)
        self.hint = tk.Label(bottom, text="", anchor="w", fg="#8a8a8a")
        self.hint.pack(side="left", fill="x", expand=True)

        self.status = tk.Label(self.root, text="", anchor="w", fg="#8a8a8a")
        self.status.pack(fill="x", padx=8, pady=(0, 8))

        hk = " / ".join([HOTKEY_LABEL, "Dock"]) if HAVE_QUARTZ else "点击窗口 / Dock"
        self.hint.configure(
            text=f"{hk} 呼出 · Enter 换行 · Shift+Enter 运行并发送 · Esc 收起")

    # -- 状态栏 ---------------------------------------------------------------
    def _set_status(self, text, color):
        # 配色按深色窗口底调亮（绿色/红色在纯黑上过暗则不可读）
        colors = {"green": "#4caf50", "red": "#ef5350", "gray": "#9e9e9e"}
        self.status.configure(text=text, fg=colors.get(color, "#9e9e9e"))

    # -- 显隐 -----------------------------------------------------------------
    def _set_hidden(self, hidden):
        if hidden:
            self.root.withdraw()
        else:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
            self.text.focus_set()
        self.hidden = hidden

    def _reshow(self):
        """发送完成后重开输入窗口（仅在窗口确被收起时；窗口已销毁则静默退出）。"""
        try:
            if self.root.state() == "withdrawn":
                self._set_hidden(False)
        except self._tk.TclError:
            pass

    def _on_escape(self, _ev=None):
        # 有热键时放心收起（热键可召回）；无热键时最小化（Dock 可还原）
        if HAVE_QUARTZ:
            self._set_hidden(True)
        else:
            self.root.iconify()
        return "break"

    # -- 运行链 ---------------------------------------------------------------
    def _on_run(self, _ev=None):
        if self.busy:
            return "break"
        code = self.text.get("1.0", "end").rstrip("\n")
        if not has_effective_code(code):
            self._set_status("只有注释/空行——没有可运行的代码，未发送", "gray")
            return "break"
        self.busy = True
        log(f"Shift+Enter 触发：{len(code.splitlines())} 行（dry={self.injector.dry}）")
        self._set_status("运行中…", "gray")
        threading.Thread(target=self._worker, args=(code,), daemon=True).start()
        return "break"

    def _worker(self, code):
        res = run_code(code)
        out = format_result(res)
        if not out:
            # 空输出：不注入也不回车——空消息发不出去，且回车会把输入框里
            # 可能残留的草稿误发出去（发出的消息保持纯输出，见 format_result）
            self.q.put(("done", (None, "运行完成，无输出——未发送", 0)))
            return
        # 注入前必收起（不再提供选项）：实测编辑器停留前台时激活/注入不可靠，
        # 收起后走的是探针验证过的可靠路径（2026-11-17 第四轮实机反馈）
        self.q.put(("hide", None))
        time.sleep(0.2)                # 给主线程一拍完成 withdraw，再激活微信
        ok, msg = self.injector.send_result(out)
        if res["timed_out"]:
            msg += "（超时强制终止）"   # 仅状态栏提示，不进发出的消息
        self.q.put(("done", (ok, msg, len(out))))
        self.q.put(("reshow", ok))     # 作者指定：发送完成 1s 后重开输入窗口

    # -- 队列轮询（Tk 只在主线程被触碰） ---------------------------------------
    def _poll(self):
        try:
            try:
                while True:
                    kind, payload = self.q.get_nowait()
                    if kind == "hide":
                        self._set_hidden(True)
                    elif kind == "toggle":
                        self._set_hidden(not self.hidden)
                        if not self.hidden:
                            self._set_status("", "gray")
                    elif kind == "done":
                        ok, msg, n = payload
                        self.busy = False
                        if ok is True:
                            self._set_status(f"✅ {msg}（{n} 字符）", "green")
                        elif ok is False:
                            self._set_status(f"❌ {msg}", "red")
                        else:
                            self._set_status(msg, "gray")
                    elif kind == "reshow":
                        # 成功 1s 后重开（作者指定）；失败尽快重开以便看到红色错误
                        self.root.after(1000 if payload else 400, self._reshow)
            except queue.Empty:
                pass
        except self._tk.TclError:
            return                     # 窗口已销毁，停止轮询
        self.root.after(80, self._poll)

    def run(self):
        log("pyin 悬浮代码编辑器启动……")
        log(f"注入后端：{self.injector.backend}"
            + ("（dry-run，不真实注入）" if self.injector.dry else ""))
        log(f"发送键：{self.injector.send_key}（引擎 {self.injector.send_engine}）")
        trusted = accessibility_trusted()
        log("辅助功能授权：" + ("已授权" if trusted else
                               "未授权（注入将被系统静默丢弃）" if trusted is False
                               else "无法判定"))
        if trusted is False:
            self._set_status("⚠️ 辅助功能未授权——注入会被系统丢弃，见 --doctor", "red")
            self.hint.configure(text="⚠️ 未授权，注入将被丢弃｜" + self.hint.cget("text"))
        if not os.path.exists("/Applications/WeChat.app"):
            log("提示：/Applications 下未找到 WeChat.app——微信装在别处或未安装时，"
                "激活步骤将失败。")
        start_hotkey(lambda: self.q.put(("toggle", None)), log)
        self.root.after(80, self._poll)
        self.root.mainloop()


# =============================================================================
# 入口
# =============================================================================

def selftest():
    """离线自测：执行层+格式化（无需 GUI/授权/微信）。"""
    fails = []

    def check(name, cond, detail=""):
        print(f"  {'PASS' if cond else 'FAIL'}  {name}"
              + (f"  [{detail}]" if detail and not cond else ""))
        if not cond:
            fails.append(name)

    r = run_code("print('hello', 1+1)")
    check("正常运行捕获 stdout", r["ok"] and "hello 2" in r["out"])
    check("格式：纯输出无头", format_result(r) == "hello 2")

    r = run_code("1/0")
    f = format_result(r)
    check("报错捕获 traceback", (not r["ok"]) and "ZeroDivisionError" in r["err"])
    check("格式：报错直发（traceback 原样）", f == r["err"].rstrip())

    r = run_code("while True: pass", timeout=1.0)
    check("超时强制终止", r["timed_out"])
    check("格式：无输出超时→空（跳过发送）", format_result(r) == "")

    r = run_code("print('partial')\nimport time\ntime.sleep(9)", timeout=1.0)
    check("格式：超时直发已捕获输出", format_result(r) == "partial")

    check("无输出→空串", format_result(run_code("x = 1")) == "")

    r = run_code("import sys; sys.stderr.write('warn!'); print('ok')")
    check("成功+stderr 换行续接", format_result(r) == "ok\nwarn!")

    big = "\n".join(f"print({i})" for i in range(2000))
    f = format_result(run_code(big))
    check("超长截断", len(f) <= MAX_RESULT_CHARS + 80 and "截断" in f)

    check("注释-only 判定", not has_effective_code("# a\n\n  # b\n"))
    check("有效代码判定", has_effective_code("# c\nprint(1)"))

    check("中文/emoji 透传", "你好 🌟" in run_code("print('你好 🌟')")["out"])

    print(f"\n[pyin] selftest：{'全部通过' if not fails else f'{len(fails)} 项失败：{fails}'}")
    return 0 if not fails else 1


def doctor():
    """只读体检（不注入任何按键）：逐项检查注入链的前提条件是否成立。
    用途：授权按"宿主 App"归因——从不同终端启动结果不同；本命令直接告诉你
    当前这次启动到底能不能注入。"""
    print(f"[pyin] 解释器：{sys.executable}（{sys.version.split()[0]}）")
    try:
        import tkinter
        print(f"[pyin] tkinter：OK（Tk {tkinter.TkVersion}）")
    except ImportError:
        print("[pyin] tkinter：✗ 缺失")
    print(f"[pyin] pyobjc Quartz：{'✓ 可用' if HAVE_QUARTZ else '✗ 缺失（降级 osascript 后端，且无全局热键）'}")
    trusted = accessibility_trusted()
    if trusted is True:
        print("[pyin] 辅助功能授权：✓ 已授权（对当前宿主 App 生效）")
    elif trusted is False:
        print("[pyin] 辅助功能授权：✗ 未授权——注入按键会被系统静默丢弃/拒绝！"
              "去 系统设置→隐私与安全性→辅助功能 给『运行 pyin 的那个 App』开开关；"
              "注意授权按宿主 App 归因，换个终端启动就要重新授权")
    else:
        print("[pyin] 辅助功能授权：? 无法判定")
    if HAVE_QUARTZ:
        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown), lambda *a: None, None)
        print(f"[pyin] CGEventTap 试建：{'✓ 成功（CGEvent 注入可用）' if tap else '✗ 失败（宿主未授权）'}")
    try:
        p = subprocess.run(["osascript", "-e", WECHAT_RUNNING_SCRIPT],
                           capture_output=True, text=True, timeout=5)
        out = (p.stdout or "").strip()
        if out.startswith("running:"):
            print(f"[pyin] 微信：✓ 运行中（{out.split(':', 1)[1]}）")
        else:
            print("[pyin] 微信：✗ 未在运行——先打开微信并停在目标聊天")
    except Exception as e:
        print(f"[pyin] 微信：? 查询失败（{e!r}）")
    try:
        q = subprocess.run(["osascript", "-e", FRONTMOST_SCRIPT],
                           capture_output=True, text=True, timeout=3)
        print(f"[pyin] 当前前台 App：{(q.stdout or '').strip() or '未知'}")
    except Exception:
        pass
    print("[pyin] 提示：真实注入的前置链 = 授权 ✓ + 微信运行 ✓ + 微信前台 ✓ + "
          "微信开启『按 Enter 发送消息』。修复后重跑本命令复核。")
    return 0


def probe_send():
    """发送策略探针：向微信当前聊天依次注入 4 条带标记的探针，定位哪种方式能触发发送。

    真实注入！须在「与启动 pyin 相同的宿主」下运行（授权按宿主归因），且微信停在测试聊天。
    探针 A=CGEvent Return（当前默认）；B=osascript key code 36（另一合成路径）；
    C=CGEvent ⌘+Return；D=只粘贴不按键（基线，应留在输入框）。"""
    print("== pyin 发送探针 ==")
    print("即将向微信当前聊天注入 4 条 [pyin 探针 A/B/C/D]（真实发送！请务必用测试聊天）")
    if accessibility_trusted() is False:
        print("✗ 辅助功能未授权——本宿主无法注入。请在运行 pyin 能粘贴成功的同一宿主下运行。")
        return 1
    inj = Injector()
    ok, err = inj.activate_wechat()
    if not ok:
        print(f"✗ {err}")
        return 1
    time.sleep(FOCUS_DELAY)
    steps = [
        ("A", "CGEvent Return（当前默认）", lambda: inj.enter()),
        ("B", "osascript key code 36", lambda: Injector(send_engine="osascript").enter()),
        ("C", "CGEvent ⌘+Return", lambda: inj._key(*send_key_spec("cmd_enter")[:2])),
        ("D", "只粘贴不按键（基线，应留在输入框）", None),
    ]
    for tag, desc, action in steps:
        set_clipboard(f"[pyin 探针 {tag}] {desc}")
        time.sleep(0.1)
        inj.paste()
        time.sleep(0.5)
        if action:
            action()
        time.sleep(0.9)
        log(f"探针 {tag} 已注入：{desc}")
    print("\n完成。请把两件事告诉 ZCode：")
    print("  1) 聊天记录里出现了哪些 [pyin 探针 X]？（多条合并在一条消息里也算，说明哪条在最前）")
    print("  2) 微信输入框里现在还剩下什么？")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="pyin —— 「更有趣（存疑）的输入方式」原型：写 Python，输出发进微信")
    ap.add_argument("--dry-run", action="store_true",
                    help="不真实注入：运行+格式化照常，注入改为打印到终端")
    ap.add_argument("--selftest", action="store_true",
                    help="离线自测（无需 GUI/授权/微信）")
    ap.add_argument("--doctor", action="store_true",
                    help="只读体检：授权/后端/微信/前台逐项检查，不注入任何按键")
    ap.add_argument("--send-key", default=SEND_KEY_DEFAULT,
                    choices=("enter", "cmd_enter", "ctrl_enter"),
                    help="发送键：enter=Return（默认，需微信开『按 Enter 发送消息』）；"
                         "cmd_enter=⌘+Return；ctrl_enter=Ctrl+Return。"
                         "若输出进了输入框但没发出去，换这里或改微信设置")
    ap.add_argument("--send-engine", default="cgevent",
                    choices=("cgevent", "osascript"),
                    help="发送键的注入引擎：cgevent=CGEvent+HID 源（默认）；"
                         "osascript=System Events 合成（另一条路径）")
    ap.add_argument("--probe-send", action="store_true",
                    help="发送策略探针：向微信测试聊天注入 4 条带标记探针，"
                         "定位哪种方式能触发发送（真实注入！）")
    ap.add_argument("--overlay", action="store_true",
                    help="原位覆盖模式（档位 B）：输入框直接叠在微信真实输入框上，"
                         "随微信前台自动显隐并跟随（Esc 暂停 · ⌘G 幽灵形态）")
    ap.add_argument("--overlay-probe", action="store_true",
                    help="只读探测：打印微信窗口边界与输入区定位结果，不启动界面")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(selftest())
    if args.doctor:
        sys.exit(doctor())
    if args.probe_send:
        sys.exit(probe_send())
    if args.overlay or args.overlay_probe:
        import overlay
        overlay.run(dry_run=args.dry_run, send_key=args.send_key,
                    send_engine=args.send_engine, probe=args.overlay_probe)
        return
    Editor(dry_run=args.dry_run, send_key=args.send_key,
           send_engine=args.send_engine).run()


if __name__ == "__main__":
    main()
