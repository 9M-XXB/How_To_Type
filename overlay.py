#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wx_overlay —— pyin 原位覆盖模式（可行性调研档位 B 的 macOS 实现）

在微信真实输入框的正上方放置一个无边框、同尺寸的 pyin 输入框（默认深色仿真样式，
⌘G 切换半透明幽灵形态），随微信前台状态自动显示/隐藏并跟随输入框位置移动——
即"一直开启"的原位替换输入体验。Enter 换行、Shift+Enter 运行并发送，语义与
悬浮编辑器模式一致；注入前会先对真实输入框中心补一次合成点击，保证粘贴与
回车落在真正的第一响应者上。

定位三级降级（自动选择，启动与运行日志均有记录）：
  T1 无障碍 AX：读取微信 AXFocusedUIElement 的位置与尺寸（需辅助功能授权；
     微信为自绘 UI，是否暴露焦点文本元素以实机探测为准）
  T2 窗口启发式：CGWindowList 取微信聊天窗口边界，按比例常量折算输入区
     （比例常量 HEUR_*；用 --overlay-probe 可观测实际矩形后调整）

用法：./run.sh --overlay [--dry-run] [--send-key ...] [--send-engine ...]
探测：./run.sh --overlay-probe（只读打印定位结果，不启动界面）

出处标注（AGENTS.md 引用要求）：
  - 方案：作者工作区《调研笔记》§1.2 档位 B（原位覆盖；未随仓库分发）。
  - AX API（非工作区来源，Apple 官方）：AXUIElementCreateApplication /
    AXUIElementCopyAttributeValue / AXValueGetValue，经 ctypes 直调
    ApplicationServices 框架；坐标均为全局屏幕"点"（point），与 Tk/CGEvent 一致。
"""

import ctypes
import queue
import threading
import time

import pyin
from pyin import (FOCUS_DELAY, PASTE_DELAY, WECHAT_NAMES, HAVE_QUARTZ,
                  log, run_code, format_result, has_effective_code,
                  Injector, start_hotkey)

POLL_INTERVAL = 0.5          # 前台/位置轮询周期（秒）
MIN_RECT = 60                # 输入框最小合理边长（点），过小视为定位失败
# T2 启发式：输入区占聊天窗口边界的比例（按实拍微信窗口标定，可调）
HEUR = {"x0": 0.135, "x1": 0.995, "y0": 0.785, "y1": 0.955}

# ---- AX ctypes 桥（非工作区来源：Apple ApplicationServices/HIServices） --------
_coref = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
_appserv = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")

_cfstr_raw = _coref.CFStringCreateWithCString
_cfstr_raw.restype = ctypes.c_void_p
_cfstr_raw.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]

_axcopy = _appserv.AXUIElementCopyAttributeValue
_axcopy.restype = ctypes.c_int32
_axcopy.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]

_axcreateapp = _appserv.AXUIElementCreateApplication
_axcreateapp.restype = ctypes.c_void_p
_axcreateapp.argtypes = [ctypes.c_int32]

_axvalueget = _appserv.AXValueGetValue
_axvalueget.restype = ctypes.c_bool
_axvalueget.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_double)]

_kUTF8 = 0x08000100
_kPointType = 1              # kAXValueCGPointType（CGSize 同为两个 double，布局一致）


def _cfstr(s):
    return _cfstr_raw(None, s.encode("utf-8"), _kUTF8)


def _ax_get(el, name):
    ref = ctypes.c_void_p()
    err = _axcopy(el, _cfstr(name), ctypes.byref(ref))
    return err, ref.value


def _ax_point(ref):
    out = (ctypes.c_double * 2)()
    return (out[0], out[1]) if _axvalueget(ref, _kPointType, out) else None


def _ax_str(ref):
    if not ref:
        return ""
    buf = ctypes.create_string_buffer(256)
    ok = _coref.CFStringGetCString(ref, buf, 256, _kUTF8)
    return buf.value.decode("utf-8", "replace") if ok else ""


# ---- 窗口/前台探测（CGWindowList，无需任何授权） --------------------------------
def wechat_windows():
    """[(pid, (X, Y, W, H))]：微信名下所有可见 layer-0 窗口，按面积降序。"""
    import Quartz
    wins = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)
    out = []
    for w in wins or []:
        owner = w.get("kCGWindowOwnerName", "")
        if not any(n in owner for n in WECHAT_NAMES):
            continue
        if w.get("kCGWindowLayer", 99) != 0:
            continue
        b = w.get("kCGWindowBounds", {})
        out.append((w.get("kCGWindowOwnerPID"),
                    (b.get("X", 0), b.get("Y", 0),
                     b.get("Width", 0), b.get("Height", 0))))
    out.sort(key=lambda t: t[1][2] * t[1][3], reverse=True)
    return out


def frontmost_owner():
    """最前台可见 layer-0 窗口的属主名（无窗口/锁屏返回 None）。"""
    import Quartz
    wins = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)
    for w in wins or []:
        if w.get("kCGWindowLayer", 99) == 0:
            return w.get("kCGWindowOwnerName", "")
    return None


def wechat_front():
    o = frontmost_owner()
    return bool(o) and any(n in o for n in WECHAT_NAMES), o or ""


# ---- 定位：T1 AX 焦点元素 / T2 窗口启发式 ----------------------------------------
def ax_input_rect(pid, win_h):
    """T1：微信进程的 AX 焦点元素 → (x, y, w, h)（全局屏幕点）。失败返回 (None, 原因)。"""
    try:
        app = _axcreateapp(int(pid))
        err, el = _ax_get(app, "AXFocusedUIElement")
        if err != 0 or not el:
            return None, f"AX 焦点元素不可用（err={err}：未授权或微信内无焦点）"
        err, role_ref = _ax_get(el, "AXRole")
        role = _ax_str(role_ref)
        err, pref = _ax_get(el, "AXPosition")
        if err:
            return None, f"AX 无位置属性（err={err}）"
        err, sref = _ax_get(el, "AXSize")
        if err:
            return None, f"AX 无尺寸属性（err={err}）"
        pos, size = _ax_point(pref), _ax_point(sref)
        if not pos or not size:
            return None, "AX 位置/尺寸解析失败"
        x, y = pos
        w, h = size
        if role not in ("AXTextArea", "AXTextField"):
            return None, f"AX 焦点角色 {role} 非文本框"
        if h > win_h * 0.5 or h < 20 or w < 100:
            return None, f"AX 焦点元素尺寸异常 {w:.0f}×{h:.0f}"
        return (x, y, w, h), f"AX 焦点元素（{role}）"
    except Exception as e:                       # ctypes 桥自身异常也要兜住
        return None, f"AX 调用异常 [{e!r}]"


def heuristic_rect(win):
    """T2：微信聊天窗口边界 → 输入区推算矩形（比例常量 HEUR_*）。"""
    X, Y, W, H = win
    return (X + W * HEUR["x0"], Y + H * HEUR["y0"],
            W * (HEUR["x1"] - HEUR["x0"]), H * (HEUR["y1"] - HEUR["y0"]))


def locate(max_win):
    """返回 (rect, how)。rect 为全局屏幕点 (x, y, w, h)；定位失败 → (None, 原因)。"""
    win_pids = {pid for pid, _ in max_win}
    win_h = max((b[3] for _, b in max_win), default=0)
    for pid in win_pids:
        rect, how = ax_input_rect(pid, win_h)
        if rect:
            return rect, how
        last_ax = how
    win = max(max_win, key=lambda t: t[1][2] * t[1][3])[1]
    if win[2] < MIN_RECT or win[3] < MIN_RECT:
        return None, last_ax if win_pids else "微信窗口不可见"
    return heuristic_rect(win), f"T2 窗口启发式（{last_ax}）"


def locate_safe(max_win):
    """locate 的防退化版：结果异常尺寸时回退启发式。"""
    rect, how = locate(max_win)
    if rect and (rect[2] < MIN_RECT or rect[3] < MIN_RECT or rect[2] > 4000):
        rect2 = heuristic_rect(max(max_win, key=lambda t: t[1][2] * t[1][3])[1])
        return rect2, f"T2 启发式（T1 异常矩形 {how}）"
    return rect, how


# ---- 覆盖框 GUI ------------------------------------------------------------------
def run(dry_run=False, send_key="enter", send_engine="cgevent", probe=False):
    import tkinter as tk

    injector = Injector(dry_run, send_key=send_key, send_engine=send_engine)

    if probe:
        probe_only(injector)
        return

    log(f"原位覆盖模式启动（档位 B）：注入后端 {injector.backend}，发送键 {injector.send_key}"
        f"（引擎 {injector.send_engine}）" + ("（dry-run）" if dry_run else ""))
    trusted = pyin.accessibility_trusted()
    log("辅助功能授权：" + ("已授权" if trusted else
                           "未授权（AX 定位与注入都会失败/被丢弃）" if trusted is False else "无法判定"))

    root = tk.Tk()
    root.overrideredirect(True)                  # 无边框：贴齐真实输入框
    root.attributes("-topmost", True)
    root.configure(bg="#1e1e1e")
    root.geometry("600x120+200+600")             # 初始占位，首轮定位后覆盖

    text = tk.Text(root, bg="#1e1e1e", fg="#d6d6d6", insertbackground="#d6d6d6",
                   relief="flat", wrap="word", font=("Menlo", 13), padx=8, pady=6,
                   selectbackground="#2f4a78", selectforeground="#ffffff")
    text.pack(fill="both", expand=True)
    text.insert("1.0", "# 原位覆盖：直接在这里写 Python\n# Enter 换行 · Shift+Enter 运行并发送\n")
    alpha = {"v": 1.0}

    status = tk.Label(root, text="", anchor="w", fg="#9e9e9e", bg="#1e1e1e",
                      font=("Menlo", 10))
    status.pack(fill="x", side="bottom")

    def set_status(msg, color):
        colors = {"green": "#4caf50", "red": "#ef5350", "gray": "#9e9e9e"}
        status.configure(text=msg, fg=colors.get(color, "#9e9e9e"))

    def toggle_ghost(_ev=None):
        alpha["v"] = 0.45 if alpha["v"] == 1.0 else 1.0
        root.attributes("-alpha", alpha["v"])
        set_status(f"形态：{'幽灵（45%）' if alpha['v'] < 1 else '深色仿真'}", "gray")
        return "break"

    root.bind("<Command-g>", toggle_ghost)
    root.bind("<Command-G>", toggle_ghost)

    q = queue.Queue()
    state = {"rect": None, "how": "", "busy": False, "paused": False,
             "visible": False, "wechat_seen": False}

    def on_run(_ev=None):
        if state["busy"]:
            return "break"
        code = text.get("1.0", "end").rstrip("\n")
        if not has_effective_code(code):
            set_status("只有注释/空行——没有可运行的代码，未发送", "gray")
            return "break"
        state["busy"] = True
        rect = state["rect"]
        set_status("运行中…", "gray")
        log(f"Shift+Enter 触发（原位覆盖）：{len(code.splitlines())} 行"
            f"（rect={rect and tuple(round(v) for v in rect)}，dry={dry_run}）")
        threading.Thread(target=_worker, args=(code, rect), daemon=True).start()
        return "break"

    text.bind("<Shift-Return>", on_run)
    text.bind("<Shift-KP_Enter>", on_run)

    def _worker(code, rect):
        res = run_code(code)
        out = format_result(res)
        if not out:
            q.put(("done", (None, "运行完成，无输出——未发送", 0)))
            return
        q.put(("hide", None))
        time.sleep(0.2)
        ok, msg = injector.send_result(out, click_rect=rect)
        if res["timed_out"]:
            msg += "（超时强制终止）"
        q.put(("done", (ok, msg, len(out))))

    def _poll():
        try:
            while True:
                kind, payload = q.get_nowait()
                if kind == "hide":
                    _show(False)
                elif kind == "show":
                    _show(True)
                elif kind == "move":
                    x, y, w, h = payload
                    root.geometry(f"{max(80, int(w)):.0f}x{max(48, int(h)):.0f}"
                                  f"+{int(x):.0f}+{int(y):.0f}")
                elif kind == "status":
                    set_status(*payload)
                elif kind == "hotkey":
                    on_escape()
                elif kind == "done":
                    ok, msg, n = payload
                    state["busy"] = False
                    if ok is True:
                        set_status(f"✅ {msg}（{n} 字符）", "green")
                    elif ok is False:
                        set_status(f"❌ {msg}", "red")
                    else:
                        set_status(msg, "gray")
        except queue.Empty:
            pass
        except tk.TclError:
            return
        root.after(int(POLL_INTERVAL * 1000), _poll)

    def _show(v):
        if v and not state["visible"]:
            root.deiconify()
            root.attributes("-topmost", True)
            root.lift()
            text.focus_set()
        elif not v and state["visible"]:
            root.withdraw()
        state["visible"] = v

    def on_escape(_ev=None):
        state["paused"] = not state["paused"]
        set_status("已暂停（微信切走再切回即恢复）" if state["paused"] else "已恢复", "gray")
        if state["paused"]:
            _show(False)
        return "break"

    text.bind("<Escape>", on_escape)
    root.bind("<Escape>", on_escape)

    # ---- 跟踪线程：显隐 + 跟随 -----------------------------------------------------
    def tracker():
        last_key = None
        while True:
            time.sleep(POLL_INTERVAL)
            try:
                front, owner = wechat_front()
                if state["busy"]:
                    continue                      # 注入进行中不动窗口
                if not front:
                    if state["visible"]:
                        q.put(("hide", None))
                    if state["paused"]:
                        state["paused"] = False   # 微信切走即自动恢复"一直开启"
                        q.put(("status", ("已恢复跟随（微信重新到前台时显示）", "gray")))
                    last_key = None
                    continue
                if state["paused"]:
                    continue
                wins = wechat_windows()
                if not wins:
                    continue
                state["wechat_seen"] = True
                rect, how = locate_safe(wins)
                if not rect:
                    q.put(("status", (f"定位失败：{how}", "red")))
                    continue
                key = tuple(round(v / 6) for v in rect)      # 6pt 步长去抖
                if key != last_key:
                    last_key = key
                    state["rect"], state["how"] = rect, how
                    q.put(("move", rect))
                    log(f"输入框定位更新：{how} → "
                        f"({rect[0]:.0f},{rect[1]:.0f}) {rect[2]:.0f}×{rect[3]:.0f}")
                q.put(("show", None))
            except Exception as e:
                log(f"跟踪线程异常：{e!r}")

    def hotkey_cb():
        q.put(("hotkey", None))

    start_hotkey(hotkey_cb, log, desc="暂停/恢复覆盖")
    root.after(int(POLL_INTERVAL * 1000), _poll)
    threading.Thread(target=tracker, daemon=True).start()
    set_status("等待微信到前台…", "gray")
    log("覆盖框就绪：微信到前台后自动贴合其输入框（Esc 暂停 · ⌘G 幽灵形态）")
    root.mainloop()


# ---- 只读探测（--overlay-probe） --------------------------------------------------
def probe_only(injector):
    front, owner = wechat_front()
    print(f"[pyin] 前台应用：{owner or '未知'}"
          + ("（微信在前台 ✓）" if any(n in owner for n in WECHAT_NAMES) else ""))
    wins = wechat_windows()
    if not wins:
        print("[pyin] 微信窗口：未找到（微信未在运行或全部窗口隐藏）")
        return 1
    for pid, b in wins:
        print(f"[pyin] 微信窗口：pid={pid} 位置=({b[0]:.0f},{b[1]:.0f}) "
              f"大小={b[2]:.0f}×{b[3]:.0f}")
    rect, how = locate_safe(wins)
    if rect:
        print(f"[pyin] 输入框定位：{how}")
        print(f"[pyin]   矩形 = ({rect[0]:.0f}, {rect[1]:.0f}) "
              f"{rect[2]:.0f}×{rect[3]:.0f}（全局屏幕点）")
        print("[pyin] 提示：T1=AX 焦点元素（微信需有焦点文本框）；"
              "T2=窗口启发式（比例常量 HEUR_*，可在 wx_overlay.py 调整）")
    else:
        print(f"[pyin] 输入框定位失败：{how}")
        print("[pyin] 提示：T1 需要辅助功能授权 + 微信内存在焦点文本框；"
              "T2 只依赖窗口边界。")
    return 0 if rect else 1
