@echo off
cd /d %~dp0
where python >nul 2>nul
if %errorlevel%==0 (
  python bot2.py
) else (
  "C:\Users\da125\AppData\Local\Programs\Python\Python313\python.exe" bot2.py
)
pause
