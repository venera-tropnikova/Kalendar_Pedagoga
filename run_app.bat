@echo off
setlocal
cd /d "%~dp0"

rem Stop ONLY the process listening on localhost:8501 so a stale Streamlit
rem process cannot keep serving an older copy of the project.
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8501" ^| findstr "LISTENING"') do (
  taskkill /F /PID %%P >nul 2>&1
)

set "PYTHONPATH=%CD%\src"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m streamlit run app.py --server.port 8501
) else (
  python -m streamlit run app.py --server.port 8501
)
endlocal
