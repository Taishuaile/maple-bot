@echo off
where python >nul 2>nul
if %errorlevel%==0 (
  python "%~dp0bot.py"
) else (
  "C:\Users\da125\AppData\Local\Programs\Python\Python313\python.exe" "%~dp0bot.py"
)
pause
