@echo off
echo ============================================
echo   Gmail Organizer - Startup
echo ============================================
echo.

cd /d "%~dp0"

set SERVER_EXE=llama-server\llama-server.exe
set MODEL=models\Qwen3-4B-Instruct-2507-Q4_K_M.gguf

if not exist "%SERVER_EXE%" (
    echo ERROR: llama-server.exe not found in llama-server\
    echo Download CPU binaries from: https://github.com/ggml-org/llama.cpp/releases
    pause
    exit /b 1
)

if not exist "%MODEL%" (
    echo ERROR: Model not found at %MODEL%
    echo Download from: https://huggingface.co/Qwen/Qwen3-4B-Instruct-GGUF
    pause
    exit /b 1
)

:: Check if llama-server is already running on port 1234
netstat -ano | findstr ":1234" >nul 2>&1
if %errorlevel%==0 (
    echo llama-server already running on port 1234. Skipping server start.
) else (
    echo Starting llama-server on port 1234...
    echo Model: %MODEL%
    start "llama-server" "%SERVER_EXE%" --model "%MODEL%" --port 1234 --host 127.0.0.1 --ctx-size 8192 --threads 8 --parallel 1 --jinja --reasoning-budget 0

    echo Waiting for server to be ready...
    :wait_loop
    timeout /t 2 /nobreak >nul
    curl -s http://127.0.0.1:1234/health >nul 2>&1
    if %errorlevel% neq 0 (
        echo   Still loading model...
        goto wait_loop
    )
    echo Server ready!
)

echo.
echo Starting Gmail Organizer...
echo.
python cli_agent.py
pause
