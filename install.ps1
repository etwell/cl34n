#Requires -Version 5.1
<#
.SYNOPSIS
    CL34N Installer - Strips music from video/audio via right-click context menu.
.DESCRIPTION
    Installs to %LOCALAPPDATA%\CL34N\ with no admin rights required.
    Downloads isolated Python and FFmpeg -- nothing touches your system Python or PATH.
    Requires an NVIDIA GPU with CUDA 13.x drivers installed system-wide.
.NOTES
    Run with:  powershell -ExecutionPolicy Bypass -File install.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'


$PYTHON_VERSION = '3.11.9'
$PYTHON_URL     = "https://www.python.org/ftp/python/$PYTHON_VERSION/python-$PYTHON_VERSION-embed-amd64.zip"
$PYTHON_SHA256  = ''

$FFMPEG_VERSION = '7.1'
$FFMPEG_URL     = "https://github.com/GyanD/codexffmpeg/releases/download/$FFMPEG_VERSION/ffmpeg-$FFMPEG_VERSION-essentials_build.zip"
$FFMPEG_SHA256  = ''

$GET_PIP_URL    = 'https://bootstrap.pypa.io/get-pip.py'

$GITHUB_RAW     = 'https://raw.githubusercontent.com/etwell/cl34n/main'

$APP_NAME   = 'CL34N'
$ROOT       = Join-Path $env:LOCALAPPDATA $APP_NAME
$APP_DIR    = Join-Path $ROOT 'app'
$PY_DIR     = Join-Path $ROOT 'python'
$FF_DIR     = Join-Path $ROOT 'ffmpeg'
$PY_EXE     = Join-Path $PY_DIR 'python.exe'


function Write-Step { param([string]$T) Write-Host "  $T" -ForegroundColor White }
function Write-OK   { param([string]$T) Write-Host "  OK  $T" -ForegroundColor Green }
function Write-Fail { param([string]$T) Write-Host "  XX  $T" -ForegroundColor Red }


function Get-File {
    param([string]$Url, [string]$Dest, [string]$Label, [string]$Sha256 = '')
    Write-Step "Downloading $Label..."
    try {
        $wc = New-Object System.Net.WebClient
        $wc.DownloadFile($Url, $Dest)
    } catch {
        throw "Failed to download $Label`: $_"
    }
    if ($Sha256) {
        $hash = (Get-FileHash -Path $Dest -Algorithm SHA256).Hash
        if ($hash -ne $Sha256.ToUpper()) {
            Remove-Item $Dest -Force -ErrorAction SilentlyContinue
            throw "SHA256 mismatch for $Label"
        }
    }
    Write-OK "$Label"
}


function Test-Cuda {
    Write-Step "Checking NVIDIA GPU..."
    $nvSmi = $null
    foreach ($c in @('nvidia-smi', 'C:\Windows\System32\nvidia-smi.exe',
                      'C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe')) {
        if (Get-Command $c -ErrorAction SilentlyContinue) { $nvSmi = $c; break }
        if (Test-Path $c) { $nvSmi = $c; break }
    }
    if (-not $nvSmi) {
        Write-Host ""
        Write-Host "  -------------------------------------------------------" -ForegroundColor Red
        Write-Host "  NVIDIA GPU drivers not found." -ForegroundColor Red
        Write-Host "  -------------------------------------------------------" -ForegroundColor Red
        Write-Host ""
        Write-Host "  CL34N uses your NVIDIA GPU to remove music from files." -ForegroundColor White
        Write-Host "  You need to install the NVIDIA CUDA drivers first." -ForegroundColor White
        Write-Host ""
        Write-Host "  1. Go to this link and download the latest driver:" -ForegroundColor Cyan
        Write-Host "     https://www.nvidia.com/en-us/drivers/" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "  2. Run the installer, then restart your computer." -ForegroundColor White
        Write-Host ""
        Write-Host "  3. Run this installer again." -ForegroundColor White
        Write-Host ""
        Read-Host "  Press Enter to close"
        exit 1
    }
    $gpu    = (& $nvSmi --query-gpu=name          --format=csv,noheader 2>&1) | Select-Object -First 1
    $driver = (& $nvSmi --query-gpu=driver_version --format=csv,noheader 2>&1) | Select-Object -First 1
    Write-OK "GPU detected: $($gpu.Trim())"
}


function Install-Python {
    Write-Step "Setting up Python..."
    $tmp = Join-Path $env:TEMP 'cl34n_python.zip'
    Get-File -Url $PYTHON_URL -Dest $tmp -Label "Python" -Sha256 $PYTHON_SHA256
    if (Test-Path $PY_DIR) { Remove-Item $PY_DIR -Recurse -Force }
    New-Item -ItemType Directory -Path $PY_DIR -Force | Out-Null
    Expand-Archive -Path $tmp -DestinationPath $PY_DIR -Force
    Remove-Item $tmp

    $pthFile = Get-ChildItem $PY_DIR -Filter '*._pth' | Select-Object -First 1
    if (-not $pthFile) { throw "Python ._pth not found -- extraction failed." }
    (Get-Content $pthFile.FullName -Raw) -replace '#import site', 'import site' |
        Set-Content $pthFile.FullName

    Write-Step "Installing pip..."
    $getPip = Join-Path $env:TEMP 'cl34n_get_pip.py'
    (New-Object System.Net.WebClient).DownloadFile($GET_PIP_URL, $getPip)
    & $PY_EXE $getPip --quiet
    if ($LASTEXITCODE -ne 0) { throw "pip bootstrap failed" }
    Remove-Item $getPip
    Write-OK "Python ready"
}


function Install-FFmpeg {
    Write-Step "Setting up audio tools..."
    $tmp    = Join-Path $env:TEMP 'cl34n_ffmpeg.zip'
    $tmpDir = Join-Path $env:TEMP 'cl34n_ffmpeg_extract'
    Get-File -Url $FFMPEG_URL -Dest $tmp -Label "FFmpeg" -Sha256 $FFMPEG_SHA256
    if (Test-Path $tmpDir) { Remove-Item $tmpDir -Recurse -Force }
    Expand-Archive -Path $tmp -DestinationPath $tmpDir -Force
    Remove-Item $tmp
    $inner = Get-ChildItem $tmpDir -Directory | Select-Object -First 1
    if (-not $inner) { throw "Unexpected FFmpeg zip structure" }
    if (Test-Path $FF_DIR) { Remove-Item $FF_DIR -Recurse -Force }
    New-Item -ItemType Directory -Path $FF_DIR -Force | Out-Null
    Copy-Item (Join-Path $inner.FullName 'bin\ffmpeg.exe')  $FF_DIR
    Copy-Item (Join-Path $inner.FullName 'bin\ffprobe.exe') $FF_DIR
    Remove-Item $tmpDir -Recurse -Force
    Write-OK "Audio tools ready"
}


function Install-AppFiles {
    Write-Step "Downloading app files from GitHub..."
    New-Item -ItemType Directory -Path $APP_DIR -Force | Out-Null
    $wc = New-Object System.Net.WebClient
    foreach ($f in @('cl34n.py', 'mdx_infer.py', 'model_registry.py', 'setup.py')) {
        $wc.DownloadFile("$GITHUB_RAW/$f", (Join-Path $APP_DIR $f))
    }
    Write-OK "App files downloaded"
}


function Main {
    Clear-Host
    Write-Host ""
    Write-Host "  CL34N" -ForegroundColor Cyan
    Write-Host "  Removes music from video and audio files." -ForegroundColor White
    Write-Host ""
    Write-Host "  This will install CL34N on your computer (~4 GB)." -ForegroundColor Gray
    Write-Host "  It runs completely in the background and won't change" -ForegroundColor Gray
    Write-Host "  your system. No admin rights required." -ForegroundColor Gray
    Write-Host ""

    if (Test-Path $ROOT) {
        Write-Host "  CL34N is already installed." -ForegroundColor Yellow
        $choice = Read-Host "  Would you like to reinstall it? [y/N]"
        if ($choice -notmatch '^[yY]') { Write-Host "  Cancelled. Nothing was changed."; return }
    }

    $confirm = Read-Host "  Ready to install? [y/N]"
    if ($confirm -notmatch '^[yY]') { Write-Host "  Cancelled. Nothing was changed."; return }

    Write-Host ""
    Write-Host "  Installing... this will take a few minutes." -ForegroundColor Gray
    Write-Host ""
    $sw = [System.Diagnostics.Stopwatch]::StartNew()

    try {
        Test-Cuda
        New-Item -ItemType Directory -Path $ROOT -Force | Out-Null
        Install-Python
        Install-FFmpeg
        Install-AppFiles

        Write-Step "Finishing setup..."
        & $PY_EXE (Join-Path $APP_DIR 'setup.py')
        if ($LASTEXITCODE -ne 0) { throw "setup.py failed (exit $LASTEXITCODE)" }

        $sw.Stop()
        $elapsed = [math]::Round($sw.Elapsed.TotalMinutes, 1)

        Write-Host ""
        Write-Host "  -------------------------------------------------------" -ForegroundColor Green
        Write-Host "  CL34N is installed! ($elapsed min)" -ForegroundColor Green
        Write-Host "  -------------------------------------------------------" -ForegroundColor Green
        Write-Host ""
        Write-Host "  How to use it:" -ForegroundColor White
        Write-Host "    Right-click any video or audio file" -ForegroundColor Gray
        Write-Host "    Select  'Remove Music (CL34N)'" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "  Note: The first time you use it, it will download" -ForegroundColor Yellow
        Write-Host "  the AI model (~200 MB). After that it's instant." -ForegroundColor Yellow
        Write-Host ""
        Read-Host "  Press Enter to close"

    } catch {
        $sw.Stop()
        Write-Host ""
        Write-Host "  -------------------------------------------------------" -ForegroundColor Red
        Write-Host "  Installation failed." -ForegroundColor Red
        Write-Host "  -------------------------------------------------------" -ForegroundColor Red
        Write-Host ""
        Write-Host "  Error: $_" -ForegroundColor Gray
        Write-Host ""
        Write-Host "  Nothing was left on your computer." -ForegroundColor Yellow
        Write-Host ""

        foreach ($dir in @($PY_DIR, $FF_DIR, $APP_DIR)) {
            if (Test-Path $dir) { Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue }
        }
        $runBat    = Join-Path $ROOT 'run.bat'
        $uninstall = Join-Path $ROOT 'uninstall.ps1'
        if (Test-Path $runBat)    { Remove-Item $runBat    -Force -ErrorAction SilentlyContinue }
        if (Test-Path $uninstall) { Remove-Item $uninstall -Force -ErrorAction SilentlyContinue }
        if ((Test-Path $ROOT) -and -not (Get-ChildItem $ROOT -ErrorAction SilentlyContinue)) {
            Remove-Item $ROOT -Force -ErrorAction SilentlyContinue
        }
        $extensions = @('.mp4', '.mkv', '.mov', '.avi', '.mp3', '.wav', '.m4a', '.flac')
        foreach ($ext in $extensions) {
            $key = "HKCU:\Software\Classes\SystemFileAssociations\$ext\shell\CL34N"
            if (Test-Path $key) { Remove-Item $key -Recurse -Force -ErrorAction SilentlyContinue }
        }
        Read-Host "  Press Enter to close"
    }
}

Main
