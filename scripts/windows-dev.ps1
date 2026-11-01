param([ValidateSet('doctor', 'demo', 'serve', 'test')][string]$Action = 'demo')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot
$env:PYTHONUTF8 = '1'
$env:Path += ";$env:USERPROFILE\.local\bin;C:\Program Files\Git\cmd"
$ffmpeg = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Gyan.FFmpeg*\*\bin\ffmpeg.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($ffmpeg) { $env:Path += ";$($ffmpeg.DirectoryName)" }
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (!(Test-Path $python)) { throw 'Create .venv first: uv venv --python 3.12 .venv' }
switch ($Action) {
    'doctor' { & $python scripts/run.py doctor }
    'demo' {
        if (!(Test-Path runs/demo)) {
            New-Item -ItemType Directory runs/demo | Out-Null
            Copy-Item examples/review/* runs/demo
        }
        & $python scripts/run.py render --run runs/demo
    }
    'serve' { & $python scripts/review_bridge.py --run runs/demo --open }
    'test' { & $python -B -m unittest discover -s tests -v }
}
exit $LASTEXITCODE
