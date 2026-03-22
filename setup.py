"""
setup.py -- CL34N post-install setup.

Run by install.ps1 after Python and FFmpeg are bootstrapped.
Handles everything Python can do: packages, run.bat, context menu, uninstaller.
"""

import subprocess
import sys
import winreg
from pathlib import Path


# Derive paths from where this script lives (APP_DIR) and the Python that runs it.
APP_DIR   = Path(__file__).resolve().parent
PY_EXE    = Path(sys.executable)
ROOT      = PY_EXE.parent.parent          # python.exe lives at ROOT\python\python.exe
FF_DIR    = ROOT / 'ffmpeg'
MODELS_DIR = ROOT / 'models'
RUN_BAT   = ROOT / 'run.bat'
UNINSTALL = ROOT / 'uninstall.ps1'

EXTENSIONS = ['.mp4', '.mkv', '.mov', '.avi', '.mp3', '.wav', '.m4a', '.flac']

ORT_FEED = (
    'https://aiinfra.pkgs.visualstudio.com/PublicPackages/_packaging/'
    'ort-cuda-13-nightly/pypi/simple/'
)


def _pip(*packages):
    subprocess.check_call(
        [str(PY_EXE), '-m', 'pip', 'install', '--quiet', '--no-warn-script-location', *packages],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _bar(step, total, label):
    width = 20
    filled = int(width * step / total)
    bar = '#' * filled + '-' * (width - filled)
    print(f'  [{bar}]  {step}/{total}  {label}    ', end='\r', flush=True)


def install_packages():
    steps = [
        ('Audio packages',  ['numpy', 'soundfile', 'librosa', 'soxr', 'resampy']),
        ('CUDA runtime',    ['nvidia-cudnn-cu12']),
        ('AI runtime',      ['--pre', 'onnxruntime-gpu', '--extra-index-url', ORT_FEED]),
    ]
    for i, (label, pkgs) in enumerate(steps, 1):
        _bar(i, len(steps), label)
        _pip(*pkgs)
    print(f'  [{"#" * 20}]  {len(steps)}/{len(steps)}  Done!              ')


def write_run_bat():
    cudnn_bin  = PY_EXE.parent / 'Lib' / 'site-packages' / 'nvidia' / 'cudnn'  / 'bin'
    cublas_bin = PY_EXE.parent / 'Lib' / 'site-packages' / 'nvidia' / 'cublas' / 'bin'
    app_script = APP_DIR / 'cl34n.py'

    bat = (
        '@echo off\n'
        'title CL34N\n'
        f'set "PATH={FF_DIR};%PATH%"\n'
        f'set "PATH={cudnn_bin};%PATH%"\n'
        f'set "PATH={cublas_bin};%PATH%"\n'
        'set "PYTHONUTF8=1"\n'
        f'"{PY_EXE}" "{app_script}" %*\n'
        'echo.\n'
        'if %errorlevel% equ 0 (\n'
        '    echo   Done! Press any key to close...\n'
        ') else (\n'
        '    echo   Something went wrong. Check the output above.\n'
        ')\n'
        'pause > nul\n'
    )
    RUN_BAT.write_text(bat, encoding='ascii')
    print(f'  OK  run.bat -> {RUN_BAT}')


def register_context_menu():
    command = f'cmd.exe /c ""{RUN_BAT}" "%1""'
    for ext in EXTENSIONS:
        key = rf'Software\Classes\SystemFileAssociations\{ext}\shell\CL34N'
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
            winreg.SetValueEx(k, 'MUIVerb', 0, winreg.REG_SZ, 'Remove Music (CL34N)')
            winreg.SetValueEx(k, 'Icon',    0, winreg.REG_SZ, str(PY_EXE))
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key + r'\command') as k:
            winreg.SetValueEx(k, '', 0, winreg.REG_SZ, command)
    print(f'  OK  context menu registered for {" ".join(EXTENSIONS)}')


def write_uninstaller():
    ext_list = ', '.join(f"'{e}'" for e in EXTENSIONS)
    script = f"""\
# CL34N Uninstaller
# Run with: powershell -ExecutionPolicy Bypass -File uninstall.ps1

$ROOT       = '{ROOT}'
$EXTENSIONS = @({ext_list})

Write-Host ""
Write-Host "  Uninstalling CL34N..." -ForegroundColor Cyan
Write-Host ""

foreach ($ext in $EXTENSIONS) {{
    $key = "HKCU:\\Software\\Classes\\SystemFileAssociations\\$ext\\shell\\CL34N"
    if (Test-Path $key) {{ Remove-Item -Path $key -Recurse -Force }}
}}
Write-Host "  Context menu removed." -ForegroundColor White

foreach ($sub in @('python', 'ffmpeg', 'app')) {{
    $d = Join-Path $ROOT $sub
    if (Test-Path $d) {{ Remove-Item $d -Recurse -Force -ErrorAction SilentlyContinue }}
}}
foreach ($f in @('run.bat', 'uninstall.ps1')) {{
    $p = Join-Path $ROOT $f
    if (Test-Path $p) {{ Remove-Item $p -Force -ErrorAction SilentlyContinue }}
}}
if (-not (Get-ChildItem $ROOT -ErrorAction SilentlyContinue)) {{
    Remove-Item $ROOT -Force -ErrorAction SilentlyContinue
}}

$modelsDir = Join-Path $ROOT 'models'
if (Test-Path $modelsDir) {{
    Write-Host ""
    Write-Host "  Your downloaded models were kept at:" -ForegroundColor Yellow
    Write-Host "    $modelsDir" -ForegroundColor Cyan
    Write-Host "  Delete that folder manually to free the space." -ForegroundColor Yellow
}}

Write-Host ""
Write-Host "  CL34N has been uninstalled." -ForegroundColor Green
Write-Host ""
"""
    UNINSTALL.write_text(script, encoding='utf-8')
    print(f'  OK  uninstaller -> {UNINSTALL}')


MODELS_DIR.mkdir(parents=True, exist_ok=True)

install_packages()
write_run_bat()
register_context_menu()
write_uninstaller()

print('\n  Setup complete.\n')
