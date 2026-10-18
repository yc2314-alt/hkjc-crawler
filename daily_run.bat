@echo off
setlocal
chcp 65001 >nul
cd /d "C:\Users\Cheung\Desktop\hkjc-crawler"
if errorlevel 1 goto fail

echo ========================================
echo 馬會每日更新
echo 資料夾: %CD%
echo ========================================
echo.

set "PYTHON=python"
if exist "%CD%\.venv\Scripts\python.exe" (
  set "PYTHON=%CD%\.venv\Scripts\python.exe"
  echo 使用虛擬環境 Python
) else (
  echo 使用系統 Python
)
echo.

echo 開始抽取賽果...
"%PYTHON%" hkjc_full.py
if errorlevel 1 goto fail

echo.
echo 抽取完成，開始提交並推送...

set "GIT=git"
where git >nul 2>&1
if errorlevel 1 set "GIT=C:\Program Files\Git\cmd\git.exe"

"%GIT%" add .
if errorlevel 1 goto fail

"%GIT%" diff --cached --quiet
if errorlevel 1 (
  "%GIT%" commit -m "每日更新"
  if errorlevel 1 goto fail
) else (
  echo 沒有新變更，跳過提交
)

"%GIT%" push
if errorlevel 1 goto fail

echo.
echo ✅ 完成！已更新並推送去 GitHub
echo.
pause
exit /b 0

:fail
echo.
echo ❌ 有問題！請睇上面錯誤
echo.
pause
exit /b 1
