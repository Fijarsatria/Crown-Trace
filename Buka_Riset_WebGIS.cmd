@echo off
setlocal
if /i "%~1"=="--check" goto check
title CrownTrace - Laboratorium Riset
pushd "%~dp0" || goto failed
set "CROWNTRACE_PYTHON=.venv\Scripts\python.exe"
if not exist "%CROWNTRACE_PYTHON%" (
    echo Menyiapkan lingkungan Python...
    python -m venv .venv || goto failed
)
"%CROWNTRACE_PYTHON%" -c "import numpy, PIL, pyproj, torch" >nul 2>&1
if errorlevel 1 (
    echo Memasang dependensi. Tahap pertama memerlukan internet.
    "%CROWNTRACE_PYTHON%" -m pip install -r requirements.txt || goto failed
)
rem Reuse a running research lab only when the new segmentation API responds.
"%CROWNTRACE_PYTHON%" -c "import http.client,json; c=http.client.HTTPConnection('127.0.0.1',8767,timeout=1); c.request('GET','/api/segmentation/status'); r=c.getresponse(); d=json.loads(r.read()); assert r.status==200 and d.get('workspace')==3" >nul 2>&1
if not errorlevel 1 (
    echo Laboratorium sudah aktif. Membuka ruang kerja pemetaan...
    start "" "http://127.0.0.1:8767/"
    popd
    exit /b 0
)
echo Ruang kerja unggah foto dan GeoTIFF: http://127.0.0.1:8767/
echo Jika port 8767 sudah dipakai versi lama, hentikan terminal versi lama dahulu.
echo Tekan Ctrl+C di jendela ini untuk menghentikan WebGIS.
"%CROWNTRACE_PYTHON%" -m crowntrace.server --port 8767 --open-browser
if errorlevel 1 goto failed
popd
exit /b 0

:check
pushd "%~dp0" || exit /b 1
if not exist "web\index.html" exit /b 1
if not exist "web\segmentation.html" exit /b 1
if not exist "web\segmentation.js" exit /b 1
if not exist "web\workspace.html" exit /b 1
if not exist "web\workspace.js" exit /b 1
if not exist "web\workspace_jobs.js" exit /b 1
if not exist "results\expanded_train\best.pth" exit /b 1
".venv\Scripts\python.exe" -m crowntrace.server --help >nul || exit /b 1
popd
echo Berkas utama, bobot, dan perintah WebGIS ditemukan.
exit /b 0

:failed
echo Gagal membuka WebGIS. Pastikan Python bisa dijalankan dengan perintah python.
pause
exit /b 1

