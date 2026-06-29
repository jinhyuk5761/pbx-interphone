@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Mini PBX 서버(콘솔 전용) 시작 ===
python step3_proxy.py
pause
