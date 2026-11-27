@echo off
setlocal
set "TARGET_ROOT=%~1"
if not defined TARGET_ROOT set "TARGET_ROOT=%USERPROFILE%\Desktop\Project\Research\Research Sawit"
if not exist "%TARGET_ROOT%\." (
    echo Folder tujuan tidak ditemukan: "%TARGET_ROOT%"
    exit /b 1
)
for %%I in ("%TARGET_ROOT%\CrownTrace") do set "TARGET=%%~fI"
if exist "%TARGET%" (
    echo Folder CrownTrace sudah tersedia: "%TARGET%"
    exit /b 2
)
robocopy "%~dp0." "%TARGET%" /E /R:0 /W:0 /XJ /XD .venv __pycache__ .pytest_cache analyses portal_workspace /NFL /NDL /NJH /NJS /NP
if errorlevel 8 (
    echo Penyalinan gagal. Periksa akses dan isi folder "%TARGET%".
    exit /b 1
)
echo CrownTrace tersedia di "%TARGET%"
echo Jalankan Buka_WebGIS.cmd dari folder tersebut.
exit /b 0
