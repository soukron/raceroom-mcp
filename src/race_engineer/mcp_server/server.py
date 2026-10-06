"""MCP server del race engineer — HTTP con OAuth para ChatGPT.

Expone herramientas de telemetria como servidor MCP remoto (Streamable HTTP)
con OAuth 2.1 + PKCE para autenticacion de ChatGPT u otros clientes MCP.

Variables de entorno:
  RACE_ENGINEER_URL   URL publica del tunnel (ej. https://xxx.trycloudflare.com)
  RACE_ENGINEER_PIN   PIN numerico para la pagina de consentimiento (vacio = sin PIN)
  RACE_ENGINEER_PORT  Puerto local (default 8000)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings

from ..collector.reader import R3EReader
from ..collector.tracker import RaceTracker
from .oauth import OAuthStore, build_authorize_html

logger = logging.getLogger(__name__)

# ── Configuracion ─────────────────────────────────────────────────────

# AnyHttpUrl normaliza la URL (ej. annade trailing slash).
# ISSUER_URL es la cadena normalizada que deben usar todos los puntos
# de la spec OAuth (PRM authorization_servers, AS metadata issuer,
# redirect iss). PUBLIC_URL sin slash sirve para construir paths.
_raw_url = os.environ.get("RACE_ENGINEER_URL", "http://localhost:8000")
ISSUER_URL = str(AnyHttpUrl(_raw_url))          # "https://x.com/" (con /)
PUBLIC_URL = ISSUER_URL.rstrip("/")              # "https://x.com"  (sin /)
OAUTH_PIN = os.environ.get("RACE_ENGINEER_PIN", "")
MCP_PORT = int(os.environ.get("RACE_ENGINEER_PORT", "8000"))

# ── OAuth store ───────────────────────────────────────────────────────

oauth_store = OAuthStore(pin=OAUTH_PIN)


# ── Token verifier (RS) ──────────────────────────────────────────────

class RaceEngineerTokenVerifier:
    """Verifica tokens de acceso contra el store en memoria."""

    async def verify_token(self, token: str) -> AccessToken | None:
        entry = oauth_store.verify_token(token)
        if entry is None:
            return None
        return AccessToken(
            token=entry.token,
            client_id=entry.client_id,
            scopes=entry.scope.split(),
            resource=entry.resource,
            expires_at=int(entry.expires_at),
        )


# ── MCP Server ────────────────────────────────────────────────────────

from mcp.server.mcpserver import MCPServer

mcp = MCPServer(
    "race-engineer",
    instructions=(
        "MCP telemetry server for RaceRoom Racing Experience. "
        "Provides real-time tire, fuel, rival and pit strategy data "
        "from the simulator's shared memory. All tool responses are in English."
    ),
    token_verifier=RaceEngineerTokenVerifier(),
    auth=AuthSettings(
        issuer_url=AnyHttpUrl(ISSUER_URL),
        resource_server_url=AnyHttpUrl(f"{PUBLIC_URL}/mcp"),
        required_scopes=["telemetry"],
        validate_token_resource=True,
    ),
)


# ── OAuth endpoints (custom routes del mismo app) ─────────────────────


@mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
async def oauth_as_metadata(request: Request) -> Response:
    """RFC 8414 — Authorization Server Metadata."""
    return JSONResponse({
        "issuer": ISSUER_URL,
        "authorization_endpoint": f"{PUBLIC_URL}/authorize",
        "token_endpoint": f"{PUBLIC_URL}/token",
        "code_challenge_methods_supported": ["S256"],
        "client_id_metadata_document_supported": True,
        "token_endpoint_auth_methods_supported": ["none"],
        "authorization_response_iss_parameter_supported": True,
        "scopes_supported": ["telemetry"],
    })


@mcp.custom_route("/authorize", methods=["GET", "POST"])
async def authorize(request: Request) -> Response:
    """Authorization endpoint — pagina de consentimiento + emision de codigo."""
    if request.method == "GET":
        params = dict(request.query_params)
        html = build_authorize_html(
            client_id=params.get("client_id", ""),
            redirect_uri=params.get("redirect_uri", ""),
            scope=params.get("scope", "telemetry"),
            state=params.get("state", ""),
            code_challenge=params.get("code_challenge", ""),
            code_challenge_method=params.get("code_challenge_method", "S256"),
            resource=params.get("resource", ""),
            require_pin=bool(OAUTH_PIN),
        )
        return HTMLResponse(html)

    # POST — procesar autorizacion
    form = await request.form()

    # Verificar PIN
    if OAUTH_PIN:
        pin = str(form.get("pin", ""))
        if not oauth_store.verify_pin(pin):
            html = build_authorize_html(
                client_id=str(form.get("client_id", "")),
                redirect_uri=str(form.get("redirect_uri", "")),
                scope=str(form.get("scope", "telemetry")),
                state=str(form.get("state", "")),
                code_challenge=str(form.get("code_challenge", "")),
                code_challenge_method=str(form.get("code_challenge_method", "S256")),
                resource=str(form.get("resource", "")),
                require_pin=True,
                error_msg="PIN incorrecto",
            )
            return HTMLResponse(html, status_code=400)

    # Crear codigo de autorizacion
    code = oauth_store.create_code(
        client_id=str(form.get("client_id", "")),
        redirect_uri=str(form.get("redirect_uri", "")),
        code_challenge=str(form.get("code_challenge", "")),
        scope=str(form.get("scope", "telemetry")),
        resource=str(form.get("resource", "")),
        state=str(form.get("state", "")),
    )

    # Redirect al cliente con el codigo
    redirect_uri = str(form.get("redirect_uri", ""))
    parsed = urlparse(redirect_uri)
    existing = {k: v[0] for k, vs in parse_qs(parsed.query).items() for v in [vs]}
    existing["code"] = code
    state = str(form.get("state", ""))
    if state:
        existing["state"] = state
    existing["iss"] = ISSUER_URL  # RFC 9207 — debe coincidir con AS metadata issuer
    final_url = urlunparse(parsed._replace(query=urlencode(existing)))

    logger.info("Autorizacion concedida, redirigiendo a %s", redirect_uri[:80])
    return Response(status_code=302, headers={"Location": final_url})


@mcp.custom_route("/token", methods=["POST"])
async def token_endpoint(request: Request) -> Response:
    """Token endpoint — canjea authorization code por access token."""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
    else:
        form = await request.form()
        body = dict(form)

    grant_type = str(body.get("grant_type", ""))
    if grant_type != "authorization_code":
        return JSONResponse(
            {"error": "unsupported_grant_type"},
            status_code=400,
        )

    code = str(body.get("code", ""))
    code_verifier = str(body.get("code_verifier", ""))
    client_id = str(body.get("client_id", ""))

    if not code or not code_verifier or not client_id:
        return JSONResponse(
            {"error": "invalid_request",
             "error_description": "Faltan parametros obligatorios"},
            status_code=400,
        )

    token_entry = oauth_store.exchange_code(
        code=code,
        code_verifier=code_verifier,
        client_id=client_id,
    )

    if token_entry is None:
        return JSONResponse(
            {"error": "invalid_grant",
             "error_description": "Codigo invalido, expirado o PKCE incorrecto"},
            status_code=400,
        )

    return JSONResponse({
        "access_token": token_entry.token,
        "token_type": "Bearer",
        "expires_in": int(token_entry.expires_at - time.time()),
        "scope": token_entry.scope,
    })


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> Response:
    """Health check — util para verificar que el servidor responde."""
    tracker_ok = _tracker is not None and _tracker.state.last_update > 0
    return JSONResponse({
        "status": "ok",
        "telemetry_active": tracker_ok,
        "last_update": _tracker.state.last_update if _tracker else 0,
        "laps_completed": len(_tracker.state.lap_history) if _tracker else 0,
    })


# ── Estado global del collector ───────────────────────────────────────

_tracker: RaceTracker | None = None
_reader: R3EReader | None = None
_collector_thread: threading.Thread | None = None
_running = False


def _collector_loop(poll_rate_hz: float = 10.0) -> None:
    """Loop del collector en hilo de fondo."""
    global _running
    assert _reader is not None
    assert _tracker is not None

    interval = 1.0 / poll_rate_hz
    reconnect_interval = 5.0
    logger.info("Collector iniciado a %.0f Hz", poll_rate_hz)

    while _running:
        if not _reader.connected:
            # Reintentar conexión periódicamente
            if _reader.connect():
                logger.info("Reconectado a shared memory")
            else:
                time.sleep(reconnect_interval)
                continue

        shared = _reader.read()
        if shared is not None:
            _tracker.update(shared)
        else:
            # read() devolvió None — posiblemente RaceRoom se cerró
            _reader.close()
            logger.info("Shared memory perdida, reintentando conexión...")
            time.sleep(reconnect_interval)
            continue

        time.sleep(interval)


def _ensure_collector() -> RaceTracker:
    """Arranca collector si no esta corriendo."""
    global _tracker, _reader, _collector_thread, _running

    if _tracker is not None and _running:
        return _tracker

    _reader = R3EReader()
    _tracker = RaceTracker()

    if not _reader.wait_for_connection(timeout_s=30):
        logger.warning("RaceRoom no detectado, collector en espera")

    _running = True
    _collector_thread = threading.Thread(target=_collector_loop, daemon=True)
    _collector_thread.start()

    return _tracker


def _format_sectors(rival: Any) -> dict[str, float | str]:
    """Sectores de la vuelta en curso. N/A si el sector aún no se ha cruzado."""
    cur = rival.current_sectors
    return {
        "s1": round(cur.s1, 3) if cur.s1 > 0 else "N/A",
        "s2": round(cur.s2, 3) if cur.s2 > 0 else "N/A",
        "s3": round(cur.s3, 3) if cur.s3 > 0 else "N/A",
    }


def _json_safe(obj: Any) -> str:
    """Serializa dataclasses a JSON legible."""
    def default(o: Any) -> Any:
        if hasattr(o, "__dataclass_fields__"):
            return asdict(o)
        return str(o)

    return json.dumps(obj, default=default, ensure_ascii=False, indent=2)


# ── Tools MCP ─────────────────────────────────────────────────────────


@mcp.tool()
def briefing() -> str:
    """Compact snapshot of the current race situation for the engineer.

    Returns everything essential in a single call: position, gaps,
    tires, fuel, nearest rivals and alerts. Designed for quick
    voice updates with minimal latency.

    Use specific tools (tire_state, fuel_state, driver_laps...)
    only when deeper analysis is needed.
    """
    tracker = _ensure_collector()
    state = tracker.state
    shared = tracker._last_shared

    if not state.session:
        return "No active session."

    s = state.session
    result: dict[str, Any] = {}

    # ── Carrera terminada ──
    if state.player_finish_status.value > 0:
        result["race_finished"] = True
        result["finish_status"] = state.player_finish_status.name
        result["finish_position"] = f"P{state.finish_position}" if state.finish_position > 0 else "unknown"
        result["phase"] = s.session_phase.name
        return _json_safe(result)

    # ── Posición y progreso ──
    result["position"] = f"P{s.player_position}/{s.num_cars}"
    result["lap"] = f"{s.player_lap}/{s.laps_total_estimate if s.laps_total_estimate > 0 else s.laps_total}"
    result["laps_remaining"] = s.laps_remaining
    result["phase"] = s.session_phase.name

    # ── Gaps ──
    if shared:
        if shared.time_delta_front > 0:
            result["gap_ahead"] = f"{shared.time_delta_front:+.2f}s"
        if shared.time_delta_behind > 0:
            result["gap_behind"] = f"-{shared.time_delta_behind:.2f}s"

    # ── Ritmo ──
    recent = [l.lap_time for l in state.lap_history[-3:] if l.lap_time > 0]
    if recent:
        result["pace"] = f"{recent[-1]:.2f}s (avg {sum(recent)/len(recent):.2f}s)"

    # ── Neumáticos (resumen) ──
    if state.current_tires:
        t = state.current_tires
        worst_name, worst_grip = t.worst_grip()
        result["tires"] = f"grip {t.avg_grip():.2f} (worst: {worst_name} {worst_grip:.2f})"
        result["compound"] = f"{t.compound_front.name}/{t.compound_rear.name}"

    # ── Combustible (resumen) ──
    if state.current_fuel:
        f = state.current_fuel
        if f.deficit > 0:
            result["fuel"] = f"DEFICIT {f.deficit:.1f}L — {f.laps_remaining_fuel:.0f} laps of fuel"
        else:
            result["fuel"] = f"OK — {f.laps_remaining_fuel:.0f} laps of fuel, {-f.deficit:.1f}L surplus"

    # ── Rivales inmediatos (solo P-1 y P+1) ──
    if state.rivals:
        nearby = sorted(
            [r for r in state.rivals.values() if r.laps],
            key=lambda r: abs(r.laps[-1].gap_to_player),
        )[:2]
        rivals_brief = []
        for r in nearby:
            lat = r.laps[-1]
            rivals_brief.append(
                f"P{lat.position} {r.info.name}: {lat.gap_to_player:+.2f}s, "
                f"pace {r.recent_pace:.2f}s" if r.recent_pace > 0
                else f"P{lat.position} {r.info.name}: {lat.gap_to_player:+.2f}s"
            )
        if rivals_brief:
            result["rivals"] = rivals_brief

    # ── Alertas ──
    alerts = []
    if state.current_fuel and state.current_fuel.deficit > 0:
        alerts.append("FUEL_SHORT")
    if state.current_tires:
        _, wg = state.current_tires.worst_grip()
        if wg < 0.7:
            alerts.append("TIRES_CRITICAL")
        elif wg < 0.85:
            alerts.append("TIRES_WORN")
    if s.pit_window_open:
        alerts.append("PIT_WINDOW_OPEN")
    if state.damage and state.damage.has_significant_damage:
        alerts.append("DAMAGE")
    if alerts:
        result["alerts"] = alerts

    return _json_safe(result)


@mcp.tool()
def tire_state() -> str:
    """Current tire state: grip, wear, temperatures and trend.

    Returns all 4 wheels with grip (0-1), wear, temp vs optimal,
    and grip evolution over recent laps. Useful for deciding when
    to pit and which tires are suffering most.
    """
    tracker = _ensure_collector()
    state = tracker.state

    if state.current_tires is None:
        return "No tire data available. RaceRoom not on track?"

    tires = state.current_tires
    worst_name, worst_grip = tires.worst_grip()

    grip_history: list[dict[str, float]] = []
    for lap in state.lap_history[-10:]:
        grip_history.append({
            "lap": lap.lap_number,
            "FL": lap.tires.front_left.grip,
            "FR": lap.tires.front_right.grip,
            "RL": lap.tires.rear_left.grip,
            "RR": lap.tires.rear_right.grip,
        })

    grip_trend = ""
    if len(grip_history) >= 3:
        recent_worst = [h[worst_name] for h in grip_history[-3:]]
        drop_per_lap = (recent_worst[0] - recent_worst[-1]) / len(recent_worst)
        if drop_per_lap > 0.005:
            laps_to_threshold = (worst_grip - 0.5) / drop_per_lap if drop_per_lap > 0 else 999
            grip_trend = (
                f"Worst tire ({worst_name}): dropping ~{drop_per_lap:.3f}/lap. "
                f"~{laps_to_threshold:.0f} laps to grip 0.50"
            )

    result = {
        "current": {
            "FL": {"grip": tires.front_left.grip, "wear": tires.front_left.wear,
                   "temp_c": tires.front_left.temp_center, "temp_opt": tires.front_left.temp_optimal},
            "FR": {"grip": tires.front_right.grip, "wear": tires.front_right.wear,
                   "temp_c": tires.front_right.temp_center, "temp_opt": tires.front_right.temp_optimal},
            "RL": {"grip": tires.rear_left.grip, "wear": tires.rear_left.wear,
                   "temp_c": tires.rear_left.temp_center, "temp_opt": tires.rear_left.temp_optimal},
            "RR": {"grip": tires.rear_right.grip, "wear": tires.rear_right.wear,
                   "temp_c": tires.rear_right.temp_center, "temp_opt": tires.rear_right.temp_optimal},
        },
        "avg_grip": tires.avg_grip(),
        "worst_tire": {"name": worst_name, "grip": worst_grip},
        "compound_front": tires.compound_front.name,
        "compound_rear": tires.compound_rear.name,
        "grip_trend": grip_trend,
        "grip_history": grip_history,
    }
    return _json_safe(result)


@mcp.tool()
def fuel_state() -> str:
    """Fuel state: remaining, consumption, lap estimate and deficit.

    Shows whether you can finish without refueling and how much you'd need.
    """
    tracker = _ensure_collector()
    state = tracker.state

    if state.current_fuel is None:
        return "No fuel data available."

    fuel = state.current_fuel
    fuel_history = [
        {"lap": l.lap_number, "fuel_left": l.fuel.fuel_left_liters}
        for l in state.lap_history[-10:]
    ]

    result = {
        "fuel_left_liters": round(fuel.fuel_left_liters, 2),
        "fuel_capacity_liters": fuel.fuel_capacity_liters,
        "fuel_per_lap": round(fuel.fuel_per_lap, 3),
        "laps_on_remaining_fuel": round(fuel.laps_remaining_fuel, 1),
        "laps_remaining_session": fuel.laps_remaining_session,
        "deficit_liters": round(fuel.deficit, 2),
        "can_finish_without_stop": fuel.deficit <= 0,
        "fuel_history": fuel_history,
    }
    return _json_safe(result)


@mcp.tool()
def pit_strategy() -> str:
    """Pit strategy analysis: available gaps, estimated post-pit position and window.

    Examines traffic gaps to find the best moment to pit.
    """
    tracker = _ensure_collector()
    state = tracker.state

    if state.session is None:
        return "No active session."

    gaps: list[dict[str, Any]] = []
    sorted_rivals = sorted(
        [r.laps[-1] for r in state.rivals.values() if r.laps],
        key=lambda r: r.position,
    )

    for idx in range(len(sorted_rivals) - 1):
        r1 = sorted_rivals[idx]
        r2 = sorted_rivals[idx + 1]
        gap_between = abs(r2.gap_to_player - r1.gap_to_player)
        if gap_between > 3.0:
            gaps.append({
                "between_positions": f"P{r1.position}-P{r2.position}",
                "gap_seconds": round(gap_between, 1),
                "names": f"{r1.info.name} / {r2.info.name}",
            })

    pit_time_estimate = 25.0
    current_gap_front = state.lap_history[-1].gap_front if state.lap_history else -1
    current_gap_behind = state.lap_history[-1].gap_behind if state.lap_history else -1

    result = {
        "player_position": f"P{state.session.player_position}" if state.session else "?",
        "player_lap": state.session.player_lap if state.session else -1,
        "laps_remaining": state.session.laps_remaining if state.session else -1,
        "pit_window": {
            "open": state.session.pit_window_open,
            "start": state.session.pit_window_start,
            "end": state.session.pit_window_end,
        },
        "gap_to_car_ahead_s": current_gap_front,
        "gap_to_car_behind_s": current_gap_behind,
        "pit_time_estimate_s": pit_time_estimate,
        "traffic_gaps_gt_3s": gaps,
        "rivals_in_pit": [
            {"name": r.laps[-1].info.name, "position": r.laps[-1].position}
            for r in state.rivals.values()
            if r.laps and r.laps[-1].in_pitlane
        ],
        "rivals_already_pitted": [
            {"name": r.info.name, "total_stops": r.total_pitstops}
            for r in state.rivals.values()
            if r.has_pitted
        ],
    }
    return _json_safe(result)


@mcp.tool()
def driver_state(count: int = 5) -> str:
    """Nearby drivers including the player.

    Returns the N closest drivers by gap plus the player marked with
    is_player=true. Each entry includes: position, lap, gap, current
    lap sectors, pace, trend, pit info and tires. Sorted by race position.

    Args:
        count: Number of rivals to include (default 5). The player
               is always included as extra.
    """
    tracker = _ensure_collector()
    state = tracker.state

    if not state.rivals and not state.session:
        return "No data available."

    player_lap = state.session.player_lap if state.session else 0

    # ── Construir entrada del jugador ──
    player_entry: dict[str, Any] | None = None
    shared = tracker._last_shared
    if state.session and shared:
        s = state.session
        cur_s = shared.sector_time_current_self
        player_entry = {
            "name": "Player",
            "is_player": True,
            "position": f"P{s.player_position}",
            "current_lap": s.player_lap,
            "track_progress_pct": round(shared.lap_distance_fraction * 100, 1),
            "race_status": "NONE",
            "gap_to_you_s": 0,
            "sectors": {
                "s1": round(cur_s[0], 3) if cur_s[0] > 0 else "N/A",
                "s2": round(cur_s[1], 3) if cur_s[1] > 0 else "N/A",
                "s3": round(cur_s[2], 3) if cur_s[2] > 0 else "N/A",
            },
            "in_pitlane": shared.pit_limiter == 1,
            "tire_front": state.current_tires.compound_front.name if state.current_tires else "N/A",
            "tire_rear": state.current_tires.compound_rear.name if state.current_tires else "N/A",
        }
        # Ritmo del jugador
        recent = [l.lap_time for l in state.lap_history[-3:] if l.lap_time > 0]
        if recent:
            player_entry["recent_pace_s"] = round(sum(recent) / len(recent), 3)
        else:
            player_entry["recent_pace_s"] = "N/A"
        if shared.lap_time_best_self > 0:
            player_entry["best_lap_s"] = round(shared.lap_time_best_self, 3)
        else:
            player_entry["best_lap_s"] = "N/A"
        if shared.lap_time_current_self > 0:
            player_entry["current_lap_time_s"] = round(shared.lap_time_current_self, 3)
        player_entry["total_pitstops"] = shared.num_pitstops

    # ── Construir entradas de rivales ──
    rivals_data = []
    for rival in sorted(
        state.rivals.values(),
        key=lambda r: abs(r.laps[-1].gap_to_player) if r.laps else 999,
    ):
        if not rival.laps or len(rivals_data) >= count:
            break
        latest = rival.laps[-1]

        lap_diff = latest.completed_laps - (player_lap - 1)
        if lap_diff > 0:
            lap_status = f"+{lap_diff} lap{'s' if lap_diff > 1 else ''}"
        elif lap_diff < 0:
            lap_status = f"{lap_diff} lap{'s' if abs(lap_diff) > 1 else ''}"
        else:
            lap_status = "same_lap"

        entry: dict[str, Any] = {
            "name": rival.info.name,
            "car_number": rival.info.car_number,
            "position": f"P{latest.position}",
            "position_class": latest.position_class,
            "current_lap": latest.current_lap,
            "lap_status": lap_status,
            "track_progress_pct": round(latest.lap_distance_fraction * 100, 1),
            "race_status": latest.finish_status.name,
            "gap_to_you_s": round(latest.gap_to_player, 2),
            "recent_pace_s": round(rival.recent_pace, 3) if rival.recent_pace > 0 else "N/A",
            "best_lap_s": round(rival.best_lap_time, 3) if rival.best_lap_time > 0 else "N/A",
            "pace_trend": rival.pace_trend,
            "sectors": _format_sectors(latest),
            "in_pitlane": latest.in_pitlane,
            "total_pitstops": rival.total_pitstops,
            "tire_front": latest.tire_front.name,
            "tire_rear": latest.tire_rear.name,
        }

        if latest.current_lap_time > 0:
            entry["current_lap_time_s"] = round(latest.current_lap_time, 3)
        if not latest.current_lap_valid:
            entry["current_lap_invalid"] = True

        rivals_data.append(entry)

    # ── Fusionar y ordenar por posición ──
    all_drivers = rivals_data
    if player_entry:
        all_drivers.append(player_entry)
    all_drivers.sort(key=lambda d: int(d["position"][1:]))

    return _json_safe(all_drivers)


@mcp.tool()
def driver_laps(name: str = "", count: int = 5) -> str:
    """Lap-by-lap history for drivers, including the player.

    Each lap includes: lap number, time, sectors (s1/s2/s3), position
    at completion, tires and pit stop count. The player appears as
    "Player" with is_player=true.

    Search "player" to get only the player's laps.

    Useful for analyzing pace, tire degradation, pit stop effects
    and comparing drivers lap by lap.

    Args:
        name: Partial driver name. "player" for player only.
              Empty returns all drivers with history.
        count: Max number of drivers to include (default 5).
    """
    tracker = _ensure_collector()
    state = tracker.state

    search = name.strip().lower()

    # ── Entrada del jugador (mismo esquema que rivales) ──
    player_result = None
    if state.lap_history and (not search or "player" in search):
        laps_data = []
        for lap in state.lap_history:
            laps_data.append({
                "lap": lap.lap_number,
                "time_s": round(lap.lap_time, 3),
                "sectors": {
                    "s1": round(lap.sectors.s1, 3) if lap.sectors.s1 > 0 else "N/A",
                    "s2": round(lap.sectors.s2, 3) if lap.sectors.s2 > 0 else "N/A",
                    "s3": round(lap.sectors.s3, 3) if lap.sectors.s3 > 0 else "N/A",
                },
                "position": f"P{lap.position}",
                "tires": f"{lap.tires.compound_front.name}/{lap.tires.compound_rear.name}",
                "pitstops": lap.num_pitstops,
            })

        best = min((l.lap_time for l in state.lap_history if l.lap_time > 0), default=-1.0)
        player_result = {
            "name": "Player",
            "is_player": True,
            "current_position": f"P{state.session.player_position}" if state.session else "?",
            "best_lap_s": round(best, 3) if best > 0 else "N/A",
            "total_laps": len(state.lap_history),
            "laps": laps_data,
        }

    # ── Entradas de rivales ──
    candidates = []
    if not search or "player" not in search:
        for rival in state.rivals.values():
            if not rival.completed:
                continue
            if search and search not in rival.info.name.lower():
                continue
            candidates.append(rival)

    candidates.sort(
        key=lambda r: r.laps[-1].position if r.laps else 999,
    )

    result = []

    # Incluir al jugador
    if player_result:
        result.append(player_result)

    # Incluir rivales
    for rival in candidates[:count]:
        laps_data = []
        for rec in rival.completed:
            laps_data.append({
                "lap": rec.lap_number,
                "time_s": round(rec.lap_time, 3),
                "sectors": {
                    "s1": round(rec.sectors.s1, 3) if rec.sectors.s1 > 0 else "N/A",
                    "s2": round(rec.sectors.s2, 3) if rec.sectors.s2 > 0 else "N/A",
                    "s3": round(rec.sectors.s3, 3) if rec.sectors.s3 > 0 else "N/A",
                },
                "position": f"P{rec.position}",
                "tires": f"{rec.tire_front.name}/{rec.tire_rear.name}",
                "pitstops": rec.num_pitstops,
            })

        latest = rival.laps[-1] if rival.laps else None
        result.append({
            "name": rival.info.name,
            "car_number": rival.info.car_number,
            "current_position": f"P{latest.position}" if latest else "?",
            "best_lap_s": round(rival.best_lap_time, 3) if rival.best_lap_time > 0 else "N/A",
            "total_laps": len(rival.completed),
            "laps": laps_data,
        })

    if not result:
        if search:
            return f"No driver matching '{name}' found or no completed laps."
        return "No driver has completed laps yet."

    return _json_safe(result)


@mcp.tool()
def player_state() -> str:
    """Current player state: position, lap, gaps ahead/behind, recent pace,
    current lap sectors, tire/fuel/damage summaries.

    The go-to tool for a complete player snapshot in one call.
    """
    tracker = _ensure_collector()
    state = tracker.state

    result: dict[str, Any] = {}

    # Si la carrera ha terminado, mostrarlo de forma prominente
    if state.player_finish_status.value > 0:
        result["race_finished"] = True
        result["finish_status"] = state.player_finish_status.name
        result["finish_position"] = f"P{state.finish_position}" if state.finish_position > 0 else "unknown"

    if state.session:
        s = state.session
        total = s.laps_total_estimate if s.laps_total_estimate > 0 else s.laps_total
        result["position"] = f"P{s.player_position}"
        result["current_lap"] = s.player_lap
        result["laps_total"] = total
        result["laps_remaining"] = s.laps_remaining
        result["num_cars"] = s.num_cars

    # Gap al coche de delante y de detrás (del top-level shared memory)
    shared = tracker._last_shared if hasattr(tracker, '_last_shared') else None
    if shared and hasattr(shared, 'time_delta_front'):
        front = shared.time_delta_front
        behind = shared.time_delta_behind
        if front > 0:
            result["gap_ahead_s"] = round(front, 3)
        if behind > 0:
            result["gap_behind_s"] = round(behind, 3)

    # Sectores de la vuelta en curso
    if shared:
        cur_s = shared.sector_time_current_self
        result["current_sectors"] = {
            "s1": round(cur_s[0], 3) if cur_s[0] > 0 else "N/A",
            "s2": round(cur_s[1], 3) if cur_s[1] > 0 else "N/A",
            "s3": round(cur_s[2], 3) if cur_s[2] > 0 else "N/A",
        }
        if shared.lap_time_current_self > 0:
            result["current_lap_time_s"] = round(shared.lap_time_current_self, 3)
        if shared.lap_time_best_self > 0:
            result["best_lap_s"] = round(shared.lap_time_best_self, 3)
        if shared.lap_time_delta_leader > 0:
            result["delta_to_leader_s"] = round(shared.lap_time_delta_leader, 3)

    # Ritmo reciente (últimas 5 vueltas)
    recent_laps = state.lap_history[-5:]
    if recent_laps:
        times = [l.lap_time for l in recent_laps if l.lap_time > 0]
        if times:
            result["recent_pace_s"] = round(sum(times) / len(times), 3)
            result["last_lap_s"] = round(times[-1], 3)

    # Resumen de neumáticos
    if state.current_tires:
        t = state.current_tires
        result["tires"] = {
            "compound": f"{t.compound_front.name}/{t.compound_rear.name}",
            "avg_grip": round(t.avg_grip(), 3),
            "worst_wear": round(max(
                t.front_left.wear, t.front_right.wear,
                t.rear_left.wear, t.rear_right.wear,
            ), 3),
        }

    # Resumen de combustible
    if state.current_fuel:
        f = state.current_fuel
        result["fuel"] = {
            "remaining_liters": round(f.fuel_left_liters, 1),
            "laps_remaining": round(f.laps_remaining_fuel, 1),
            "per_lap": round(f.fuel_per_lap, 2),
            "deficit": round(f.deficit, 1),
        }

    # Daños (solo si hay)
    if state.damage and state.damage.has_significant_damage:
        result["damage"] = {
            "engine": round(state.damage.engine, 2),
            "transmission": round(state.damage.transmission, 2),
            "aero": round(state.damage.aerodynamics, 2),
            "suspension": round(state.damage.suspension, 2),
        }

    return _json_safe(result) if result else "No player data available."


@mcp.tool()
def race_state() -> str:
    """Race session overview: track, format, phase and global state.

    High-level session info. For player data use player_state.
    """
    tracker = _ensure_collector()
    state = tracker.state

    pace_history = [
        {"lap": l.lap_number, "time": round(l.lap_time, 3),
         "valid": l.valid, "position": l.position}
        for l in state.lap_history[-10:]
    ]

    result: dict[str, Any] = {}

    if state.session:
        s = state.session
        total = s.laps_total_estimate if s.laps_total_estimate > 0 else s.laps_total
        session_info: dict[str, Any] = {
            "track": f"{s.track_name} ({s.layout_name})",
            "type": s.session_type.name,
            "phase": s.session_phase.name,
            "player_lap": s.player_lap,
            "leader_lap": s.leader_lap,
            "laps_total": total,
            "laps_remaining": s.laps_remaining,
            "player_position": f"P{s.player_position}",
            "time_based": s.time_based,
            "num_cars": s.num_cars,
        }
        if s.time_based:
            session_info["session_duration_s"] = s.session_duration_s
            session_info["time_remaining_s"] = s.time_remaining_s
            if s.laps_total_estimate > 0:
                session_info["laps_estimated"] = True
        else:
            session_info["time_remaining_s"] = s.time_remaining_s
        result["session"] = session_info

    # Estado de finalización del jugador
    if state.player_finish_status.value > 0:
        result["player_finish"] = {
            "status": state.player_finish_status.name,
            "position": f"P{state.finish_position}" if state.finish_position > 0 else "unknown",
        }

    if state.damage and state.damage.has_significant_damage:
        result["damage"] = {
            "engine": state.damage.engine,
            "transmission": state.damage.transmission,
            "aero": state.damage.aerodynamics,
            "suspension": state.damage.suspension,
        }

    result["pace_history"] = pace_history

    if state.current_tires:
        result["avg_grip"] = round(state.current_tires.avg_grip(), 3)

    if state.current_fuel:
        result["fuel_laps_remaining"] = round(state.current_fuel.laps_remaining_fuel, 1)

    return _json_safe(result)



# ── Entrypoint ────────────────────────────────────────────────────────


@mcp.tool()
def player_laps(last_n: int = 10) -> str:
    """Detailed history of the player's last N laps.

    Includes extended fields not in driver_laps: per-wheel grip,
    fuel remaining and consumption per lap, gaps to car ahead/behind,
    delta vs personal best, and validity.

    For comparing with rivals on equal fields, use driver_laps.

    Args:
        last_n: Number of laps to return (default 10).
    """
    tracker = _ensure_collector()
    state = tracker.state

    if not state.lap_history:
        return "No completed laps yet."

    laps = []
    for lap in state.lap_history[-last_n:]:
        laps.append({
            "lap": lap.lap_number,
            "time_s": round(lap.lap_time, 3),
            "valid": lap.valid,
            "sectors": {
                "s1": round(lap.sectors.s1, 3) if lap.sectors.s1 > 0 else "N/A",
                "s2": round(lap.sectors.s2, 3) if lap.sectors.s2 > 0 else "N/A",
                "s3": round(lap.sectors.s3, 3) if lap.sectors.s3 > 0 else "N/A",
            },
            "position": f"P{lap.position}",
            "tires": f"{lap.tires.compound_front.name}/{lap.tires.compound_rear.name}",
            "grip_avg": round(lap.tires.avg_grip(), 3),
            "grip_worst": {
                "tire": lap.tires.worst_grip()[0],
                "value": round(lap.tires.worst_grip()[1], 3),
            },
            "fuel_left": round(lap.fuel.fuel_left_liters, 2),
            "fuel_per_lap": round(lap.fuel.fuel_per_lap, 2),
            "pitstops": lap.num_pitstops,
            "gap_front_s": round(lap.gap_front, 3) if lap.gap_front > 0 else "N/A",
            "gap_behind_s": round(lap.gap_behind, 3) if lap.gap_behind > 0 else "N/A",
            "delta_best_s": round(lap.delta_best_self, 3) if abs(lap.delta_best_self) < 999 else "N/A",
        })

    return _json_safe(laps)


# ── Entrypoint ────────────────────────────────────────────────────────


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
    )

    logger.info("=== Race Engineer MCP Server ===")
    logger.info("URL publica: %s", PUBLIC_URL)
    logger.info("MCP endpoint: %s/mcp", PUBLIC_URL)
    logger.info("Puerto local: %d", MCP_PORT)

    if OAUTH_PIN:
        logger.info("Autorizacion con PIN activada")
    else:
        logger.info("AVISO: sin PIN, cualquiera con la URL puede autorizar")

    # Extraer hostname del tunnel para permitirlo en el Host header
    tunnel_host = urlparse(PUBLIC_URL).netloc
    allowed = [tunnel_host, f"localhost:{MCP_PORT}", f"127.0.0.1:{MCP_PORT}"]
    logger.info("Hosts permitidos: %s", allowed)

    logger.info("Arrancando collector...")
    _ensure_collector()

    logger.info("Servidor listo — esperando conexiones")
    mcp.run(
        transport="streamable-http",
        port=MCP_PORT,
        transport_security=TransportSecuritySettings(
            allowed_hosts=allowed,
        ),
    )


if __name__ == "__main__":
    main()
