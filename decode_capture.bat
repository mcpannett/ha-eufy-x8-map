@echo off
rem Decode an X8 app capture (read-only). Put the PCAPdroid .pcap file in tools\probe first.
cd /d "%~dp0tools\probe"
python -m pip install --quiet requests cryptography
python decode_capture.py
echo.
echo Finished - tell Claude.
pause
