@echo off
setlocal

REM === Race Engineer MCP Server para ChatGPT ===
REM
REM 1. Arranca run_tunnel.bat primero y copia la URL del tunnel
REM 2. Pega la URL aqui abajo (sin barra final)
REM 3. Ejecuta este script
REM 4. En ChatGPT, anade un MCP server con la URL: <tunnel>/mcp

REM --- URL publica del tunnel (CAMBIAR cada vez que arranques cloudflared) ---
set RACE_ENGINEER_URL=https://PEGA-AQUI-LA-URL.trycloudflare.com

REM --- PIN de autorizacion (dejar vacio para desactivar) ---
set RACE_ENGINEER_PIN=2609

REM --- Puerto local (debe coincidir con run_tunnel.bat) ---
set RACE_ENGINEER_PORT=8000

cd /d C:\Users\User\src\race-engineer
echo.
echo === Race Engineer MCP Server ===
echo URL:  %RACE_ENGINEER_URL%
echo MCP:  %RACE_ENGINEER_URL%/mcp
echo PIN:  %RACE_ENGINEER_PIN%
echo.

.venv\Scripts\python.exe -m race_engineer.mcp_server.server

pause
