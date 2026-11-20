# pyin · Desktop Prototype of the "A More Interesting (Perhaps) Input Method" Idea (macOS · WeChat · Python)

> Idea source: author's original statement (2026-11-13, documented in the author's workspace — not shipped in this repo) · Design basis: feasibility study §1.1 Tier A / MVP (injection precedent: Espanso) · Implementation log: feasibility study §6
> Written by ZCode (coding agent, model GLM-5.3-Flash), 2026-11-17 · 中文: [README.md](README.md)

## Demo video

<video src="assets/demo.mp4" controls muted playsinline width="100%"></video>

*Write Python in the floating editor → Shift+Enter to run → the output (errors included) lands in WeChat; sensitive regions are covered with a meme, per box. If your viewer doesn't play inline: [assets/demo.mp4](assets/demo.mp4).*

## What is this

The idea's **Tier A (MVP)** realized on macOS + WeChat: an always-on-top floating code editor. You write Python in it —

| Step | Behavior | Key / mechanism |
|---|---|---|
| 1. Summon | Floating always-on-top editor appears | Global hotkey `⌘⌥E` (or click the window / Dock) |
| 2. Write code | **Enter = newline** (write multi-line Python as usual) | Keybinding specified by the author, 2026-11-17 |
| 3. Run | **Shift+Enter = run**: child process `python -`, stdout/stderr captured, killed after 5s timeout | Explicit trigger — never "execute while typing" |
| 4. Inject | The run result (**normal output or an error — both count**, per the original idea) is sent **as-is with no header** → clipboard → activate WeChat → `Cmd+V` paste | Verifies WeChat is actually frontmost after activation, **aborts otherwise**; skipped entirely when there is no output; the editor **always hides before injecting** (injection is unreliable while the editor sits in front — observed 2026-11-17) |
| 5. Send | Simulated `Enter` → message sent; the editor re-opens automatically **1 second after the send completes**, ready for the next snippet | Original clipboard restored (after 1.5s) |

## Running

```bash
./run.sh               # Default mode = in-place overlay: the input box sits directly on WeChat's real input box, persistent
./run.sh --overlay-tune  # Calibration: nudge the orange frame until it exactly matches the real input box, then save
./run.sh --overlay-probe # Read-only probe: prints WeChat window bounds and input-area location
./run.sh --editor      # Floating editor mode (the v0.1 free-position always-on-top editor)
./run.sh --dry-run     # No real injection: run+format as usual, injection prints to the terminal instead
./run.sh --selftest    # Offline self-test (no GUI / permission / WeChat needed)
./run.sh --doctor      # Read-only check: permission/backend/WeChat/frontmost — run this when nothing gets sent
./run.sh --send-key cmd_enter    # Change the send key: enter (default) / cmd_enter / ctrl_enter
./run.sh --send-engine osascript  # Change the injection engine for the send key (CGEvent / osascript paths)
./run.sh --probe-send  # Send probe: injects 4 tagged probes into a test chat to find which strategy sends (real injection)
python3 pyin.py        # Degraded mode without .venv (no hotkey; injection via osascript, functionally equivalent)
```

See the next section "In-place overlay mode" for the default mode.

- The interpreter is whatever Python launches pyin; which third-party libraries you can import depends on it (the prototype itself needs only the standard library plus macOS built-ins `pbcopy`/`pbpaste`/`osascript`).
- `.venv/` is gitignored. To rebuild: `python3 -m venv .venv && .venv/bin/pip install pyobjc-framework-Quartz`.

## In-place overlay mode (default · research tier B)

`./run.sh` enters this mode directly; use `--editor` for the floating editor. Calibration workflow:

```bash
./run.sh --overlay-tune    # nudge the orange frame with arrows/Shift+arrows, Enter saves overlay_calib.json
```

A borderless input box **same size, same place** as WeChat's real input box (dark simulated style by default, `⌘G` toggles a 45% ghost form). It shows when WeChat is frontmost, hides when it isn't, and follows the box as it moves — an always-on in-place replacement. Keys match the floating editor: Enter newline, Shift+Enter run-and-send; before sending, the overlay steps aside and a synthetic click is placed on the center of the real input box (guaranteeing first-responder status for paste and Enter).

Positioning falls back automatically (logged at startup and while running): **T1** Accessibility focused element (WeChat is custom-drawn; whether it exposes a focused text box is determined empirically) → **T2** window bounds + point-offset model (constants `list_w/right_m/box_h/bottom_m`, in points; derived from frame-by-frame measurements of the recording, and persisted via overlay_calib.json once calibrated with `--overlay-tune`). Esc or ⌘⌥E pauses the overlay (it resumes automatically after WeChat leaves and returns to the front).

Known edge: an unfocused WeChat window does **not** collapse — measured on 2026-11-20 — so the real input box stays visible under the overlay. The frontmost-polling distinguishes three cases (WeChat front / our own app front / unrelated app front). If the T2 rect is misaligned with the real input box, run `--overlay-tune` once; overlay_calib.json persists and is gitignored. Also: the overlay carries a slim titlebar — macOS borderless windows cannot become the key window (measured; the tune window was first built borderless and could not be focused at all): the titlebar hovers 28pt above the input box, and the content area matches the real input box exactly.

## First run: Accessibility permission (required)

Typing the result "into" WeChat relies on **system-level key injection** (CGEventPost or osascript keystroke — same trust requirement):

1. System Settings → Privacy & Security → **Accessibility**
2. Enable the switch for the **host app that runs pyin** (iTerm/Terminal/VSCode… — whichever app runs `run.sh` in a terminal)
3. Restart pyin. The startup banner reports the detected trust state; without trust, the hotkey self-disables and real injection fails — use `--dry-run` meanwhile.
4. **Two traps** (verified 2026-11-17):
   - **Trust is attributed per host app**: launching from iTerm requires trusting iTerm; from VSCode, VSCode — change the launcher and you must re-grant. Run `./run.sh --doctor` to see whether the current launch can inject at all;
   - **macOS splits "listening" and "injecting" into two independent grants** (verified on this machine): `CGEventTapCreate` (ListenOnly, the hotkey listener) lives in the "Input Monitoring" domain and may succeed even without Accessibility — **a working hotkey does not mean injection works**; trust the "Accessibility" line of `--doctor`;
   - **Synthetic keys need an explicit HID event source** (probe-verified 2026-11-17): a source-less `CGEventCreateKeyboardEvent(None, …)` Return is ignored by WeChat (while the pasted Cmd+V arrives) — with `kCGEventSourceStateHIDSystemState` the send triggers normally; the osascript synthesis path (`--send-engine osascript`) is a verified fallback;
   - Every injection step lands in [pyin.log](pyin.log) (gitignored) — check it whenever the status bar shows an error.

## How it works & files

Single file [pyin.py](pyin.py) (four layers, ≈710 lines) plus the [run.sh](run.sh) launcher:

| Layer | Functions / class | Responsibility |
|---|---|---|
| Execution | `run_code()` / `format_result()` | Child-process execution (`-u` unbuffered; on timeout the whole process group is killed and the pipes are drained a second time, preserving output already produced); the result is sent **as-is with no header** (success→stdout, error→raw traceback, timeout→captured output), middle-truncated past 2000 chars |
| Injection | `Injector` | Clipboard write → AppleScript activates WeChat (only if already running, so it never cold-launches) → frontmost check → `Cmd+V` → `Enter` → clipboard restore |
| GUI | `Editor` | tkinter always-on-top window; Enter newline (Text default), Shift+Enter run, Esc hide; all UI updates routed to the main thread via a queue |
| Hotkey | `start_hotkey()` | CGEventTap (ListenOnly, keys pass through) listening for `⌘⌥E`; on creation failure (no trust / no pyobjc) only the hotkey is disabled |

Tunable constants (top of pyin.py): `RUN_TIMEOUT=5.0`, `FOCUS_DELAY=0.5` / `PASTE_DELAY=0.5` (activation / paste→Enter delays, matching the probe), `CLIPBOARD_RESTORE_DELAY=1.5`, `MAX_RESULT_CHARS=2000`, `HOTKEY_KEYCODE=14` (hotkey = the E key), `WECHAT_BUNDLE_IDS`.

## Verification log (2026-11-17, tested by ZCode)

- ✔ `--selftest` 13/13: normal output sent as-is / error sent as raw traceback / timeout sends already-captured output / no output skips sending / stderr continuation / long-output truncation / comment-only guard / CJK + emoji passthrough — run on both the venv interpreter (with pyobjc) and the degraded one (without)
- ✔ dry-run injection chain: success and error formatted outputs print as expected
- ✔ GUI smoke test: window alive 6s with no exceptions; untrusted-permission detection (ctypes call to `AXIsProcessTrusted`), hotkey degradation path, and backend banner all correct
- ✔ **Delivery verified (2026-11-17, author ran `--probe-send`)**: probe **A** (CGEvent Return + explicit HID source) and **B** (osascript key code 36) both sent successfully into the WeChat chat; **C** (⌘+Return) and **D** (paste-only baseline) stayed in the input box. Conclusions: ① with Accessibility granted, the whole injection chain works; ② **a source-less synthetic Return is ignored by WeChat (while the pasted Cmd+V arrives) — the explicit HID event source is the key fix for the default path**; ③ ⌘+Enter is not a WeChat send shortcut. The default configuration (`enter` + `cgevent`) is the probe-verified combo: write code → Shift+Enter → run → output delivered to WeChat and auto-sent. End to end works.

## Known limitations (mostly design assumptions, annotated in code and here)

1. **No focus detection** (by Tier A definition): the send target is "whatever input box in WeChat has focus after activation". Leave WeChat on the intended chat first.
2. **Appends, never clears**: existing text in the WeChat input box stays; the result is pasted at the cursor (the prototype never reads or clears the box — WeChat's input is a custom-drawn widget, reading it is unreliable; see research notes §3.4).
3. **Send-key assumption**: the default is Enter-to-send. **If the output lands in the input box as a newline instead of sending** (observed in the 2026-11-17 test rounds): synthetic events use a **private event source** (`kCGEventSourceStatePrivate`) with flags always set explicitly — `CGEventCreateKeyboardEvent` inherits the event source's current modifier state (documented Apple behavior), so modifier residue from the Shift+Enter you just typed could make WeChat interpret the Return as a newline (Shift+Enter is WeChat's newline shortcut); the probe path always worked because your hands were off the keyboard. The paste→Enter delay matches the probe (0.5s). If anything is still off, run `--probe-send`.
4. **No state across runs**: every run is a fresh child process (hanging code can't freeze the editor; side effects stay contained).
5. **Errors are sent too** (explicitly required by the original idea): tracebacks may contain paths or other sensitive strings; the "recall window" from research notes §3.6 is not implemented — test in a small chat first.
6. **Clipboard clobber window**: anything you copy within 1.5s after sending gets overwritten by the restore step.
7. **IME composition**: Shift+Enter behavior while an IME composition is active is unverified (finish/commit the composition first).
8. **WeChat versions unverified**: the prototype only uses "activate + system keys", no WeChat-internal APIs, so it should work across versions; please feed real-world results back into this file.
9. **No output, no send**: when the code produces no output at all (including a silent timeout), injection and Enter are skipped — an empty message cannot be sent, and pressing Enter could mis-fire leftover draft text.

## Sources & basis (per AGENTS.md citation rules)

- **Design**: feasibility study (author's workspace doc "调研笔记", not shipped in this repo) §1.1 Tier A (floating editor + explicit trigger + clipboard injection + Enter-to-send) and §4 MVP plan (this prototype = the macOS implementation of W1+W2); injection precedent Espanso (espanso.org).
- **Apple APIs** (non-workspace sources, official docs): CGEventTap / CGEventPost and keycodes (kVK_ANSI_V=9, kVK_Return=36, kVK_ANSI_E=14); `AXIsProcessTrusted` called via ctypes against the ApplicationServices framework.
- **WeChat behavior assumptions** (no workspace verification, unverified across versions): "Enter sends the message" on by default, paste lands in the input box, activation restores input focus.
- **Scope & keybindings** (specified by the author, 2026-11-17): Python language, WeChat as target, Enter=newline / Shift+Enter=run-and-send, platform macOS, **results sent as-is with no header**.
