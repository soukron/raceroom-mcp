# Race Engineer MCP

AI race engineer for **RaceRoom Racing Experience** — an MCP server that reads
live telemetry from the simulator's shared memory and exposes it as tools for
ChatGPT, Claude or any MCP-compatible client.

Built for real-time voice coaching during races: position, gaps, tire wear,
fuel strategy, pit windows and rival tracking — all accessible through natural
language.

## How it works

```
RaceRoom (shared memory $R3E)
        │
        ▼
   Collector (10 Hz polling)
        │
        ▼
   Race Tracker (lap detection, gap calculation, event logging)
        │
        ▼
   MCP Server (Streamable HTTP + OAuth 2.1)
        │
        ▼
   Cloudflare Tunnel (public HTTPS)
        │
        ▼
   ChatGPT / Claude / any MCP client
```

The collector reads R3E's shared memory at 10 Hz and feeds the race tracker,
which detects laps, calculates chained gaps to all drivers, tracks tire/fuel
state and logs race events (position changes, pit stops, fastest laps,
incidents). The MCP server exposes 9 tools over Streamable HTTP with OAuth 2.1
+ PKCE authentication.

## MCP Tools

| Tool | Description |
|---|---|
| `briefing` | Compact snapshot: position, gaps, pace, tires, fuel, nearest rivals, alerts |
| `player_state` | Full player state: position, lap, gaps, sectors, tires, fuel, damage |
| `player_laps` | Detailed player lap history: per-wheel grip, fuel, gaps, delta vs best |
| `tire_state` | All 4 wheels: grip, wear, temps, trend and projected laps to threshold |
| `fuel_state` | Fuel remaining, consumption, laps estimate and deficit |
| `pit_strategy` | Traffic gaps, rivals in/already pitted, pit window status |
| `driver_state` | Nearby drivers + player: position, gap, sectors, pace, pit info |
| `driver_laps` | Lap history for any driver: time, sectors, position, tires, pit stops |
| `race_state` | Session overview: track, format, phase, laps, pace history |

## Prerequisites

- **Windows** with RaceRoom Racing Experience installed
- **Python 3.11+**
- **cloudflared** (for exposing the server to ChatGPT)

```bat
winget install Cloudflare.cloudflared
```

## Setup

Clone the repository and create a virtualenv:

```bat
cd C:\Users\User\src\race-engineer
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Or use the provided script:

```bat
setup_venv.bat
```

## Running

You need two terminals: one for the Cloudflare tunnel and one for the MCP server.

### 1. Start the tunnel

```bat
run_tunnel.bat
```

This runs `cloudflared tunnel --url http://localhost:8000`. Copy the generated
`https://xxx.trycloudflare.com` URL.

### 2. Start the MCP server

Edit `run_mcp_server.bat` and paste the tunnel URL:

```bat
set RACE_ENGINEER_URL=https://xxx.trycloudflare.com
set RACE_ENGINEER_PIN=1234
set RACE_ENGINEER_PORT=8000
```

Then run:

```bat
run_mcp_server.bat
```

The server starts collecting telemetry immediately. If RaceRoom is not running
yet, the collector waits and reconnects automatically every 5 seconds.

### 3. Connect from ChatGPT

In ChatGPT, add an MCP server with URL: `https://xxx.trycloudflare.com/mcp`

You'll be prompted for the PIN on first connection.

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `RACE_ENGINEER_URL` | `http://localhost:8000` | Public tunnel URL |
| `RACE_ENGINEER_PIN` | _(empty)_ | PIN for OAuth consent page (empty = no PIN) |
| `RACE_ENGINEER_PORT` | `8000` | Local HTTP port |

## System prompt

The file [`PROMPT.md`](PROMPT.md) contains a system prompt designed to make
ChatGPT (or any LLM) behave like a real race engineer over voice radio: brief,
action-first, no filler. Paste it into ChatGPT's custom instructions or system
prompt before starting a race session.

## Console output

The tracker logs race events in real time:

```
🏎️ Grid position: P8
Lap 1 completed
⚡ PERSONAL BEST — 1:32.451 (-1:32.451s)
🟢 P8 → P7 - Gains position on Nico Verdonk - +1 position
   ↑ Fabrizio Broggi P5 → P4 (Klaus Werner)
🔧 PIT IN — player enters pit (P5)
🔧 PIT OUT — player exits pit (P9)
💥 IMPACT — damage: aero 15%, susp 8%
🏆 FASTEST LAP — Fidel Leib: 1:31.208
🏁 RACE FINISHED — P3 (FINISHED)
🏁 Klaus Werner crosses the line — P4
```

## Project structure

```
race-engineer/
├── src/race_engineer/
│   ├── collector/
│   │   ├── reader.py          # Shared memory reader ($R3E)
│   │   ├── tracker.py         # Race tracker: laps, gaps, events
│   │   └── r3e_types.py       # R3E shared memory struct definitions
│   ├── mcp_server/
│   │   ├── server.py          # MCP server + OAuth endpoints + 9 tools
│   │   └── oauth.py           # OAuth 2.1 + PKCE (authorization code flow)
│   ├── agent/
│   │   └── cli.py             # Local CLI agent (alternative to MCP)
│   └── models.py              # Data models (LapData, RivalState, etc.)
├── pyproject.toml
├── config.example.yaml        # Config template (LLM, collector, agent)
├── setup_venv.bat             # Create/update virtualenv
├── run_tunnel.bat             # Start Cloudflare tunnel
└── run_mcp_server.bat         # Start MCP server
```

## Health check

```bat
curl http://localhost:8000/health
```

Returns `telemetry_active: true` when RaceRoom is running and data is flowing.

## License

MIT
