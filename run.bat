@echo off
title Google Places Intelligence Service
echo =============================================
echo  Google Places Data Ingestion Service
echo =============================================
echo.

:: Check if .env exists
if not exist ".env" (
    echo [!] .env file not found. Copying from .env.example...
    copy .env.example .env
    echo [!] Please edit .env and set your GOOGLE_PLACES_API_KEY
    echo.
)

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Please install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

:: Create venv if not exists
if not exist ".venv" (
    echo [*] Creating virtual environment...
    python -m venv .venv
    echo [OK] Virtual environment created.
)

:: Activate venv
echo [*] Activating virtual environment...
call .venv\Scripts\activate.bat

:: Install dependencies
echo [*] Installing dependencies...
pip install -r requirements.txt --quiet
echo [OK] Dependencies installed.

:: Check if PostgreSQL is accessible
echo.
echo [*] Checking database connection...
python -c "import psycopg2; conn = psycopg2.connect('postgresql://postgres:postgres@localhost:5432/places_db'); conn.close(); print('[OK] Database connected.')" 2>nul
if errorlevel 1 (
    echo [!] PostgreSQL not reachable at localhost:5432/places_db
    echo [!] Options:
    echo     1. Start PostgreSQL locally
    echo     2. Use: docker-compose up db -d
    echo     3. Edit DATABASE_URL in .env
    echo.
    echo [*] Attempting to start DB with docker-compose...
    docker-compose up db -d 2>nul
    if errorlevel 1 (
        echo [!] Docker not available. Please start PostgreSQL manually.
        pause
        exit /b 1
    )
    echo [*] Waiting for database to be ready...
    timeout /t 5 /nobreak >nul
)

:: Run migrations
echo [*] Running database migrations...
alembic upgrade head 2>nul
if errorlevel 1 (
    echo [!] Migration failed, attempting to init tables directly...
)

:: Run tests
echo.
echo [*] Running tests...
python -m pytest tests/ -v --tb=short 2>nul
if errorlevel 1 (
    echo [!] Some tests failed, but continuing to start server...
)

:: Start server
echo.
echo =============================================
echo  Starting server at http://localhost:8000
echo  Dashboard: http://localhost:8000
echo  API Docs:  http://localhost:8000/docs
echo  Health:    http://localhost:8000/health
echo =============================================
echo  Press Ctrl+C to stop
echo.
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
