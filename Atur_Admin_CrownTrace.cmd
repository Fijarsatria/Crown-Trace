@echo off
setlocal
title CrownTrace - Siapkan Administrator Lokal
pushd "%~dp0" || exit /b 1
if not exist ".venv\Scripts\python.exe" (
    echo Jalankan Buka_WebGIS.cmd terlebih dahulu untuk menyiapkan Python.
    pause
    popd
    exit /b 1
)
echo Akun dibuat hanya untuk mode operasional lokal, bukan demo.
echo Gunakan alamat email dan kata sandi milik administrator.
".venv\Scripts\python.exe" -m crowntrace.portal_server --operational --init-admin
if errorlevel 1 (
    echo Pembuatan akun gagal.
    pause
    popd
    exit /b 1
)
echo Buka Buka_Operasional_WebGIS.cmd, lalu masuk dengan akun Anda sendiri.
pause
popd
