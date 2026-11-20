# pyin · 「更有趣（存疑）的输入方式」桌面原型（macOS · 微信 · Python）

> 想法出处：作者原述（2026-11-13，详见作者工作区《想法记录》文档，未随仓库分发）· 方案依据：可行性调研 §1.1 档位 A·MVP（注入先例 Espanso）· 实施记录：可行性调研 §6
> 本文由 ZCode（编码智能体，模型 GLM-5.3-Flash）编写（2026-11-17）· English: [README.en.md](README.en.md)

## 演示视频

<video src="assets/demo.mp4" controls muted playsinline width="100%"></video>

*悬浮编辑器写 Python → Shift+Enter 运行 → 输出（含报错）直达微信聊天；画面中的敏感区域已按框填充梗图遮盖。若你的查看器不内联播放：[assets/demo.mp4](assets/demo.mp4)。*

## 这是什么

把想法的**档位 A（MVP）**落到 macOS + 微信上：一个置顶悬浮代码编辑器，你在里面写 Python——

| 步骤 | 行为 | 键位/机制 |
|---|---|---|
| 1. 呼出 | 置顶悬浮编辑器出现 | 全局热键 `⌘⌥E`（或点击窗口/Dock） |
| 2. 写代码 | **Enter = 换行**（编辑器内正常写多行 Python） | 作者 2026-11-17 指定键位 |
| 3. 运行 | **Shift+Enter = 运行**：子进程 `python -` 执行，捕获 stdout/stderr，超时 5s 强杀 | 显式触发，绝无"边打边跑" |
| 4. 回注 | 运行结果（**正常输出或报错均算结果**，见想法原述）**不加任何头、原样直发**写剪贴板 → 激活微信 → `Cmd+V` 粘贴 | 激活后核实微信确为前台，否则**中止**（宁可不发也不发错）；无输出则跳过发送；注入前编辑器**必先收起**（窗口停在前台时注入不可靠——2026-11-17 实机反馈） |
| 5. 发送 | 模拟 `Enter` → 消息发出；编辑器收起，**发送完成 1 秒后自动重开**，可直接写下一段 | 恢复原剪贴板内容（1.5s 后） |

## 运行

```bash
./run.sh               # 默认模式 = 原位覆盖：输入框直接叠在微信真实输入框上，一次启动持续生效
./run.sh --overlay-tune  # 输入区校准：微调橙色外框至与真实输入框完全重合，回车保存
./run.sh --overlay-probe # 只读探测：打印微信窗口边界与输入区定位结果
./run.sh --editor      # 悬浮编辑器模式（v0.1 的自由位置置顶编辑器）
./run.sh --dry-run     # 不真实注入：运行+格式化照常，注入改为打印到终端（无权限也能体验全流程）
./run.sh --selftest    # 离线自测（无需 GUI/授权/微信）
./run.sh --doctor      # 只读体检：授权/后端/微信/前台逐项检查，不注入任何按键——发不出去先跑它
./run.sh --send-key cmd_enter    # 换发送键：enter（默认）/ cmd_enter / ctrl_enter
./run.sh --send-engine osascript  # 换发送键的注入引擎（CGEvent / osascript 两条合成路径）
./run.sh --probe-send  # 发送探针：向测试聊天注入 4 条带标记探针，定位哪种方式能触发发送（真实注入）
python3 pyin.py        # 无 .venv 时的降级模式（无热键；注入走 osascript，功能等价）
```

默认模式的说明见下节「原位覆盖模式」。

- 解释器 = 启动 pyin 的那个 Python；能 import 哪些第三方库取决于它（原型自身只用标准库 + macOS 自带 `pbcopy`/`pbpaste`/`osascript`）。
- `.venv/` 已被 [.gitignore](.gitignore) 忽略；重建：`python3 -m venv .venv && .venv/bin/pip install pyobjc-framework-Quartz`。

## 原位覆盖模式（默认模式 · 调研档位 B）

直接 `./run.sh` 即进入本模式；`--editor` 切回悬浮编辑器。校准工作流：

```bash
./run.sh --overlay-tune    # 方向键/Shift+方向键微调橙色外框，回车保存 overlay_calib.json
```

无边框输入框与微信真实输入框**同位同尺寸**重叠（默认深色仿真样式，`⌘G` 切换 45% 幽灵形态）。微信在前台时自动显示、切走自动隐藏、窗口移动自动跟随——"一直开启"的原位替换输入体验。键位与悬浮编辑器一致：Enter 换行，Shift+Enter 运行并发送；发送前覆盖框自动让位，并对真实输入框中心补一次合成点击（保证粘贴与回车落在真正的第一响应者上）。

定位三级降级（自动，启动与运行日志均记录）：**T1** 无障碍 AX 焦点元素（微信为自绘 UI，是否暴露焦点文本框以实机探测为准）→ **T2** 窗口边界 + 点偏移模型（常量 `list_w/right_m/box_h/bottom_m`，单位为点；由录屏逐帧测量推算，`--overlay-tune` 校准后保存 overlay_calib.json 持久生效）。Esc 或 ⌘⌥E 暂停覆盖（微信切走再切回自动恢复）。

已知边界：微信 4.x 失焦时会自动收起窗口——实测（2026-11-20）**窗口其实不会收起**，仅前台轮询需要区分"微信前台/本应用前台/无关应用前台"三种情形；T2 推算矩形若与实际输入框有偏差，用 `--overlay-tune` 校准一次即可（overlay_calib.json 持久生效，不入库）。另：覆盖框为**无边框浮动面板形态**——覆盖框必须能取得键盘焦点，而 overrideredirect 无边框窗口无法成为 key window（实测：点击有 Button-1 事件但永远无 FocusIn，canBecomeKey 补丁也无效——该类窗口根本不参与 key 竞选），故用 Tk 官方留给 aqua 的 `::tk::unsupported::MacWindowStyle` 声明为 **floating 类（NSPanel 浮动面板）** 并以 objc swizzle 兜底；若仍出现无法输入请反馈并把 pyin.log 发来。

## 首次使用必读：辅助功能授权

把运行结果"打进"微信靠的是**系统级按键模拟**（CGEventPost 或 osascript keystroke，两者同源），必须授权：

1. 系统设置 → 隐私与安全性 → **辅助功能**
2. 把**运行 pyin 的宿主 App**（iTerm/Terminal/VSCode 等——即谁在终端里跑 `run.sh`，就加谁）打开开关
3. 重启 pyin。启动横幅会显示授权探测结果；未授权时：热键创建失败自动禁用、真实注入会失败——先用 `--dry-run` 体验
4. **两个易踩的坑**（2026-11-17 实测）：
   - **授权按宿主 App 归因**：从 iTerm 启动就授权 iTerm，从 VSCode 启动就授权 VSCode——换启动方式要重新授权。拿不准就跑 `./run.sh --doctor`，它直接告诉你当前这次启动能不能注入；
   - **macOS 把"监听"与"注入"拆成两项独立授权**（本机实测）：`CGEventTapCreate`（ListenOnly，热键监听）走"输入监控"域，即使辅助功能未授权也可能创建成功——**热键能呼出不代表注入可用**，以 `--doctor` 的"辅助功能授权"一行为准；
   - **合成按键要挂显式 HID 事件源**（2026-11-17 探针实测）：无源 `CGEventCreateKeyboardEvent(None, …)` 的 Return 会被 WeChat 忽略（而粘贴的 Cmd+V 能到达）——挂 `kCGEventSourceStateHIDSystemState` 后即可正常触发发送；osascript 合成路径（`--send-engine osascript`）作为实测备用；
   - 注入链每一步都落 [pyin.log](pyin.log)（已 gitignore），状态栏报错时先看它。

## 工作原理与文件

单文件 [pyin.py](pyin.py)（四层，≈710 行）+ [run.sh](run.sh) 启动脚本：

| 层 | 函数/类 | 职责 |
|---|---|---|
| 执行层 | `run_code()` / `format_result()` | 子进程执行（`-u` 无缓冲；超时杀整个进程组后二次排空管道，保留已产生的输出）；结果**不加任何头原样直发**（成功→stdout、报错→traceback 原样、超时→已捕获输出），超 2000 字符中间截断 |
| 注入层 | `Injector` | 写剪贴板 → AppleScript 激活微信（仅已在运行时，避免顺手拉起）→ 核实前台 → `Cmd+V` → `Enter` → 恢复剪贴板 |
| GUI 层 | `Editor` | tkinter 置顶悬浮窗；Enter 换行（Text 默认）、Shift+Enter 运行、Esc 收起；所有 UI 更新经队列回主线程 |
| 热键层 | `start_hotkey()` | CGEventTap（ListenOnly，不吞键）监听 `⌘⌥E`；创建失败（未授权/无 pyobjc）只禁用热键 |

可调常量（pyin.py 顶部）：`RUN_TIMEOUT=5.0`（运行超时秒）、`FOCUS_DELAY=0.5` / `PASTE_DELAY=0.5`（激活/粘贴→回车时延，与探针实测一致）、`CLIPBOARD_RESTORE_DELAY=1.5`（剪贴板恢复）、`MAX_RESULT_CHARS=2000`（发送上限）、`HOTKEY_KEYCODE=14`（热键 = E 键）、`WECHAT_BUNDLE_IDS`。

## 验证记录（2026-11-17，ZCode 实测）

- ✔ `--selftest` 13 项全过：正常输出直发/报错 traceback 原样/超时已捕获输出直发/无输出跳过/stderr 续接/超长截断/注释-only 判定/中文 emoji 透传——venv（含 pyobjc）与降级（无 pyobjc）两套解释器各跑一遍
- ✔ dry-run 注入链：成功与报错两种格式化输出按预期打印
- ✔ GUI 冒烟：窗口 6s 存活无异常；未授权探测（ctypes 调 `AXIsProcessTrusted`）、热键降级路径、后端横幅均正确
- ✔ **送达验证（2026-11-17，作者实测 `--probe-send`）**：探针 **A**（CGEvent Return + 显式 HID 源）与 **B**（osascript key code 36）均成功发送进微信聊天；**C**（⌘+Return）与 **D**（只粘贴基线）留在输入框。结论：①辅助功能授权后注入链全通；②**无源合成 Return 会被 WeChat 忽略（粘贴的 Cmd+V 却能到达）——显式 HID 事件源是默认路径能发送的关键修复**；③⌘+Enter 不是 WeChat 的发送组合键。默认配置（`enter` + `cgevent`）即探针验证的组合：写代码 → Shift+Enter → 运行 → 输出送达微信并自动发送，链路打通。

## 已知限制（多为设计假设，均已在代码/本文标注）

1. **不检测焦点**（档位 A 的定义）：发送目标 = "激活微信后获得焦点的输入框"。请先把微信停在目标聊天。
2. **追加而非清空**：微信输入框里已有文字时，结果会**追加**到光标处（原型不读取、不清空原框——微信输入框为自绘控件，读取不可依赖，见调研笔记 §3.4）。
3. **发送键假设**：默认按 Enter 发送。**若输出进了输入框但变成换行没发出去**（2026-11-17 第五/六轮实机反馈）：合成事件使用**私有事件源**（`kCGEventSourceStatePrivate`）并无条件显式设置 flags——因为 `CGEventCreateKeyboardEvent` 会继承事件源当时的修饰键状态（Apple 文档行为），编辑器路径刚按完 Shift+Enter 的修饰键残留可能让 Return 被微信按惯例解释为换行（Shift+Enter 正是微信的换行快捷键）；探针路径双手离键所以始终干净。粘贴→回车时延与探针一致（0.5s）。仍异常时跑 `--probe-send` 定位。
4. **无跨次变量保留**：每次运行是独立子进程（防卡死/防误操作，安全性优先）。
5. **报错也发送**（想法原述明确要求）：报错内容可能含路径等敏感信息；调研笔记 §3.6 的"撤回窗口"尚未实现——建议先小范围实测。
6. **剪贴板覆盖窗口**：发送后 1.5s 内你复制的内容会被恢复动作覆盖。
7. **IME 组字期间 Shift+Enter** 行为未验证（中文注释输入后建议先按 Esc 退出组字态）。
8. **微信版本未逐一验证**：本原型只用"激活 + 系统按键"，不依赖微信内部 API，理论上各版本通用；实测结果欢迎补充进本文件。
9. **无输出不发送**：代码没产生任何输出时（含超时且无输出），跳过注入与回车——空消息发不出去，且回车会把输入框里可能残留的草稿误发出去。

## 出处与依据（AGENTS.md 引用规范）

- **方案**：可行性调研（作者工作区文档《调研笔记》，未随仓库分发）§1.1 档位 A（悬浮编辑器 + 显式触发 + 剪贴板注入 + 回车发送）与 §4 MVP 路线（本原型 = W1+W2 的 macOS 版）；注入先例 Espanso（espanso.org）。
- **Apple API**（非工作区来源，官方文档）：CGEventTap / CGEventPost 键位常量（kVK_ANSI_V=9、kVK_Return=36、kVK_ANSI_E=14）；`AXIsProcessTrusted` 经 ctypes 直调 ApplicationServices 框架。
- **微信行为假设**（工作区内无核验依据，未逐版本验证）："按 Enter 发送消息"默认开启、粘贴可进入输入框、激活后焦点回到输入框。
- **键位与首批范围**（作者 2026-11-17 指定）：Python 语言、微信目标、Enter=换行 / Shift+Enter=运行并发送、平台 macOS、**结果不加任何头原样直发**。
