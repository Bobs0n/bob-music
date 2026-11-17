@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Instalando o Bob Music pela primeira vez...
  py -3.12 -m venv .venv
  .venv\Scripts\python.exe -m pip install -r requirements.txt
)
start "" ".venv\Scripts\pythonw.exe" run.py
