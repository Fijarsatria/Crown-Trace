@echo off
setlocal
title CrownTrace - Layanan Pemetaan
pushd "%~dp0" || exit /b 1
if /i "%~1"=="--check" goto check
set "PORTAL_MODE=--demo"
set "PORTAL_PORT=8765"
if /i "%~1"=="--operational" (
    set "PORTAL_MODE=--operational"
    set "PORTAL_PORT=8766"
)
if not "%~2"=="" set "PORTAL_PORT=%~2"
set "CROWNTRACE_PYTHON=.venv\Scripts\python.exe"
if not exist "%CROWNTRACE_PYTHON%" (
    echo Menyiapkan lingkungan Python...
    python -m venv .venv || goto failed
)
"%CROWNTRACE_PYTHON%" -c "import numpy, PIL, pyproj, torch, sqlite3" >nul 2>&1
if errorlevel 1 (
    echo Memasang dependensi. Tahap pertama memerlukan internet.
    "%CROWNTRACE_PYTHON%" -m pip install -r requirements.txt || goto failed
)
if /i "%PORTAL_MODE%"=="--demo" echo DEMO lokal: tidak ada transfer atau transaksi nyata.
if /i "%PORTAL_MODE%"=="--operational" echo Mode operasional lokal. Buat admin melalui Atur_Admin_CrownTrace.cmd jika pertama kali.
rem Reuse this portal if it is already running on the selected port.
"%CROWNTRACE_PYTHON%" -c "import http.client,json,sys; c=http.client.HTTPConnection('127.0.0.1',int(sys.argv[1]),timeout=1); c.request('GET','/api/bootstrap'); r=c.getresponse(); d=json.loads(r.read()); assert r.status==200 and d.get('portal_release')=='20261127-public' and d.get('demo')==(sys.argv[2]=='--demo')" "%PORTAL_PORT%" "%PORTAL_MODE%" >nul 2>&1
if not errorlevel 1 (
    echo Portal sudah aktif. Membuka halaman ...
    start "" "http://127.0.0.1:%PORTAL_PORT%/"
    popd
    exit /b 0
)
set "PROJ_NETWORK=OFF"
echo Portal: http://127.0.0.1:%PORTAL_PORT%
echo Tekan Ctrl+C di jendela ini untuk menghentikan portal.
"%CROWNTRACE_PYTHON%" -m crowntrace.portal_server %PORTAL_MODE% --port %PORTAL_PORT% --open-browser
if errorlevel 1 goto failed
popd
exit /b 0

:check
if not exist ".venv\Scripts\python.exe" goto failed
if not exist "web\portal\index.html" goto failed
if not exist "web\portal\portal.js" goto failed
if not exist "web\portal\portal-map.js" goto failed
if not exist "web\portal\portal-ops.js" goto failed
if not exist "web\portal\portal.css" goto failed
".venv\Scripts\python.exe" -m crowntrace.portal_server --help >nul || goto failed
".venv\Scripts\python.exe" -c "from crowntrace.portal_engine import engine_status,PUBLIC_SAMPLE,INVENTORY_TILE_SAMPLE; import json; v=engine_status(); print(json.dumps(v,indent=2)); assert all(v.values()) and INVENTORY_TILE_SAMPLE.is_file() and (PUBLIC_SAMPLE/'summary.json').is_file() and (PUBLIC_SAMPLE/'map.json').is_file(), 'Bobot/GeoTIFF/contoh belum lengkap'" || goto failed
popd
echo Semua profil model serta portal layanan siap.
exit /b 0

:failed
echo Portal gagal dibuka. Periksa Python, model, QGIS, atau port yang mungkin sudah digunakan.
if /i not "%~1"=="--check" pause
popd
exit /b 1
