@echo off
setlocal enabledelayedexpansion

title Product Synthesis Studio — Local Digital Product Factory
echo =====================================================================
echo              PRODUCT SYNTHESIS STUDIO (PSS) v1.0.0
echo          Local-First AI Digital Product Creation Factory
echo =====================================================================
echo.

cd /d "%~dp0"

:: 1. Check Python installation
where py >nul 2>nul
if %errorlevel% neq 0 (
    where python >nul 2>nul
    if %errorlevel% neq 0 (
        echo [ERROR] Python was not found in your system PATH.
        echo Please install Python 3.11 from python.org and check "Add Python to PATH".
        pause
        exit /b 1
    )
    set PY_CMD=python
) else (
    set PY_CMD=py -3.11
)

:: 2. Check virtual environment
if not exist ".venv\Scripts\activate.bat" (
    echo [SETUP] Virtual environment not detected. Creating isolated environment...
    %PY_CMD% -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo [SETUP] Installing required dependencies (ReportLab, Streamlit, PyMuPDF, etc.)...
    call .venv\Scripts\activate.bat
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo [ERROR] Dependency installation encountered issues.
        pause
        exit /b 1
    )
) else (
    call .venv\Scripts\activate.bat
)

:: 3. Initialize SQLite Database Schema
echo [DB] Initializing database and verifying schema...
python -m database.db

:: 4. Probe Ollama Service
echo [AI] Checking local Ollama service connection...
curl -s http://localhost:11434/api/tags >nul 2>nul
if %errorlevel% neq 0 (
    echo.
    echo [WARNING] Ollama is not currently detected on http://localhost:11434.
    echo The UI will run, but to enable AI generation, open another PowerShell window and run:
    echo     ollama serve
    echo.
) else (
    echo [AI] Local Ollama service is ONLINE and ready!
)

:: 5. Launch Streamlit Application
echo.
echo =====================================================================
echo Launching Product Synthesis Studio in your browser...
echo Binding to localhost:8501
echo =====================================================================
streamlit run app.py --server.port 8501 --server.address localhost

pause

