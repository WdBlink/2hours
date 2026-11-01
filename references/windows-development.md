# Windows development (initial support)

Use Python 3.10+, Node.js 20+, uv and ffmpeg. The development checkout does not need to be installed as a global Skill.

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe tzdata sounddevice
npm.cmd ci --prefix vendor/answer-me-with-html/source
node vendor/cubox/cli/scripts/install.js
powershell -ExecutionPolicy Bypass -File scripts/windows-dev.ps1 demo
powershell -ExecutionPolicy Bypass -File scripts/windows-dev.ps1 serve
```

`demo` preserves an existing runs/demo directory. `serve` opens the review service on localhost. Python UTF-8 mode is required for Chinese content when subprocess output is captured on Windows. The launcher enables it and finds winget-installed ffmpeg.

The review service and dispatch preparation use Windows byte-range locks instead of fcntl. JSON writes flush the temporary file before atomic replacement; Windows does not support the Unix directory-fsync step. Dispatch still requires the same user approvals and host tools; the server does not send messages itself.

Initial validation on Windows: example report rendering, HTTP service startup, all 38 dispatch tests, and two process-lock/Unicode-write tests passed. The complete suite is not yet green: macOS path assumptions, Unix resource monitoring, retention cleanup and symlink privileges remain.

Known limits:

- Retention cleanup is disabled on Windows and preserves recordings. Its Unix descriptor-relative deletion protections require a Windows-specific implementation.
- `resource_guard.py` remains a macOS-only memory guard and refuses unsupported platforms.
- The bundled recorder already selects Tk/ffmpeg on Windows. Actual screen/microphone capture and transcription have not been validated here. macOS Swift build commands do not apply.
- Symlink tests need Windows Developer Mode or appropriate privileges.
- Cubox requires a successful official binary download and local account login. Speech transcription requires a separately configured backend.

For collaboration configure your own `git config --local user.name` and `user.email`, authenticate with GitHub, and use feature branches plus pull requests. This setup creates no commits or pushes.
