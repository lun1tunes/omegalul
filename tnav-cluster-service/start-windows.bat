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

for /f "usebackq eol=# tokens=1,* delims==" %%A in ("tnav-cluster.env") do if not "%%A"=="" set "%%A=%%B"

if not defined TNAV_CLUSTER_HOST set "TNAV_CLUSTER_HOST=127.0.0.1"
if not defined TNAV_CLUSTER_PORT set "TNAV_CLUSTER_PORT=8400"

echo Starting tNav Cluster Agent at http://%TNAV_CLUSTER_HOST%:%TNAV_CLUSTER_PORT%
echo Keep this window open. Press Ctrl+C to stop.
".venv\Scripts\python.exe" -m uvicorn app.main:app --host "%TNAV_CLUSTER_HOST%" --port "%TNAV_CLUSTER_PORT%"
exit /b %errorlevel%
