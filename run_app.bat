@echo off
setlocal
cd /d "%~dp0"

rem Local launcher: single Streamlit on :8501 with auto-restart on generator changes.
set "PYTHONPATH=%CD%\src"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" local_streamlit_supervisor.py --port 8501
) else (
  python local_streamlit_supervisor.py --port 8501
)
endlocal
