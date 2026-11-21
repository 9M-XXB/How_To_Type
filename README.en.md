# pyin — Send your Python output straight into a WeChat chat

> The "A More Interesting (Perhaps) Input Method" desktop prototype: an invisible code input box that sits on top of WeChat's real input box — write Python, press Shift+Enter, and the **output (errors included) becomes the chat message**.
> Fully tested on macOS · Windows / Linux are first releases (untested) · Zero third-party dependencies (optional pyobjc on macOS) · Python standard library + native system APIs only
> Author: 9M-XXB · Implementation & research: ZCode (GLM-5.3-Flash) · 2026-11 · 中文: [README.md](README.md)

## Demo video

<video src="assets/demo.mp4" controls muted playsinline width="100%"></video>

*Write Python in the invisible overlay → Shift+Enter to run → output (errors included) lands in the WeChat chat. Sensitive regions are covered with a meme, per box. If your viewer doesn't play inline: [assets/demo.mp4](assets/demo.mp4).*

## Quick start

```bash
git clone https://github.com/9M-XXB/How_To_Type.git
cd How_To_Type

# macOS (venv + pyobjc recommended, enables global hotkey and CGEvent injection)
python3 -m venv .venv && .venv/bin/pip install pyobjc-framework-Quartz
./run.sh

# Windows (zero third-party dependencies)
python pyin.py

# Linux (needs X11/XWayland + xdotool + xclip)
./run.sh
```

Three steps to use:

1. **Permission** (macOS only; Windows needs none): System Settings → Privacy & Security → Accessibility → enable the terminal app that runs pyin (see [Permission & safety](#permission--safety)).
2. **Calibrate** (once): park WeChat on a chat → `./run.sh --overlay-tune` → click the orange frame → arrow keys to align → Enter to save.
3. **Use**: `./run.sh` → the overlay hugs WeChat's input box → click and type → **Shift+Enter** runs and sends.

## Three modes

| Mode | Command | Description |
|---|---|---|
| **In-place overlay** (default) | `./run.sh` | A borderless input box sits exactly on WeChat's real input box; shows with WeChat, hides otherwise, follows as it moves — persistent |
| Floating editor | `./run.sh --editor` | A free-position always-on-top editor (the v0.1 form) |
| Input-area calibration | `./run.sh --overlay-tune` | Align a translucent orange frame to the real input box, Enter to save (remembered per platform) |

Standalone tools:

| Command | Purpose |
|---|---|
| `--overlay-probe` | Read-only probe: prints WeChat window bounds and input-area location |
| `--doctor` | Read-only check: permission / backend / WeChat / frontmost — run this when nothing gets sent |
| `--selftest` | Offline self-test (no GUI / permission / WeChat needed) |
| `--dry-run` | No real injection: run + format as usual, injection prints to the terminal |
| `--probe-send` | Send probe: injects 4 tagged probes into a test chat to find a working send strategy (real injection) |
| `--send-key <k>` | Change the send key: enter (default) / cmd_enter / ctrl_enter |
| `--send-engine <e>` | Change the send-key injection engine: cgevent (default) / osascript (macOS only) |
| `--log-keys` | Log every keystroke to the diagnostics (default: non-printable keys only) |

## How it works (30-second version)

```
WeChat front ──► overlay hugs the input box ──► you write Python (Enter = newline)
   └── Shift+Enter ──► overlay steps aside ──► child-process run (5s timeout guard)
        ──► output, verbatim, to clipboard ──► activate WeChat + click the real input box
        ──► Ctrl/Cmd+V ──► Enter sends ──► your clipboard is restored
        ──► within 1s the overlay returns, cleared, ready for the next snippet
```

| Behavior | Rule |
|---|---|
| Output form | **No header, verbatim** — normal output and error tracebacks are both sent |
| No output | Nothing is sent (no paste, no Enter); the status bar tells you |
| Timeout | Killed after 5 s; output produced so far is still sent |
| Long output | Middle-truncated past 2000 characters |
| Failure | The overlay keeps your code for retry; details in `pyin.log` |

## Permission & safety

| Platform | Requirement | Notes |
|---|---|---|
| macOS | Accessibility (required) | Trust is attributed per **host app**: launching from iTerm requires trusting iTerm; change the launcher and re-grant |
| Windows | none | SendInput needs no permission |
| Linux | none | X11/XWayland; native Wayland sessions not supported yet |

Two traps (measured on macOS):

- **"Listening" and "injecting" are two independent grants**: a working hotkey does not mean injection works — trust the "Accessibility" line of `--doctor`.
- **Trust is attributed per host app**: not sure? Run `./run.sh --doctor`; it tells you whether the current launch can inject.

## Diagnostics & troubleshooting

Everything is persisted in **`pyin.log`** (gitignored): Tk callback exceptions, thread tracebacks, overlay click/focus events, every injection step. **Check it first, then send the relevant lines with your report.**

| Symptom | First check | Tool |
|---|---|---|
| Overlay missing / misaligned | Is WeChat frontmost; does the rect match | `--overlay-probe` → `--overlay-tune` |
| Clicking the overlay doesn't let you type | Look for `<FocusIn>` in the log | `pyin.log` (add `--log-keys`) |
| Output reached WeChat but wasn't sent | WeChat's "press Enter to send" setting; or change the send key | `--send-key cmd_enter` |
| Output was pasted into the overlay | Return-early race (fixed); if it recurs, check injection-time events | `pyin.log` |
| Nothing happens at all | Is the process alive | `--doctor` / `--selftest` |

## How it works

Four platform-agnostic layers + one platform layer (`native.py`):

| Layer | Responsibility |
|---|---|
| Execution | Child-process `python -` (`-u` unbuffered; on timeout the process group is killed and produced output is kept); result sent verbatim with no header, middle-truncated past 2000 chars |
| Injection | Clipboard write (round-trip verified) → activate WeChat + verify frontmost → paste chord → Enter → clipboard restore; explicit failure when unauthorized |
| Overlay | Borderless always-on-top input window: Enter newline, Shift+Enter run-and-send, Esc pause, ⌘G ghost form (45% alpha on macOS) |
| Positioning | T1 AX focused element (macOS only) → T2 window bounds + point-offset model (`list_w/right_m/box_h/bottom_m`) → calibration file |

Tunable constants (top of each file): run timeout, injection delays, clipboard-restore delay, send length cap, hotkey keycode, send key, positioning offsets.

## Cross-platform

| Platform | Injection & clipboard | Window positioning | Global hotkey | Permission |
|---|---|---|---|---|
| **macOS** | CGEventPost + private HID source + pbcopy/pbpaste | AX focused element (T1) + window heuristic (T2) | CGEventTap ⌘⌥E | Accessibility |
| **Windows** | SendInput + CF_UNICODETEXT clipboard (pure ctypes) | EnumWindows (title/class match) + T2 calibration | RegisterHotKey Ctrl+Alt+E | none |
| **Linux** | xdotool + xclip | xdotool search + T2 calibration | not yet | none (X11/XWayland) |

- Platform implementations live in [`native.py`](native.py); pyin and overlay dispatch via `native.PLATFORM`; the macOS paths stay in their original files.
- Calibration is saved per platform (sectioned in `overlay_calib.json`, gitignored).
- `cmd_enter` maps to `ctrl_enter` on Windows (no ⌘ there); Linux needs X11/XWayland.
- **Honest note**: the Windows / Linux branches follow the official APIs and have **not been tested on those platforms**; please report issues with `pyin.log`.

## Known limitations

**Input & sending**

1. No focus detection: the send target is whatever input box the overlay covers — park WeChat on the intended chat first.
2. WeChat's existing input text is never read or cleared; output is appended at the cursor.
3. The send key defaults to Enter and depends on WeChat's "press Enter to send" setting; change with `--send-key`.
4. **Errors are sent too** (explicitly required by the original idea): tracebacks may contain sensitive paths; a "recall window" is not implemented yet.
5. No output, no send (an empty message can't be sent, and Enter could mis-fire a draft).
6. Each run is a fresh child process: no state is kept across runs.

**Privacy & clipboard**

7. Anything you copy within 1.5 s after sending gets overwritten by the restore step.
8. Output and tracebacks may contain sensitive information — verify in a test chat first.

**Environment**

9. Shift+Enter behavior during IME composition is unverified.
10. WeChat versions are unverified one by one (the prototype only uses "activate + system keys").
11. The Windows / Linux branches have not been tested on those platforms (first releases).

## Verification log

The macOS host went through 9 rounds of iteration and verification (probe-verified delivery, race fixes, invisibility work); the full history lives in the upstream research notes §6.3. Inside this repo, `--selftest` (12 checks), `--doctor` and `--dry-run` are available for self-checking at any time.

## Sources & basis

- **Idea & design**: author's original statement (2026-11-13); feasibility study tiers A/B (author's workspace docs, not shipped in this repo); injection precedent [Espanso](https://espanso.org).
- **Platform APIs** (official docs, non-workspace sources): macOS CGEventTap / CGEventPost / AX / `AXIsProcessTrusted`; Windows SendInput / RegisterHotKey / clipboard API; Linux XTEST (via xdotool).
- **WeChat behavior assumptions** (unverified across versions): "press Enter to send" on by default, paste lands in the input box, activation restores input focus.
- **Keybindings & scope** (specified by the author, 2026-11-17): Python language, WeChat as target, Enter=newline / Shift+Enter=run-and-send, results sent verbatim with no header.
