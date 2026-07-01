@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

cd /d "%~dp0"

:: Clear conda's SSL_CERT_DIR/FILE — miniforge3 points them at an empty certs
:: dir, which makes uv warn "No valid certificates found". uv bundles its own
:: CA store (rustls), so these inherited vars are unneeded here.
set "SSL_CERT_DIR="
set "SSL_CERT_FILE="

:: pypi mirror (verified: tuna 200). uv reads UV_DEFAULT_INDEX.
set "UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple"

echo ========================================
echo   Pixelle-Video Web UI Launcher
echo ========================================
echo.

:: uv is the only prerequisite. If present, `uv run` auto-creates the
:: venv, installs dependencies, and downloads a matching Python itself.
where uv >nul 2>&1
if errorlevel 1 (
    echo [!] uv not detected. uv is the package manager this project uses.
    echo.
    choice /c YN /n /m "Auto-download and install uv now? [Y/N]: "
    if errorlevel 2 goto :nouv
    echo.
    echo Installing uv...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    set "UV_BIN=%USERPROFILE%\.local\bin"
    if not exist "!UV_BIN!\uv.exe" (
        echo.
        echo [ERROR] uv installation failed.
        goto :nouv
    )
    set "PATH=!UV_BIN!;%PATH%"
    echo uv installed.
)

echo.
echo Starting Streamlit...
echo (First launch may take several minutes to download Python and dependencies.)
echo Press Ctrl+C to stop the server.
echo ========================================
echo.

:: FFmpeg is required at first video generation (shutil.which("ffmpeg")).
:: Precedence: PATH > tools\ffmpeg\bin\ffmpeg.exe > unpack tools\ffmpeg.zip > download.
where ffmpeg >nul 2>&1
if errorlevel 1 (
    set "FFMPEG_DIR=%~dp0tools\ffmpeg\bin"
    if not exist "!FFMPEG_DIR!\ffmpeg.exe" if exist "%~dp0tools\ffmpeg.zip" (
        echo Unpacking bundled tools\ffmpeg.zip ...
        powershell -NoProfile -ExecutionPolicy Bypass -Command ^
          "$ProgressPreference='SilentlyContinue';" ^
          "$d='%~dp0tools\ffmpeg'; New-Item -ItemType Directory -Force $d | Out-Null;" ^
          "Expand-Archive -Path '%~dp0tools\ffmpeg.zip' -DestinationPath $d -Force;" ^
          "$b=Get-ChildItem $d -Recurse -Filter ffmpeg.exe | Select-Object -First 1;" ^
          "if($b){Move-Item $b.DirectoryName.FullName (Join-Path $d 'bin') -Force}"
    )
    if exist "!FFMPEG_DIR!\ffmpeg.exe" (
        set "PATH=!FFMPEG_DIR!;%PATH%"
    ) else (
        echo [!] FFmpeg not detected. It is required to generate videos.
        echo.
        choice /c YN /n /m "Auto-download FFmpeg (~90 MB) into .\tools\ffmpeg? [Y/N]: "
        if errorlevel 2 goto :noffmpeg
        echo.
        powershell -NoProfile -ExecutionPolicy Bypass -Command ^
          "$ProgressPreference='SilentlyContinue';" ^
          "$u='https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip';" ^
          "$z=\"$env:TEMP\ffmpeg.zip\"; Invoke-WebRequest $u -OutFile $z;" ^
          "$d='%~dp0tools\ffmpeg'; New-Item -ItemType Directory -Force $d | Out-Null;" ^
          "Expand-Archive -Path $z -DestinationPath $d -Force;" ^
          "$b=Get-ChildItem $d -Recurse -Filter ffmpeg.exe | Select-Object -First 1;" ^
          "if($b){Move-Item $b.DirectoryName.FullName (Join-Path $d 'bin') -Force}"
        if not exist "!FFMPEG_DIR!\ffmpeg.exe" (
            echo.
            echo [ERROR] FFmpeg download failed.
            goto :noffmpeg
        )
        set "PATH=!FFMPEG_DIR!;%PATH%"
        echo FFmpeg ready.
        echo.
    )
)

:: Playwright's Chromium binary is not shipped by `uv sync`; without it the
:: first video generation fails with "Executable doesn't exist". Idempotent.
:: Mirror: cdn.playwright.dev is a known CN slow/blocked spot; npmmirror hosts
:: the chrome-for-testing binaries (verified path: /binaries/chrome-for-testing/).
set "PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/chrome-for-testing"
echo Ensuring Playwright Chromium is installed (first run downloads ~170 MB)...
uv run playwright install chromium
if errorlevel 1 goto :fail

uv run streamlit run web/app.py

if errorlevel 1 goto :fail
goto :end

:noffmpeg
echo.
echo ----------------------------------------
echo FFmpeg is required for video generation.
echo Install it manually or download from:
echo   https://ffmpeg.org/download.html
echo Then re-run this script.
echo ----------------------------------------
echo.
pause
exit /b 1

:nouv
echo.
echo ----------------------------------------
echo Please install uv manually, then run this script again:
echo   https://docs.astral.sh/uv/getting-started/installation/
echo ----------------------------------------
echo.
pause
exit /b 1

:fail
echo.
echo ========================================
echo   [ERROR] Failed to Start
echo ========================================
echo.
echo Common causes:
echo   - Network issue during dependency download (retry)
echo   - Source code downloaded without setup
echo.
echo For regular users, download the ONE-CLICK PACKAGE instead:
echo   https://github.com/woaiACE/Pixelle-Video/releases
echo.
echo For developers:
echo   1. Install uv: https://docs.astral.sh/uv/
echo   2. Run: uv sync
echo   3. Run this script again
echo ========================================
echo.
pause
exit /b 1

:end
endlocal
