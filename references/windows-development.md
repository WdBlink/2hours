# Windows development and use

Windows 10/11 x64 can run the full report, review, screen/microphone, local transcription and dispatch workflow without WSL or Swift.

## Install

Install Git, Node.js 20+, uv and ffmpeg (for example `winget install Gyan.FFmpeg`). In the checkout:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/windows-dev.ps1 setup
# If your network requires an HTTP proxy, append: -Proxy http://127.0.0.1:7897
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/windows-dev.ps1 doctor
```

Setup creates .venv, installs pinned Python dependencies, renderer dependencies, Cubox CLI and the local SenseVoice model. The Cubox CLI archive and the model files are checked against pinned SHA-256 values; a mismatch stops setup and nothing is installed from that download. It never starts screen/microphone capture. Model files and recorder settings live in ~/.2hours/recorder; private runs stay outside Git. The launcher sets UTF-8 mode and discovers winget-installed ffmpeg. For direct Python commands use .venv/Scripts/python.exe and set PYTHONUTF8=1.

## Use

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/windows-dev.ps1 demo
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/windows-dev.ps1 serve
```

Open the printed localhost URL yourself. `demo` preserves an existing report. To use an existing private run append `-Run C:\absolute\run`. Start recording from the report page and choose the report area. Tk provides pause, resume, marker, discard and stop controls. Do not overwrite an existing recording directory. Recording requires the user's explicit action. Windows uses screen pixels and microphone audio; macOS-native system audio is not supported and the CLI rejects that option.

The installed Codex Skill is named `$2hours`. Its Windows section uses the Skill's own virtual environment. The host must provide list/read/send/create thread tools; the Python service only prepares approved calls and records actual host receipts, never sends messages itself. Human confirmation remains required unless the user explicitly enables automatic dispatch in settings. Routine development uses Git branches and PRs; configure your own user.name/user.email and GitHub login.

Cubox is optional. If you have an account, run vendor/cubox/cli/bin/cubox-cli.exe auth login locally, never paste tokens into chat. Without an account, report Cubox as not_configured and continue with Codex evidence. Missing login is not an empty collection.

## Safety and verification

Windows dispatch uses byte-range locks, pinned directories and link-safe lock handles. Atomic JSON replacement flushes the file; Windows does not support the Unix directory-fsync step. Retention cleanup requires preview and rechecks the review under locks. It pins every ancestor and deletes the exact opened media handle; junctions, reparse points, hardlinks, changed/busy files and active/unreviewed recordings are preserved. UNC/network cleanup fails closed.

The resource guard runs the target in a kill-on-close Windows Job Object. A small launcher joins the job before it starts the target, and the job does not allow breakaway, so processes the target creates directly or through its children stay in the job and are terminated when the guard closes it, even if their parent has already exited. This does not cover processes started on the target's behalf by an out-of-job broker (for example WMI, Task Scheduler, COM/shell activation or a service); those are outside the job and are neither limited nor terminated by the guard. Python start-up hooks of the launcher (site/.pth) run before it joins the job. RAM, system commit, pagefile growth and stage memory are sampled; RAM/commit at 90% pauses work. Unsupported compressor/page-out counters are null. As with macOS, brief spikes between samples may be missed. Guarded commands require psutil, included in setup and PEP723 metadata.

Native tests cover handle-safe deletion and races, real Job Object tree cleanup, real FFmpeg/Tk synthetic pause/resume/marker/stop and Unicode paths. On this machine an actual 10.36-second screen/microphone recording, Chinese SenseVoice transcription, frame extraction and an explicitly authorized same-chat native dispatch succeeded. Local acceptance files are under runs/ and are not committed. Browser automation was denied by host policy; page operations were verified through HTTP tests, and opening the page is manual.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/windows-dev.ps1 test
node --check assets/bridge.js
node --check assets/board.js
node --test vendor/traceink/test/*.test.mjs
```

macOS Swift-only tests are skipped on Windows. True symlink tests need Developer Mode or symlink privileges; native Windows junction/hardlink tests run without changing security settings. Platform-specific Unix rename-injection tests have Windows handle-contention equivalents.
