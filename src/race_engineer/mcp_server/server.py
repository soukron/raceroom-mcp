"""MCP server del race engineer.

Expone herramientas (tools) que consultan el RaceState mantenido por el collector.
Arranca el collector en un hilo de fondo y expone los datos procesados.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..collector.reader import R3EReader
from ..collector.tracker import RaceTracker

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "race-engineer",
    instructions=(
        "Servidor MCP de telemetría para RaceRoom Racing Experience. "
        "Proporciona datos de neumáticos, combustible, rivales y estrategia de pit."
    ),
)

# Estado global compartido entre el collector thread y los tools
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
    logger.info("Collector iniciado a %.0f Hz", poll_rate_hz)

    while _running:
        shared = _reader.read()
        if shared is not None:
            _tracker.update(shared)
        time.sleep(interval)


def _ensure_collector() -> RaceTracker:
    """Arranca collector si no está corriendo."""
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


def _json_safe(obj: Any) -> str:
    """Serializa dataclasses a JSON legible."""
    def default(o: Any) -> Any:
        if hasattr(o, "__dataclass_fields__"):
            return asdict(o)
        return str(o)

    return json.dumps(obj, default=default, ensure_ascii=False, indent=2)


# ── Tools MCP ─────────────────────────────────────────────────────────


@mcp.tool()
def tire_status() -> str:
    """Estado actual de los neumáticos: grip, desgaste, temperaturas y tendencia.

    Devuelve el estado de las 4 ruedas con datos de grip (0-1), desgaste,
    temperatura vs óptima, y la evolución de grip en las últimas vueltas.
    Útil para decidir cuándo parar y qué neumáticos están sufriendo más.
    """
    tracker = _ensure_collector()
    state = tracker.state

    if state.current_tires is None:
        return "No hay datos de neumáticos disponibles. ¿RaceRoom está en pista?"

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
            grip_trend = f"Peor rueda ({worst_name}): cayendo ~{drop_per_lap:.3f}/vuelta. ~{laps_to_threshold:.0f} vueltas hasta grip 0.50"

    result = {
        "current": {
            "FL": {"grip": tires.front_left.grip, "wear": tires.front_left.wear, "temp_c": tires.front_left.temp_center, "temp_opt": tires.front_left.temp_optimal},
            "FR": {"grip": tires.front_right.grip, "wear": tires.front_right.wear, "temp_c": tires.front_right.temp_center, "temp_opt": tires.front_right.temp_optimal},
            "RL": {"grip": tires.rear_left.grip, "wear": tires.rear_left.wear, "temp_c": tires.rear_left.temp_center, "temp_opt": tires.rear_left.temp_optimal},
            "RR": {"grip": tires.rear_right.grip, "wear": tires.rear_right.wear, "temp_c": tires.rear_right.temp_center, "temp_opt": tires.rear_right.temp_optimal},
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
def fuel_status() -> str:
    """Estado del combustible: restante, consumo, estimación de vueltas y déficit.

    Muestra si llegas al final sin repostar y cuánto necesitarías en un pit.
    """
    tracker = _ensure_collector()
    state = tracker.state

    if state.current_fuel is None:
        return "No hay datos de combustible disponibles."

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
    """Análisis de estrategia de pit: gaps disponibles, posición estimada post-pit y ventana obligatoria.

    Examina los huecos en el tráfico para encontrar el mejor momento de entrada a boxes.
    """
    tracker = _ensure_collector()
    state = tracker.state

    if state.session is None:
        return "No hay sesión activa."

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

    pit_time_estimate = 25.0  # segundos de pit estimados (ajustable)
    current_gap_front = state.lap_history[-1].gap_front if state.lap_history else -1
    current_gap_behind = state.lap_history[-1].gap_behind if state.lap_history else -1

    result = {
        "current_position": state.session.current_lap if state.session else -1,
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
def rivals_report(count: int = 5) -> str:
    """Informe de los rivales directos: ritmo, gaps, paradas y neumáticos.

    Muestra los N rivales más cercanos por posición, su ritmo reciente,
    si han parado, con qué neumáticos van y cómo evoluciona el gap.

    Args:
        count: Número de rivales a incluir (por defecto 5).
    """
    tracker = _ensure_collector()
    state = tracker.state

    if not state.rivals:
        return "No hay datos de rivales disponibles."

    rivals_data = []
    for rival in sorted(state.rivals.values(), key=lambda r: abs(r.laps[-1].gap_to_player) if r.laps else 999):
        if not rival.laps or len(rivals_data) >= count:
            break
        latest = rival.laps[-1]
        rivals_data.append({
            "name": rival.info.name,
            "car_number": rival.info.car_number,
            "position": latest.position,
            "position_class": latest.position_class,
            "gap_to_you_s": round(latest.gap_to_player, 2),
            "recent_pace_s": round(rival.recent_pace, 3) if rival.recent_pace > 0 else "N/A",
            "last_sectors": {"s1": latest.last_sectors.s1, "s2": latest.last_sectors.s2, "s3": latest.last_sectors.s3},
            "best_sectors": {"s1": latest.best_sectors.s1, "s2": latest.best_sectors.s2, "s3": latest.best_sectors.s3},
            "in_pitlane": latest.in_pitlane,
            "total_pitstops": rival.total_pitstops,
            "tire_front": latest.tire_front.name,
            "tire_rear": latest.tire_rear.name,
        })

    return _json_safe(rivals_data)


@mcp.tool()
def race_overview() -> str:
    """Resumen general de la carrera: sesión, posición, vueltas, daños y tendencia de ritmo.

    Visión de alto nivel para que el ingeniero entienda la situación completa.
    """
    tracker = _ensure_collector()
    state = tracker.state

    pace_history = [
        {"lap": l.lap_number, "time": round(l.lap_time, 3), "valid": l.valid, "position": l.position}
        for l in state.lap_history[-10:]
    ]

    result: dict[str, Any] = {}

    if state.session:
        result["session"] = {
            "track": f"{state.session.track_name} ({state.session.layout_name})",
            "type": state.session.session_type.name,
            "phase": state.session.session_phase.name,
            "lap": state.session.current_lap,
            "laps_total": state.session.laps_total,
            "time_remaining_s": state.session.time_remaining_s,
            "num_cars": state.session.num_cars,
        }

    if state.damage and state.damage.has_significant_damage:
        result["damage"] = {
            "engine": state.damage.engine,
            "transmission": state.damage.transmission,
            "aerodynamics": state.damage.aerodynamics,
            "suspension": state.damage.suspension,
        }

    result["pace_history"] = pace_history

    if state.current_tires:
        result["avg_grip"] = round(state.current_tires.avg_grip(), 3)

    if state.current_fuel:
        result["fuel_laps_remaining"] = round(state.current_fuel.laps_remaining_fuel, 1)

    return _json_safe(result)


@mcp.tool()
def lap_history(last_n: int = 10) -> str:
    """Histórico de las últimas N vueltas con tiempos, parciales, grip y combustible.

    Ideal para analizar evolución del ritmo y decidir estrategia.

    Args:
        last_n: Número de vueltas a devolver (por defecto 10).
    """
    tracker = _ensure_collector()
    state = tracker.state

    if not state.lap_history:
        return "No hay vueltas completadas todavía."

    laps = []
    for lap in state.lap_history[-last_n:]:
        laps.append({
            "lap": lap.lap_number,
            "time": round(lap.lap_time, 3),
            "sectors": {"s1": lap.sectors.s1, "s2": lap.sectors.s2, "s3": lap.sectors.s3},
            "valid": lap.valid,
            "position": lap.position,
            "grip_avg": round(lap.tires.avg_grip(), 3),
            "grip_worst": {"tire": lap.tires.worst_grip()[0], "value": round(lap.tires.worst_grip()[1], 3)},
            "fuel_left": round(lap.fuel.fuel_left_liters, 2),
            "gap_front": lap.gap_front,
            "gap_behind": lap.gap_behind,
            "delta_best": lap.delta_best_self,
        })

    return _json_safe(laps)


# ── Entrypoint ────────────────────────────────────────────────────────


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    logger.info("Arrancando Race Engineer MCP server...")
    _ensure_collector()
    mcp.run()


if __name__ == "__main__":
    main()
