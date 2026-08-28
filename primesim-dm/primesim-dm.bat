@echo off
rem Windows wrapper: "primesim-dm.bat lint deck.sp" from any directory.
rem Prefers the py launcher, falls back to python on PATH.
setlocal
set "PYEXE=py"
where py >nul 2>nul || set "PYEXE=python"
"%PYEXE%" "%~dp0primesim-dm" %*
