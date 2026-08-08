@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv is missing. Run setup-windows.bat first.
  exit /b 1
)
if not exist "context-seeder.env" (
  echo ERROR: context-seeder.env is missing.
  echo Run: copy context-seeder.env.example context-seeder.env
  exit /b 1
)

for /f "usebackq eol=# tokens=1,* delims==" %%A in ("context-seeder.env") do if not "%%A"=="" set "%%A=%%B"
if not defined POSTGRES_HOST (
  echo ERROR: PostgreSQL settings are incomplete.
  exit /b 1
)
if not defined EMBEDDING_API_KEY (
  echo ERROR: EMBEDDING_API_KEY is not configured.
  exit /b 1
)

".venv\Scripts\python.exe" seed_excel_agent_context.py
exit /b %errorlevel%
