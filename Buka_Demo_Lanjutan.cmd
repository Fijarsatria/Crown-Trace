@echo off
setlocal
pushd "%~dp0" || exit /b 1
if not exist ".venv\Scripts\python.exe" (
 echo Jalankan Buka_WebGIS.cmd untuk menyiapkan Python.
 pause
 popd
 exit /b 1
)
echo Demo lanjutan: kontrol monitoring dan draft QA. Bukan data lapangan.
".venv\Scripts\python.exe" -m research.seed_ops_demo || (pause & popd & exit /b 1)
set "PROJ_NETWORK=OFF"
".venv\Scripts\python.exe" -m crowntrace.portal_server --demo --store portal_workspace/demo_ops --port 8770 --open-browser
popd
