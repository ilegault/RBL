@echo off
setlocal EnableDelayedExpansion

echo.
echo =============================================
echo   RBL  Build + Deploy
echo =============================================
echo.

:: --- Python (venv) is needed for the version helpers and PyInstaller ---
where python >nul 2>&1
if !ERRORLEVEL! NEQ 0 (
    echo ERROR: python is not on PATH. Activate the project venv, then run again.
    exit /b 1
)

:: --- Last deployed build (last_build.json, gitignored, written after a successful deploy) ---
python scripts\build_version.py last
echo.

:: --- Version prompt ---
:ask_version
set "VERSION="
set /p VERSION="Enter new build version (e.g. 4.3 or 4.2.1): "
if "!VERSION!"=="" (
    echo   Version cannot be empty.
    goto ask_version
)
python scripts\build_version.py check "!VERSION!"
if !ERRORLEVEL! NEQ 0 goto ask_version

echo.

:: --- Enumerate drives via PowerShell (wmic is deprecated on Windows 11) ---
:: Writes a temp PS1 file to avoid inline escaping headaches.
:: Lists all drives except C:, with label and free/total size.
set "PS_TMP=%TEMP%\rbl_drives.ps1"
> "!PS_TMP!" echo Get-PSDrive -PSProvider FileSystem ^| Where-Object { $_.Name -ne 'C' } ^| ForEach-Object {
>> "!PS_TMP!" echo     $v = Get-Volume -DriveLetter $_.Name -ErrorAction SilentlyContinue
>> "!PS_TMP!" echo     if ($v) {
>> "!PS_TMP!" echo         $lbl = if ($v.FileSystemLabel) { $v.FileSystemLabel } else { '(no label)' }
>> "!PS_TMP!" echo         $fr  = [math]::Round($v.SizeRemaining / 1GB, 1)
>> "!PS_TMP!" echo         $tot = [math]::Round($v.Size / 1GB, 1)
>> "!PS_TMP!" echo         Write-Output ("$($_.Name):|" + $lbl + "|" + $fr + "/" + $tot + " GB")
>> "!PS_TMP!" echo     }
>> "!PS_TMP!" echo }

set DRIVE_COUNT=0
for /f "usebackq delims=" %%L in (`powershell -NoProfile -ExecutionPolicy Bypass -File "!PS_TMP!" 2^>nul`) do (
    set /a DRIVE_COUNT+=1
    set "DRIVE_!DRIVE_COUNT!=%%L"
)
del "!PS_TMP!" >nul 2>&1

:: --- Handle no drives found ---
if !DRIVE_COUNT!==0 (
    echo No external drives found.
    echo Insert a USB drive or connect an external drive, then run again.
    pause
    exit /b 1
)

:: --- List available drives ---
echo Detected drive(s^):
echo.
for /l %%i in (1,1,!DRIVE_COUNT!) do (
    for /f "tokens=1,2,3 delims=|" %%A in ("!DRIVE_%%i!") do (
        echo   %%i.  %%A  [%%B]  %%C
    )
)
echo.

:: --- Auto-select if only one drive, otherwise ask ---
if !DRIVE_COUNT!==1 (
    for /f "tokens=1 delims=|" %%A in ("!DRIVE_1!") do set "USB_DRIVE=%%A"
    echo Auto-selected: !USB_DRIVE!
    goto drive_selected
)

:ask_drive
set "CHOICE="
set /p CHOICE="Select number [1-!DRIVE_COUNT!] or type a drive letter (e.g. E): "
set "USB_DRIVE="

:: Try numeric match
for /l %%i in (1,1,!DRIVE_COUNT!) do (
    if "%%i"=="!CHOICE!" (
        for /f "tokens=1 delims=|" %%A in ("!DRIVE_%%i!") do set "USB_DRIVE=%%A"
    )
)

:: Try drive-letter match (accept "E" or "E:")
if "!USB_DRIVE!"=="" (
    set "LTR=!CHOICE!"
    if not "!LTR:~1,1!"==":" set "LTR=!LTR!:"
    for /l %%i in (1,1,!DRIVE_COUNT!) do (
        for /f "tokens=1 delims=|" %%A in ("!DRIVE_%%i!") do (
            if /i "%%A"=="!LTR!" set "USB_DRIVE=%%A"
        )
    )
)

if "!USB_DRIVE!"=="" (
    echo   Invalid - enter a number from the list or a drive letter.
    goto ask_drive
)

:drive_selected

:: --- Confirm before any destructive operation ---
echo.
echo Target: !USB_DRIVE!
if exist "!USB_DRIVE!\RBL" echo WARNING: !USB_DRIVE!\RBL already exists and will be replaced.
echo.
set "CONFIRM="
set /p CONFIRM="Proceed with build + deploy? [Y/N]: "
if /i not "!CONFIRM!"=="Y" (echo Aborted. & exit /b 0)

:: --- Verify drive is accessible before we start ---
if not exist "!USB_DRIVE!\" (
    echo ERROR: !USB_DRIVE! is no longer accessible. Was it ejected?
    exit /b 1
)

:: --- Clean previous build artifacts ---
echo.
echo Cleaning previous build artifacts...
if exist build rmdir /S /Q build
if exist dist  rmdir /S /Q dist
if exist "!USB_DRIVE!\RBL" (
    echo Removing !USB_DRIVE!\RBL...
    rmdir /S /Q "!USB_DRIVE!\RBL"
)

:: --- Build stamp ---
:: build_info.json (repo root, gitignored) is bundled by rbl.spec and read by
:: src\rbl\build_info.py: window title, rbl.log, session/calibration sidecars.
:: The deploy folder stays named RBL; the version lives inside it instead.
echo.
echo Writing build stamp...
if exist build_info.json del /Q build_info.json
powershell -NoProfile -ExecutionPolicy Bypass -Command "& { param($ver) $c=(git rev-parse --short HEAD 2>$null); if(-not $c){$c=''}; $d=[bool](git status --porcelain --untracked-files=no 2>$null); $o=[ordered]@{version=$ver; commit=[string]$c; dirty=$d; built_at=(Get-Date -Format s)}; [IO.File]::WriteAllText('build_info.json',($o|ConvertTo-Json),(New-Object Text.UTF8Encoding $false)); Write-Host ('  -> v'+$ver+'  commit '+$c+$(if($d){' (uncommitted changes)'}else{''})) } '!VERSION!'"
if not exist build_info.json (
    echo   ERROR: Failed to write build_info.json
    exit /b 1
)

:: Stamped copy of the version resource; the committed version_info.txt is a
:: template a build never edits (so the dirty check above stays honest).
python scripts\build_version.py resource "!VERSION!"
if !ERRORLEVEL! NEQ 0 (
    echo   ERROR: Failed to write build\version_info.txt
    exit /b 1
)

:: --- Build ---
echo.
echo Running PyInstaller...
pyinstaller rbl.spec
if !ERRORLEVEL! NEQ 0 (
    echo.
    echo BUILD FAILED - nothing was deployed.
    exit /b 1
)

:: --- VERSION.txt beside RBL.exe, readable from Explorer without launching ---
powershell -NoProfile -ExecutionPolicy Bypass -Command "& { $j=Get-Content build_info.json -Raw | ConvertFrom-Json; $dirty=$(if($j.dirty){' (built with uncommitted changes)'}else{''}); $t=@(('RBL version '+$j.version), ('Commit      '+$j.commit+$dirty), ('Built       '+$j.built_at)); [IO.File]::WriteAllLines('dist\RBL\VERSION.txt',[string[]]$t) }"
if not exist dist\RBL\VERSION.txt (
    echo   ERROR: Failed to write dist\RBL\VERSION.txt
    exit /b 1
)

:: --- Re-verify drive is still accessible after the build ---
if not exist "!USB_DRIVE!\" (
    echo.
    echo Drive !USB_DRIVE! disappeared during the build.
    echo The build succeeded - manually copy dist\RBL to your drive.
    exit /b 1
)

:: --- Deploy ---
echo.
echo Deploying to !USB_DRIVE!\...
xcopy /E /I /Y "dist" "!USB_DRIVE!\"
if !ERRORLEVEL! NEQ 0 (
    echo.
    echo COPY FAILED - check available space on !USB_DRIVE!
    exit /b 1
)

:: --- Record this deploy for the next run's "Last deployed build" line ---
copy /Y build_info.json last_build.json >nul

echo.
echo =============================================
echo   Done! Deployed to !USB_DRIVE!\RBL  [v!VERSION!]
echo =============================================
echo.
