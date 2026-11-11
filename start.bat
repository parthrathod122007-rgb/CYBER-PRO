@echo off
title CYBERPRO Platform Server
cd /d "%~dp0"
echo Starting CYBERPRO Academy & LMS Platform on http://127.0.0.1:8000 ...
python server.py
pause
