#Requires -Version 5.1
<#
.SYNOPSIS
    CL34N Installer - Strips music from video/audio via right-click context menu.
.DESCRIPTION
    Installs to %LOCALAPPDATA%\CL34N\ with no admin rights required.
    Requires an NVIDIA GPU with CUDA drivers installed system-wide.
.NOTES
    Run with:  powershell -ExecutionPolicy Bypass -File install.ps1
    Or:        irm https://raw.githubusercontent.com/etwell/cl34n/main/install.ps1 | iex
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$PYTHON_VERSION = '3.11.9'
$PYTHON_URL     = "https://www.python.org/ftp/python/$PYTHON_VERSION/python-$PYTHON_VERSION-embed-amd64.zip"
$PYTHON_SHA256  = ''

$FFMPEG_VERSION = '7.1'
$FFMPEG_URL     = "https://github.com/GyanD/codexffmpeg/releases/download/$FFMPEG_VERSION/ffmpeg-$FFMPEG_VERSION-essentials_build.zip"
$FFMPEG_SHA256  = ''

$GET_PIP_URL = 'https://bootstrap.pypa.io/get-pip.py'
$GITHUB_RAW  = 'https://raw.githubusercontent.com/etwell/cl34n/main'

$APP_NAME = 'CL34N'
$ROOT     = Join-Path $env:LOCALAPPDATA $APP_NAME
$APP_DIR  = Join-Path $ROOT 'app'
$PY_DIR   = Join-Path $ROOT 'python'
$FF_DIR   = Join-Path $ROOT 'ffmpeg'
$PY_EXE   = Join-Path $PY_DIR 'python.exe'

# 4 bootstrap steps here + 3 package steps in setup.py = 7 total
$script:BarStep  = 0
$script:BarTotal = 7

function Show-Bar {
    param([string]$Label)
    $script:BarStep++
    $width  = 40
    $filled = [int]($width * $script:BarStep / $script:BarTotal)
    $bar    = '#' * $filled + '-' * ($width - $filled)
    $line   = "  [$bar]  $($script:BarStep)/$($script:BarTotal)  $Label"
    Write-Host ("`r" + $line.PadRight(72)) -NoNewline
}

function Get-File {
    param([string]$Url, [string]$Dest, [string]$Sha256 = '')
    try {
        (New-Object System.Net.WebClient).DownloadFile($Url, $Dest)
    } catch {
        throw "Download failed: $_"
    }
    if ($Sha256) {
        $hash = (Get-FileHash -Path $Dest -Algorithm SHA256).Hash
        if ($hash -ne $Sha256.ToUpper()) {
            Remove-Item $Dest -Force -ErrorAction SilentlyContinue
            throw "SHA256 mismatch"
        }
    }
}

function Test-Cuda {
    $nvSmi = $null
    foreach ($c in @('nvidia-smi', 'C:\Windows\System32\nvidia-smi.exe',
                      'C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe')) {
        if (Get-Command $c -ErrorAction SilentlyContinue) { $nvSmi = $c; break }
        if (Test-Path $c) { $nvSmi = $c; break }
    }

    if ($nvSmi) {
        $gpu = (& $nvSmi --query-gpu=name --format=csv,noheader 2>&1) | Select-Object -First 1
        Show-Bar "GPU: $($gpu.Trim())"
        return
    }

    $nvidiaGpu = Get-WmiObject Win32_VideoController -ErrorAction SilentlyContinue |
                 Where-Object { $_.Name -like '*NVIDIA*' } | Select-Object -First 1

    Write-Host ""
    Write-Host ""
    Write-Host "  -------------------------------------------------------" -ForegroundColor Red

    if ($nvidiaGpu) {
        Write-Host "  NVIDIA GPU found but drivers are not installed." -ForegroundColor Red
        Write-Host "  -------------------------------------------------------" -ForegroundColor Red
        Write-Host ""
        Write-Host "  Your GPU: $($nvidiaGpu.Name)" -ForegroundColor White
        Write-Host ""
        Write-Host "  CL34N needs your NVIDIA display driver to be installed." -ForegroundColor White
        Write-Host "  (You do NOT need a separate CUDA download -- it's included" -ForegroundColor Gray
        Write-Host "   in the standard driver.)" -ForegroundColor Gray
        Write-Host ""
        Write-Host "  Option A -- NVIDIA App (easiest, auto-detects your GPU):" -ForegroundColor Cyan
        Write-Host "     https://www.nvidia.com/en-us/software/nvidia-app/" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "  Option B -- Manual driver download:" -ForegroundColor Cyan
        Write-Host "     https://www.nvidia.com/en-us/drivers/" -ForegroundColor Cyan
    } else {
        Write-Host "  No NVIDIA GPU detected." -ForegroundColor Red
        Write-Host "  -------------------------------------------------------" -ForegroundColor Red
        Write-Host ""
        Write-Host "  CL34N requires an NVIDIA graphics card to work." -ForegroundColor White
        Write-Host "  This computer does not appear to have one." -ForegroundColor White
        Write-Host ""
        Write-Host "  If you do have an NVIDIA GPU, try installing its driver:" -ForegroundColor Gray
        Write-Host "     https://www.nvidia.com/en-us/software/nvidia-app/" -ForegroundColor Cyan
    }

    Write-Host ""
    Write-Host "  After installing the driver, restart your computer" -ForegroundColor White
    Write-Host "  then run this installer again." -ForegroundColor White
    Write-Host ""
    Read-Host "  Press Enter to close"
    exit 1
}

function Install-Python {
    $tmp = Join-Path $env:TEMP 'cl34n_python.zip'
    Get-File -Url $PYTHON_URL -Dest $tmp -Sha256 $PYTHON_SHA256
    if (Test-Path $PY_DIR) { Remove-Item $PY_DIR -Recurse -Force }
    New-Item -ItemType Directory -Path $PY_DIR -Force | Out-Null
    Expand-Archive -Path $tmp -DestinationPath $PY_DIR -Force
    Remove-Item $tmp
    $pthFile = Get-ChildItem $PY_DIR -Filter '*._pth' | Select-Object -First 1
    if (-not $pthFile) { throw "Python ._pth not found -- extraction failed." }
    (Get-Content $pthFile.FullName -Raw) -replace '#import site', 'import site' |
        Set-Content $pthFile.FullName
    $getPip = Join-Path $env:TEMP 'cl34n_get_pip.py'
    (New-Object System.Net.WebClient).DownloadFile($GET_PIP_URL, $getPip)
    & $PY_EXE $getPip --quiet --no-warn-script-location 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "pip bootstrap failed" }
    Remove-Item $getPip
    Show-Bar "Python"
}

function Install-FFmpeg {
    $tmp    = Join-Path $env:TEMP 'cl34n_ffmpeg.zip'
    $tmpDir = Join-Path $env:TEMP 'cl34n_ffmpeg_extract'
    Get-File -Url $FFMPEG_URL -Dest $tmp -Sha256 $FFMPEG_SHA256
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
    Show-Bar "FFmpeg"
}

function Install-AppFiles {
    New-Item -ItemType Directory -Path $APP_DIR -Force | Out-Null
    $wc = New-Object System.Net.WebClient
    foreach ($f in @('cl34n.py', 'mdx_infer.py', 'model_registry.py', 'setup.py')) {
        $wc.DownloadFile("$GITHUB_RAW/$f", (Join-Path $APP_DIR $f))
    }
    Show-Bar "App files"
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
    Write-Host "  Installing... usually 3-5 minutes." -ForegroundColor Gray
    Write-Host ""

    $sw = [System.Diagnostics.Stopwatch]::StartNew()

    try {
        New-Item -ItemType Directory -Path $ROOT -Force | Out-Null
        Test-Cuda
        Install-Python
        Install-FFmpeg
        Install-AppFiles

        # setup.py continues the bar from the current step
        $env:PYTHONUTF8 = '1'
        & $PY_EXE -u (Join-Path $APP_DIR 'setup.py') $script:BarStep $script:BarTotal
        if ($LASTEXITCODE -ne 0) { throw "setup.py failed (exit $LASTEXITCODE)" }

        $sw.Stop()
        $elapsed = [math]::Round($sw.Elapsed.TotalMinutes, 1)

        Write-Host ""
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
