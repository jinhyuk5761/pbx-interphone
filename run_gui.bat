@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Mini PBX GUI 시작 ===
python gui_pbx.py
echo.
echo (창이 닫혔거나 오류가 발생했습니다. 위 메시지를 확인하세요.)
pause
