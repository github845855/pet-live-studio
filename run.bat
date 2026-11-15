@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 start.py %*
goto done

:use_python
where python >nul 2>nul
if errorlevel 1 goto no_python
python start.py %*
goto done

:no_python
echo.
echo Python was not found. Install Python 3.10+ from https://www.python.org/downloads/
echo Remember to enable "Add python.exe to PATH" during setup.
echo.
pause
exit /b 1

:done
if errorlevel 1 pause
exit /b %errorlevel%