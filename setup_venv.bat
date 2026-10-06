@echo off
REM Recrea/actualiza el virtualenv con las dependencias de race-engineer.
REM Ejecutar una vez tras actualizar pyproject.toml.

cd /d C:\Users\User\src\race-engineer

echo === Actualizando dependencias ===

if not exist .venv (
    echo Creando virtualenv...
    python -m venv .venv
)

.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -e ".[dev]"

echo.
echo === Listo ===
echo Dependencias instaladas:
.venv\Scripts\pip.exe list --format=columns | findstr /i "mcp pydantic openai starlette uvicorn httpx"
echo.
pause
