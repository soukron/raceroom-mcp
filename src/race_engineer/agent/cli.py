"""Agente CLI del race engineer.

Modo interactivo: el piloto pregunta por terminal y recibe respuestas del
ingeniero de pista alimentado con telemetría en tiempo real.

Funciona como consumidor directo del collector + LLM (sin pasar por MCP).
Para uso desde Cursor u otro cliente MCP, usar el MCP server en su lugar.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time

from ..collector.reader import R3EReader
from ..collector.tracker import RaceTracker
from ..llm.client import LLMConfig, RaceEngineerLLM, load_config_from_yaml

logger = logging.getLogger(__name__)

# Comandos especiales del CLI
COMMANDS = {
    "/tires": "Estado de neumáticos",
    "/fuel": "Estado de combustible",
    "/pit": "Estrategia de pit",
    "/rivals": "Informe de rivales",
    "/race": "Resumen de carrera",
    "/laps": "Histórico de vueltas",
    "/clear": "Limpiar historial de conversación",
    "/quit": "Salir",
    "/help": "Mostrar comandos",
}


def _build_context(tracker: RaceTracker, focus: str = "general") -> str:
    """Construye el bloque de contexto para el LLM según el tipo de consulta."""
    state = tracker.state
    parts: list[str] = []

    if state.session:
        s = state.session
        parts.append(
            f"Sesión: {s.track_name} ({s.layout_name}), {s.session_type.name}, "
            f"vuelta {s.current_lap}/{s.laps_total if s.laps_total > 0 else '?'}, "
            f"{s.num_cars} coches"
        )

    if focus in ("general", "tires") and state.current_tires:
        t = state.current_tires
        parts.append(
            f"Neumáticos — grip: FL={t.front_left.grip:.2f} FR={t.front_right.grip:.2f} "
            f"RL={t.rear_left.grip:.2f} RR={t.rear_right.grip:.2f} | "
            f"desgaste: FL={t.front_left.wear:.2f} FR={t.front_right.wear:.2f} "
            f"RL={t.rear_left.wear:.2f} RR={t.rear_right.wear:.2f} | "
            f"compuesto: {t.compound_front.name}/{t.compound_rear.name}"
        )
        for lap in state.lap_history[-5:]:
            parts.append(
                f"  V{lap.lap_number}: grip FL={lap.tires.front_left.grip:.2f} "
                f"FR={lap.tires.front_right.grip:.2f} RL={lap.tires.rear_left.grip:.2f} "
                f"RR={lap.tires.rear_right.grip:.2f}"
            )

    if focus in ("general", "fuel") and state.current_fuel:
        f = state.current_fuel
        parts.append(
            f"Combustible: {f.fuel_left_liters:.1f}L / {f.fuel_capacity_liters:.0f}L, "
            f"{f.fuel_per_lap:.2f}L/vuelta, para {f.laps_remaining_fuel:.1f} vueltas, "
            f"faltan {f.laps_remaining_session} vueltas sesión, "
            f"déficit: {f.deficit:+.1f}L"
        )

    if focus in ("general", "pit", "rivals"):
        for rival in sorted(
            [r for r in state.rivals.values() if r.laps],
            key=lambda r: abs(r.laps[-1].gap_to_player),
        )[:5]:
            lat = rival.laps[-1]
            parts.append(
                f"Rival {lat.info.name} (#{rival.info.car_number}) P{lat.position}: "
                f"gap={lat.gap_to_player:+.2f}s, ritmo={rival.recent_pace:.3f}s, "
                f"paradas={rival.total_pitstops}, pits={'SÍ' if lat.in_pitlane else 'no'}, "
                f"neumaticos={lat.tire_front.name}/{lat.tire_rear.name}"
            )

    if focus in ("general", "laps"):
        for lap in state.lap_history[-5:]:
            parts.append(
                f"V{lap.lap_number}: {lap.lap_time:.3f}s "
                f"[{lap.sectors.s1:.3f}/{lap.sectors.s2:.3f}/{lap.sectors.s3:.3f}] "
                f"P{lap.position} fuel={lap.fuel.fuel_left_liters:.1f}L "
                f"{'✓' if lap.valid else '✗'}"
            )

    if state.damage and state.damage.has_significant_damage:
        d = state.damage
        parts.append(
            f"Daños: motor={d.engine:.0%} trans={d.transmission:.0%} "
            f"aero={d.aerodynamics:.0%} susp={d.suspension:.0%}"
        )

    return "\n".join(parts) if parts else "Sin datos de telemetría disponibles."


def _focus_for_command(cmd: str) -> str:
    mapping = {"/tires": "tires", "/fuel": "fuel", "/pit": "pit", "/rivals": "rivals", "/laps": "laps"}
    return mapping.get(cmd, "general")


def _implicit_question(cmd: str) -> str:
    mapping = {
        "/tires": "¿Cómo están los neumáticos? ¿Cuánto me queda de ritmo?",
        "/fuel": "¿Cómo voy de combustible? ¿Llego al final?",
        "/pit": "¿Cuándo debería entrar a boxes? ¿Hay un hueco limpio?",
        "/rivals": "¿Cómo van mis rivales directos? ¿Quién es la amenaza?",
        "/race": "Dame un resumen de cómo va la carrera.",
        "/laps": "Analiza mi ritmo en las últimas vueltas.",
    }
    return mapping.get(cmd, "Dame un resumen de la situación.")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    config_path = os.environ.get("RACE_ENGINEER_CONFIG", "config.yaml")
    if not os.path.exists(config_path):
        print(f"No se encuentra {config_path}. Copia config.example.yaml → config.yaml y ajústalo.")
        sys.exit(1)

    llm_config = load_config_from_yaml(config_path)
    llm = RaceEngineerLLM(llm_config)

    reader = R3EReader()
    tracker = RaceTracker()

    collector_running = threading.Event()

    def collector_loop() -> None:
        logger.info("Esperando a RaceRoom...")
        if not reader.wait_for_connection(timeout_s=600):
            logger.error("No se detectó RaceRoom en 10 minutos")
            return
        logger.info("Conectado a RaceRoom")
        collector_running.set()
        while collector_running.is_set():
            shared = reader.read()
            if shared is not None:
                tracker.update(shared)
            time.sleep(0.1)

    thread = threading.Thread(target=collector_loop, daemon=True)
    thread.start()

    print("╔═══════════════════════════════════════╗")
    print("║     🏎️  RACE ENGINEER v0.1.0  🏎️      ║")
    print("║  Ingeniero de pista IA para RaceRoom  ║")
    print("╠═══════════════════════════════════════╣")
    print("║  /tires  — neumáticos                 ║")
    print("║  /fuel   — combustible                ║")
    print("║  /pit    — estrategia de pit           ║")
    print("║  /rivals — rivales directos            ║")
    print("║  /race   — resumen general             ║")
    print("║  /laps   — histórico de vueltas        ║")
    print("║  /help   — todos los comandos          ║")
    print("║  /quit   — salir                       ║")
    print("║                                        ║")
    print("║  O escribe cualquier pregunta libre.   ║")
    print("╚═══════════════════════════════════════╝")
    print()

    if not collector_running.wait(timeout=5):
        print("⏳ Esperando a que RaceRoom arranque... (el CLI ya funciona)")

    while True:
        try:
            user_input = input("\n🏁 > ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input:
            continue

        if user_input == "/quit":
            break
        elif user_input == "/help":
            for cmd, desc in COMMANDS.items():
                print(f"  {cmd:10s} {desc}")
            continue
        elif user_input == "/clear":
            llm.clear_history()
            print("🗑️  Historial limpiado.")
            continue

        if user_input.startswith("/"):
            focus = _focus_for_command(user_input)
            question = _implicit_question(user_input)
        else:
            focus = "general"
            question = user_input

        context = _build_context(tracker, focus)

        print("📡 Consultando telemetría + LLM...")
        response = llm.ask(question, context)
        print(f"\n📻 Ingeniero:\n{response}")

    collector_running.clear()
    reader.close()
    print("\n👋 Sesión terminada.")


if __name__ == "__main__":
    main()
