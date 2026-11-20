#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
native —— pyin 跨平台平台层（Windows / Linux 实现；macOS 实现保留在 pyin.py/overlay.py）

pyin.Injector 与 overlay 按 native.PLATFORM 分派到对应实现。

Windows：纯 ctypes（user32/kernel32），零第三方依赖。
  - 微信窗口：EnumWindows 可见窗口，标题含 微信/WeChat 或类名
    WeChatMainWndForPC / Weixin（3.x/4.x 常见类名，以实机为准）
  - 注入：SendInput 键盘（Ctrl+V 粘贴、Return 发送）、SetCursorPos+mouse_event 点击
  - 剪贴板：CF_UNICODETEXT（OpenClipboard / SetClipboardData，10 次重试）
  - 热键：RegisterHotKey Ctrl+Alt+E + 消息循环线程
Linux：xdotool + xclip（需 X11 / XWayland；Wayland 原生会话不受支持）。
  - 微信窗口：xdotool search --class wechat / --name 微信 + getwindowgeometry
  - 注入：xdotool mousemove/click、key ctrl+v、key Return
  - 剪贴板：xclip -selection clipboard
  - 热键：暂不支持（覆盖框聚焦时 Esc 可暂停）

未实测声明：Windows/Linux 分支按官方 API 文档编写，尚未在对应平台实测；
问题请以 pyin.log 反馈（双语 README 有已知差异清单）。
"""

import os
import subprocess
import sys
import time

PLATFORM = {"darwin": "darwin", "win32": "windows"}.get(sys.platform, "linux")
WECHAT_NAMES = ("微信", "WeChat", "Weixin")
WECHAT_WIN_CLASSES = ("WeChatMainWndForPC", "Weixin", "WeChat")


# ======================================================================
# Windows（纯 ctypes：user32 / kernel32）
# ======================================================================
if PLATFORM == "windows":
    import ctypes
    import ctypes.wintypes as wt
    import threading

    _u = ctypes.windll.user32
    _k = ctypes.windll.kernel32
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    VK_CONTROL = 0x11
    VK_RETURN = 0x0D
    VK_V = 0x56
    SW_RESTORE = 9

    _u.SetForegroundWindow.argtypes = [wt.HWND]
    _u.GetForegroundWindow.restype = wt.HWND
    _u.GetWindowTextLengthW.argtypes = [wt.HWND]
    _u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    _u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    _u.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
    _u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    _k.OpenProcess.restype = wt.HANDLE
    _k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    _k.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR,
                                              ctypes.POINTER(wt.DWORD)]

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                    ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_void_p)]

    class _INPUTunion(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wt.DWORD), ("u", _INPUTunion)]

    def _win_key(vk, up=False):
        i = INPUT()
        i.type = INPUT_KEYBOARD
        i.u.ki.wVk = vk
        if up:
            i.u.ki.dwFlags |= KEYEVENTF_KEYUP
        _u.SendInput(1, ctypes.byref(i), ctypes.sizeof(i))

    def _win_chord(vks, delay=0.02):
        for vk in vks:
            _win_key(vk)
            time.sleep(delay)
        for vk in reversed(vks):
            _win_key(vk, up=True)
            time.sleep(delay)

    def _win_wechat_hwnds():
        hwnds = []
        proto = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

        def proc(hwnd, _lp):
            try:
                if not _u.IsWindowVisible(hwnd):
                    return True
                n = _u.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(n + 2)
                _u.GetWindowTextW(hwnd, buf, n + 2)
                cls = ctypes.create_unicode_buffer(256)
                _u.GetClassNameW(hwnd, cls, 256)
                if ("微信" in buf.value or "WeChat" in buf.value
                        or cls.value in WECHAT_WIN_CLASSES):
                    hwnds.append(hwnd)
            except Exception:
                pass
            return True

        cb = proto(proc)
        _u.EnumWindows(cb, 0)
        return hwnds

    def clipboard_set(text):
        data = text.encode("utf-16-le") + b"\x00\x00"
        for _ in range(10):                       # 剪贴板可能被其他进程占用
            if _u.OpenClipboard(0):
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("OpenClipboard 失败")
        try:
            _u.EmptyClipboard()
            h = _k.GlobalAlloc(GMEM_MOVEABLE, len(data))
            p = _k.GlobalLock(h)
            ctypes.memmove(p, data, len(data))
            _k.GlobalUnlock(h)
            _u.SetClipboardData(CF_UNICODETEXT, h)   # 成功后句柄归系统
        finally:
            _u.CloseClipboard()

    def clipboard_get():
        for _ in range(10):
            if _u.OpenClipboard(0):
                break
            time.sleep(0.05)
        else:
            return ""
        try:
            h = _u.GetClipboardData(CF_UNICODETEXT)
            if not h:
                return ""
            p = _k.GlobalLock(h)
            s = ctypes.wstring_at(p)
            _k.GlobalUnlock(h)
            return s
        finally:
            _u.CloseClipboard()

    def wechat_windows():
        out = []
        for hwnd in _win_wechat_hwnds():
            r = wt.RECT()
            pid = wt.DWORD(0)
            if _u.GetWindowRect(hwnd, ctypes.byref(r)):
                _u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                out.append((pid.value, (r.left, r.top,
                                        r.right - r.left, r.bottom - r.top)))
        out.sort(key=lambda t: t[1][2] * t[1][3], reverse=True)
        return out

    def frontmost_owner():
        hwnd = _u.GetForegroundWindow()
        if not hwnd:
            return ""
        n = _u.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 2)
        _u.GetWindowTextW(hwnd, buf, n + 2)
        pid = wt.DWORD(0)
        _u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = ""
        h = _k.OpenProcess(0x1000, False, pid.value)   # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            buf2 = ctypes.create_unicode_buffer(512)
            size = wt.DWORD(512)
            if _k.QueryFullProcessImageNameW(h, 0, buf2, ctypes.byref(size)):
                exe = os.path.basename(buf2.value)
            _k.CloseHandle(h)
        return f"{buf.value} [{exe or pid.value}]"

    def wechat_front():
        o = frontmost_owner()
        hit = (any(n in o for n in WECHAT_NAMES)
               or any(n in o.lower() for n in ("wechat", "weixin")))
        return hit, o

    def activate_wechat():
        hwnds = _win_wechat_hwnds()
        if not hwnds:
            return False, "未找到微信窗口（先打开微信）"
        hwnd = hwnds[0]
        _u.ShowWindow(hwnd, SW_RESTORE)
        _u.SetForegroundWindow(hwnd)
        time.sleep(0.05)
        _u.SetForegroundWindow(hwnd)                 # 双次调用提升成功率
        return True, "微信已置前"

    def click_at(x, y):
        _u.SetCursorPos(int(x), int(y))
        time.sleep(0.05)
        _u.mouse_event(0x0002, 0, 0, 0, 0)           # LEFTDOWN
        time.sleep(0.03)
        _u.mouse_event(0x0004, 0, 0, 0, 0)           # LEFTUP

    def paste_chord():
        _win_chord([VK_CONTROL, VK_V])

    def enter_send(send_key="enter"):
        if send_key in ("cmd_enter", "ctrl_enter"):
            _win_chord([VK_CONTROL, VK_RETURN])
        else:
            _win_chord([VK_RETURN])

    def hotkey_start(callback, on_log):
        """Ctrl+Alt+E 全局热键（RegisterHotKey + 消息循环线程）。"""
        MOD_CONTROL, MOD_ALT, WM_HOTKEY = 0x0002, 0x0001, 0x0312

        def _loop():
            if not _u.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT, 0x45):
                on_log("全局热键注册失败（可能被其他程序占用）")
                return
            msg = wt.MSG()
            while _u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    callback()
            _u.UnregisterHotKey(None, 1)

        threading.Thread(target=_loop, daemon=True).start()
        on_log("全局热键已启用：Ctrl+Alt+E 暂停/恢复覆盖")
        return True

    def accessibility_trusted():
        return True                                   # Windows 注入无需特殊授权

    def make_keyable(_root):
        return "Windows 窗口原生可聚焦（无需处理）"

    def ax_input_rect(_pid, _win_h):
        return None, "T1（AX 焦点元素）仅 macOS；Windows 使用 T2 + 校准"


# ======================================================================
# Linux（xdotool + xclip；需 X11 / XWayland）
# ======================================================================
elif PLATFORM == "linux":
    import shutil
    import threading

    def _xd():
        return shutil.which("xdotool")

    def _xclip():
        return shutil.which("xclip")

    def _run(args, stdin=None, timeout=5):
        return subprocess.run(args, input=stdin, capture_output=True,
                              text=True, timeout=timeout)

    def clipboard_set(text):
        x = _xclip()
        if not x:
            raise RuntimeError("未找到 xclip（安装：sudo apt install xclip）")
        subprocess.run([x, "-selection", "clipboard"], input=text,
                       capture_output=True, text=True, check=True)

    def clipboard_get():
        x = _xclip()
        if not x:
            return ""
        p = subprocess.run([x, "-selection", "clipboard", "-o"],
                           capture_output=True, text=True)
        return p.stdout

    def wechat_windows():
        xd = _xd()
        if not xd:
            return []
        ids = []
        for kw in ("wechat", "weixin", "微信"):
            p = _run([xd, "search", "--onlyvisible", "--class", kw]
                     if kw != "微信" else
                     [xd, "search", "--onlyvisible", "--name", kw])
            for line in (p.stdout or "").splitlines():
                line = line.strip()
                if line.isdigit() and line not in ids:
                    ids.append(line)
        out = []
        for wid in ids:
            g = _run([xd, "getwindowgeometry", wid])
            pos = geom = None
            for line in (g.stdout or "").splitlines():
                if line.startswith("Position:"):
                    pos = line.split(":", 1)[1].split("(")[0].strip()
                elif line.startswith("Geometry:"):
                    geom = line.split(":", 1)[1].strip()
            if not pos or not geom:
                continue
            try:
                px, py = [int(v) for v in pos.split(",")]
                w_, h_ = [int(v) for v in geom.lower().split("x")]
                out.append((0, (px, py, w_, h_)))
            except ValueError:
                continue
        out.sort(key=lambda t: t[1][2] * t[1][3], reverse=True)
        return out

    def frontmost_owner():
        xd = _xd()
        if not xd:
            return ""
        p = _run([xd, "getactivewindow", "getwindowname"])
        return (p.stdout or "").strip()

    def wechat_front():
        o = frontmost_owner()
        hit = bool(o) and any(n in o for n in WECHAT_NAMES)
        return hit, o

    def activate_wechat():
        wins = wechat_windows()
        xd = _xd()
        if not wins or not xd:
            return False, "未找到微信窗口（先打开微信）"
        wid = None
        for kw in ("wechat", "weixin"):
            p = _run([xd, "search", "--onlyvisible", "--class", kw])
            lines = [l.strip() for l in (p.stdout or "").splitlines() if l.strip().isdigit()]
            if lines:
                wid = lines[0]
                break
        if not wid:
            return False, "未定位到微信窗口 id"
        subprocess.run([xd, "windowactivate", wid], capture_output=True)
        return True, "微信已置前"

    def click_at(x, y):
        xd = _xd()
        subprocess.run([xd, "mousemove", str(int(x)), str(int(y))],
                       capture_output=True)
        time.sleep(0.05)
        subprocess.run([xd, "click", "1"], capture_output=True)

    def paste_chord():
        xd = _xd()
        subprocess.run([xd, "key", "ctrl+v"], capture_output=True)

    def enter_send(send_key="enter"):
        xd = _xd()
        key = {"cmd_enter": "ctrl+Return", "ctrl_enter": "ctrl+Return"}.get(
            send_key, "Return")
        subprocess.run([xd, "key", key], capture_output=True)

    def hotkey_start(callback, on_log):
        on_log("Linux 暂不支持全局热键（覆盖框聚焦时 Esc 可暂停）")
        return False

    def accessibility_trusted():
        return None                                   # X11 无此授权概念

    def make_keyable(_root):
        return "X11 窗口原生可聚焦（无需处理）"

    def ax_input_rect(_pid, _win_h):
        return None, "T1（AX 焦点元素）仅 macOS；Linux 使用 T2 + 校准"


# ======================================================================
# macOS：实现保留在 pyin.py / overlay.py（本模块只提供占位接口说明）
# ======================================================================
else:
    def accessibility_trusted():
        import pyin
        return pyin.accessibility_trusted()
