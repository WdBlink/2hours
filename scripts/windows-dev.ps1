param(
    [ValidateSet('setup', 'doctor', 'demo', 'serve', 'test', 'asr', 'record')]
    [string]$Action = 'doctor',
    [string]$Run = 'runs/demo',
    [string]$Proxy
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot
$env:PYTHONUTF8 = '1'
$env:UV_LINK_MODE = 'copy'
$env:Path += ";$env:USERPROFILE\.local\bin;C:\Program Files\Git\cmd"
$ffmpeg = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Gyan.FFmpeg*\*\bin\ffmpeg.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($ffmpeg) { $env:Path += ";$($ffmpeg.DirectoryName)" }
if ($Proxy) { $env:HTTPS_PROXY = $Proxy; $env:HTTP_PROXY = $Proxy }
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
function Check-Exit {
    if ($LASTEXITCODE -ne 0) { throw "Command failed (exit $LASTEXITCODE); see output above." }
}
if ($Action -eq 'setup') {
    if (!(Get-Command uv -ErrorAction SilentlyContinue)) { throw 'Install uv first: https://docs.astral.sh/uv/' }
    if (!(Get-Command node -ErrorAction SilentlyContinue)) { throw 'Install Node.js 20+ first.' }
    if (!(Get-Command ffmpeg -ErrorAction SilentlyContinue)) { throw 'Install ffmpeg first: winget install Gyan.FFmpeg' }
    if (!(Test-Path $python)) { uv venv --python 3.12 .venv; Check-Exit }
    uv pip install --python $python -r requirements-windows.txt
    Check-Exit
    npm.cmd ci --prefix vendor/answer-me-with-html/source
    Check-Exit
    if (!(Test-Path vendor/cubox/cli/bin/cubox-cli.exe)) { node vendor/cubox/cli/scripts/install.js; Check-Exit }
    & $python vendor/blurt/scripts/transcribe.py setup --backend sensevoice
    Check-Exit
} elseif (!(Test-Path $python)) { throw 'Run scripts/windows-dev.ps1 setup first.' }
switch ($Action) {
    { $_ -in 'setup', 'doctor' } {
        & $python scripts/run.py doctor
        Check-Exit
        & $python vendor/blurt/scripts/doctor.py
    }
    'demo' {
        if (!(Test-Path $Run)) {
            New-Item -ItemType Directory -Force $Run | Out-Null
            Copy-Item examples/review/* $Run
        }
        if (!(Test-Path (Join-Path $Run 'report.html'))) { & $python scripts/run.py render --run $Run }
        else { Write-Output "Existing report preserved: $Run/report.html" }
    }
    'serve' { & $python scripts/review_bridge.py --run $Run }
    'test' { & $python -B -m unittest discover -s tests -v }
    'asr' { & $python vendor/blurt/scripts/transcribe.py run (Join-Path $Run 'blurt/recording.mp4') --out (Join-Path $Run 'blurt') }
    'record' { & $python vendor/blurt/scripts/record.py start --session (Join-Path $Run 'blurt') }
}
exit $LASTEXITCODE
