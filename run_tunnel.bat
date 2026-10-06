@echo off
REM Arranca un tunnel de Cloudflare para exponer el MCP server a internet.
REM Copia la URL https://xxx.trycloudflare.com que aparezca y pegala
REM en run_mcp_server.bat como RACE_ENGINEER_URL.
REM
REM Requisito: instalar cloudflared
REM   winget install Cloudflare.cloudflared
REM
echo === Cloudflare Tunnel para Race Engineer ===
echo.
echo Cuando aparezca la URL (https://xxx.trycloudflare.com), copiala
echo y pegala en run_mcp_server.bat como RACE_ENGINEER_URL.
echo.
cloudflared tunnel --url http://localhost:8000
pause
