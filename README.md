# pyin — 把 Python 的输出发进微信聊天

> 「更有趣（存疑）的输入方式」桌面原型：一个贴在微信输入框上的隐形代码输入框——写 Python，Shift+Enter 运行，**输出（连报错一起）直接变成聊天消息**。
> macOS 已完整实测 · Windows / Linux 为首发版本（未经实测）· 零第三方依赖（macOS 可选 pyobjc 增强）· 仅 Python 标准库 + 系统原生 API
> 作者：9M-XXB · 实现与调研：ZCode（GLM-5.3-Flash）· 2026-11 · English: [README.en.md](README.en.md)

## 演示视频

<video src="assets/demo.mp4" controls muted playsinline width="100%"></video>

*悬浮隐形输入框写 Python → Shift+Enter 运行 → 输出（含报错）直达微信聊天。敏感区域已按框填充遮盖。浏览器不内联播放时：[assets/demo.mp4](assets/demo.mp4)。*

## 快速开始

```bash
git clone https://github.com/9M-XXB/How_To_Type.git
cd How_To_Type

# macOS（推荐 venv 安装 pyobjc，提供全局热键与 CGEvent 注入）
python3 -m venv .venv && .venv/bin/pip install pyobjc-framework-Quartz
./run.sh

# Windows（零第三方依赖）
python pyin.py

# Linux（需 X11/XWayland + xdotool + xclip）
./run.sh
```

三个步骤即可使用：

1. **授权**（macOS 必需，Windows 不需要）：系统设置 → 隐私与安全性 → 辅助功能 → 给运行 pyin 的终端 App 开开关（详见[授权说明](#授权与安全)）。
2. **校准**（一次即可）：微信停在某聊天 → `./run.sh --overlay-tune` → 点击橙色外框 → 方向键对齐 → 回车保存。
3. **使用**：`./run.sh` → 覆盖框自动贴合微信输入框 → 点击打字 → **Shift+Enter** 运行并发送。

## 三种模式

| 模式 | 命令 | 说明 |
|---|---|---|
| **原位覆盖**（默认） | `./run.sh` | 无边框输入框直接叠在微信真实输入框上，随微信前台自动显隐并跟随移动，一次启动持续生效 |
| 悬浮编辑器 | `./run.sh --editor` | 自由位置的置顶编辑器（v0.1 形态） |
| 输入区校准 | `./run.sh --overlay-tune` | 半透明橙色框微调对齐真实输入框，回车保存（按平台独立记忆） |

工具命令（均可独立运行）：

| 命令 | 用途 |
|---|---|
| `--overlay-probe` | 只读探测：打印微信窗口边界与输入区定位结果 |
| `--doctor` | 只读体检：授权 / 后端 / 微信 / 前台逐项检查——发不出去先跑它 |
| `--selftest` | 离线自测（无需 GUI / 授权 / 微信） |
| `--dry-run` | 不真实注入：运行 + 格式化照常，注入改为打印到终端 |
| `--probe-send` | 发送探针：向测试聊天注入 4 条带标记探针，定位哪种方式能发送（真实注入） |
| `--send-key <k>` | 换发送键：enter（默认）/ cmd_enter / ctrl_enter |
| `--send-engine <e>` | 换发送键注入引擎：cgevent（默认）/ osascript（仅 macOS） |
| `--log-keys` | 诊断日志记录每一次按键（默认只记非可打印键） |

## 工作流程（30 秒理解）

```
微信前台 ──► 覆盖框自动贴合输入框 ──► 你写 Python（Enter=换行）
   └── Shift+Enter ──► 覆盖框让位 ──► 子进程运行（5s 超时保护）
        ──► 输出原样进剪贴板 ──► 激活微信 + 点击真实输入框
        ──► Ctrl/Cmd+V ──► Enter 发送 ──► 恢复你的剪贴板
        ──► 1s 内覆盖框清空内容回到原位，继续写下一段
```

| 行为 | 规则 |
|---|---|
| 输出形态 | **不加任何头，原样直发**——正常输出与报错 traceback 都发 |
| 无输出 | 不发送（不粘贴也不回车），状态栏提示 |
| 超时 | 5 秒强制终止，已产生的输出照发 |
| 长输出 | 超过 2000 字符中间截断 |
| 失败 | 覆盖框保留代码便于重试；错误详情见 `pyin.log` |

## 授权与安全

| 平台 | 授权要求 | 说明 |
|---|---|---|
| macOS | 辅助功能（必需） | 按**宿主 App** 归因：从 iTerm 启动就授权 iTerm，从 VSCode 启动就授权 VSCode；换终端要重新授权 |
| Windows | 无 | SendInput 注入无需授权 |
| Linux | 无 | X11/XWayland；Wayland 原生会话暂不支持 |

两个容易踩的坑（macOS 实测）：

- **"监听"与"注入"是两项独立授权**：热键能呼出不代表注入可用，以 `--doctor` 的"辅助功能授权"一行为准。
- **授权按宿主 App 归因**：拿不准就跑 `./run.sh --doctor`，它直接告诉你当前这次启动能不能注入。

## 诊断与排查

所有运行记录持久化在 **`pyin.log`**（已 gitignore）：Tk 回调异常、线程 traceback、覆盖框的点击/焦点事件、注入链每一步。**遇到问题先看它，再把相关行发给作者。**

| 症状 | 首选排查 | 工具 |
|---|---|---|
| 覆盖框没出现 / 位置不对 | 微信是否前台；定位矩形是否对齐 | `--overlay-probe` → `--overlay-tune` |
| 点击覆盖框无法打字 | 日志有无 `<FocusIn>` | `pyin.log`（可加 `--log-keys`） |
| 输出进了微信但没发送 | 微信「按 Enter 发送消息」设置；或换发送键 | `--send-key cmd_enter` |
| 输出被粘贴进覆盖框 | 竞态（已修复）；仍有则看日志注入时刻事件 | `pyin.log` |
| 什么都没发生、状态栏无变化 | 进程是否存活 | `--doctor` / `--selftest` |

## 工作原理

四个平台无关的层次 + 一个平台层（`native.py`）：

| 层 | 职责 |
|---|---|
| 执行层 | 子进程 `python -` 运行（`-u` 无缓冲；超时杀进程组后仍保留已产生输出）；结果不加头原样直发，超 2000 字符截断 |
| 注入层 | 剪贴板写入（回读校验）→ 激活微信并核实前台 → 粘贴和弦 → 回车 → 恢复剪贴板；未授权即显式失败 |
| 覆盖框 | 无边框置顶输入窗：Enter 换行、Shift+Enter 运行发送、Esc 暂停、⌘G 幽灵形态（macOS 45% 透明） |
| 定位层 | T1 AX 焦点元素（仅 macOS）→ T2 窗口边界 + 点偏移模型（`list_w/right_m/box_h/bottom_m`）→ 校准文件持久化 |

可调常量（各文件顶部）：运行超时、注入时延、剪贴板恢复时延、发送上限、热键键码、发送键、定位偏移模型。

## 跨平台支持

| 平台 | 注入与剪贴板 | 窗口定位 | 全局热键 | 授权 |
|---|---|---|---|---|
| **macOS** | CGEventPost + 私有 HID 源 + pbcopy/pbpaste | AX 焦点元素（T1）+ 窗口启发式（T2） | CGEventTap ⌘⌥E | 辅助功能 |
| **Windows** | SendInput + CF_UNICODETEXT 剪贴板（纯 ctypes） | EnumWindows（标题/类名匹配）+ T2 校准 | RegisterHotKey Ctrl+Alt+E | 无需 |
| **Linux** | xdotool + xclip | xdotool search + T2 校准 | 暂不支持 | 无需（X11/XWayland） |

- 平台实现集中在 [`native.py`](native.py)，pyin 与 overlay 按 `native.PLATFORM` 分派；macOS 路径保留在原文件。
- 输入区校准按平台分节保存（`overlay_calib.json`，已 gitignore）。
- Windows 的 `cmd_enter` 等同 `ctrl_enter`；Linux 需要 X11/XWayland 会话。
- **诚实声明**：Windows / Linux 分支按官方 API 编写、**尚未在对应平台实测**；遇到问题请带 `pyin.log` 反馈。

## 已知限制

**输入与发送**

1. 不检测焦点：发送目标 = 覆盖框所贴的输入框（档位 A→B 的定义），请先把微信停在目标聊天。
2. 微信输入框里的已有文字不会被读取或清空，输出追加到光标处。
3. 发送键默认 Enter，依赖微信「按 Enter 发送消息」设置；可 `--send-key` 换。
4. **报错也发送**（想法原述明确要求）：traceback 可能含敏感路径；"撤回窗口"尚未实现。
5. 无输出不发送（空消息发不出，且回车可能误发草稿）。
6. 每次运行是独立子进程：无跨次变量保留。

**隐私与剪贴板**

7. 发送后 1.5s 内你复制的内容会被恢复动作覆盖。
8. 报错与输出可能含敏感信息，建议先在测试聊天验证。

**环境**

9. IME 组字期间的 Shift+Enter 行为未验证。
10. 微信版本未逐一验证（只用"激活 + 系统按键"，不依赖微信内部 API）。
11. Windows / Linux 分支未经对应平台实测（首发版本）。

## 验证记录

macOS 宿主已完成 9 轮迭代与验证（探针实测送达、竞态修复、隐形化等），完整过程见上游《调研笔记》§6.3；本仓库内可用 `--selftest`（12 项）、`--doctor`、`--dry-run` 随时自检。

## 出处与依据

- **想法与方案**：作者原述（2026-11-13）；可行性调研档位 A/B（作者工作区文档，未随仓库分发）；注入先例 [Espanso](https://espanso.org)。
- **平台 API**（官方文档，非工作区来源）：macOS CGEventTap / CGEventPost / AX / `AXIsProcessTrusted`；Windows SendInput / RegisterHotKey / 剪贴板 API；Linux XTEST（经 xdotool）。
- **微信行为假设**（未逐版本验证）："按 Enter 发送消息"默认开启、粘贴可进入输入框、激活后焦点回到输入框。
- **键位与范围**（作者 2026-11-17 指定）：Python 语言、微信目标、Enter=换行 / Shift+Enter=运行并发送、结果不加任何头原样直发。
