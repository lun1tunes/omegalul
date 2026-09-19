@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv is missing. Run setup-windows.bat first.
  exit /b 1
)
if not exist "tnav-cluster.env" (
  echo ERROR: tnav-cluster.env is missing.
  echo Run: copy tnav-cluster.env.example tnav-cluster.env
  exit /b 1
)

echo Starting tNav Cluster Agent. Env is loaded from tnav-cluster.env by Python, not by this .bat.
echo Keep this window open. Press Ctrl+C to stop.
".venv\Scripts\python.exe" -m app
exit /b %errorlevel%
