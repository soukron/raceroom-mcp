"""Modelos de datos del race engineer.

Representan el estado procesado de la telemetría, no el raw de shared memory.
El collector transforma r3e_shared → estos modelos cada vuelta.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class SessionType(IntEnum):
    UNAVAILABLE = -1
    PRACTICE = 0
    QUALIFY = 1
    RACE = 2
    WARMUP = 3


class SessionPhase(IntEnum):
    UNAVAILABLE = -1
    GARAGE = 1
    GRIDWALK = 2
    FORMATION = 3
    COUNTDOWN = 4
    GREEN = 5
    CHECKERED = 6


class TireCompound(IntEnum):
    UNAVAILABLE = -1
    OPTION = 0
    PRIME = 1


class TireSubtype(IntEnum):
    UNAVAILABLE = -1
    PRIMARY = 0
    ALTERNATE = 1
    SOFT = 2
    MEDIUM = 3
    HARD = 4


class FinishStatus(IntEnum):
    UNAVAILABLE = -1
    NONE = 0       # sigue en carrera
    FINISHED = 1
    DNF = 2
    DNQ = 3
    DNS = 4
    DQ = 5


# ── Snapshots por vuelta ─────────────────────────────────────────────


@dataclass
class TireState:
    """Estado de un neumático en un momento dado."""

    grip: float  # 0.0 - 1.0
    wear: float  # 0.0 - 1.0
    temp_inner: float  # °C
    temp_center: float  # °C
    temp_outer: float  # °C
    temp_optimal: float  # °C
    pressure_kpa: float
    dirt: float  # 0.0 - 1.0
    has_flatspot: bool


@dataclass
class TireSetState:
    """Las 4 ruedas."""

    front_left: TireState
    front_right: TireState
    rear_left: TireState
    rear_right: TireState
    compound_front: TireSubtype = TireSubtype.UNAVAILABLE
    compound_rear: TireSubtype = TireSubtype.UNAVAILABLE

    def avg_grip(self) -> float:
        return (
            self.front_left.grip
            + self.front_right.grip
            + self.rear_left.grip
            + self.rear_right.grip
        ) / 4

    def worst_grip(self) -> tuple[str, float]:
        tires = {
            "FL": self.front_left.grip,
            "FR": self.front_right.grip,
            "RL": self.rear_left.grip,
            "RR": self.rear_right.grip,
        }
        worst = min(tires, key=tires.get)  # type: ignore[arg-type]
        return worst, tires[worst]


@dataclass
class FuelState:
    """Estado de combustible."""

    fuel_left_liters: float
    fuel_capacity_liters: float
    fuel_per_lap: float  # media calculada por el collector
    laps_remaining_fuel: float  # fuel_left / fuel_per_lap
    laps_remaining_session: int  # vueltas que quedan en la sesión
    deficit: float  # litros que faltan para acabar (negativo = sobra)


@dataclass
class SectorTimes:
    """Tiempos por sector."""

    s1: float  # segundos, -1.0 = N/A
    s2: float
    s3: float

    @property
    def total(self) -> float:
        if self.s1 < 0 or self.s2 < 0 or self.s3 < 0:
            return -1.0
        return self.s1 + self.s2 + self.s3


@dataclass
class LapData:
    """Snapshot completo al final de una vuelta."""

    lap_number: int
    lap_time: float  # segundos
    sectors: SectorTimes
    sectors_best: SectorTimes
    tires: TireSetState
    fuel: FuelState
    position: int
    position_class: int
    gap_front: float  # segundos al coche de delante
    gap_behind: float  # segundos al coche de detrás
    delta_best_self: float  # delta vs mejor vuelta propia
    delta_leader: float
    delta_leader_class: float
    car_speed_avg: float  # calculada por el collector
    num_pitstops: int  # paradas acumuladas al completar la vuelta
    valid: bool
    timestamp: float  # time.time() del snapshot


# ── Rivales ──────────────────────────────────────────────────────────


@dataclass
class RivalInfo:
    """Info estática de un rival."""

    name: str
    car_number: int
    class_id: int
    model_id: int
    slot_id: int


@dataclass
class RivalState:
    """Estado de un rival en el momento actual."""

    info: RivalInfo
    position: int
    position_class: int
    completed_laps: int
    current_lap: int  # completed_laps + 1
    lap_distance_fraction: float  # 0.0-1.0, progreso en la vuelta actual
    finish_status: FinishStatus  # NONE=en carrera, FINISHED, DNF, etc.
    gap_to_player: float  # positivo = delante, negativo = detrás
    last_lap_time: float
    current_lap_time: float  # tiempo de la vuelta en curso (-1 si no disponible)
    current_lap_valid: bool  # si la vuelta en curso es válida
    current_sectors: SectorTimes  # sectores de la vuelta en curso (progresivos)
    last_sectors: SectorTimes
    best_sectors: SectorTimes
    car_speed: float  # m/s
    in_pitlane: bool
    pitstop_status: int  # fase del pit (-1=N/A, 0=none, 1=entering, etc.)
    num_pitstops: int
    tire_front: TireSubtype
    tire_rear: TireSubtype
    drs_active: bool
    ptp_active: bool


@dataclass
class RivalLapRecord:
    """Registro de una vuelta completada por un rival."""
    lap_number: int
    lap_time: float
    sectors: SectorTimes
    position: int  # posición al completar la vuelta
    tire_front: TireSubtype
    tire_rear: TireSubtype
    num_pitstops: int


@dataclass
class RivalHistory:
    """Histórico de un rival a lo largo de la carrera.

    ``laps`` contiene snapshots recientes (limitados por el tracker).
    ``completed`` acumula un registro por vuelta completada y sobrevive
    al trim de snapshots — fuente fiable para ritmo, tendencia e histórico.
    """

    info: RivalInfo
    laps: list[RivalState] = field(default_factory=list)
    completed: list[RivalLapRecord] = field(default_factory=list)
    _best_lap: float = field(default=-1.0, repr=False)

    def record_lap(self, rival_state: RivalState) -> None:
        """Registra una vuelta completada a partir del snapshot actual."""
        lap_time = rival_state.last_lap_time
        if lap_time <= 0:
            return
        self.completed.append(RivalLapRecord(
            lap_number=rival_state.completed_laps,  # acaban de completar esta
            lap_time=lap_time,
            sectors=rival_state.last_sectors,
            position=rival_state.position,
            tire_front=rival_state.tire_front,
            tire_rear=rival_state.tire_rear,
            num_pitstops=rival_state.num_pitstops,
        ))
        if self._best_lap < 0 or lap_time < self._best_lap:
            self._best_lap = lap_time

    @property
    def recent_pace(self) -> float:
        """Ritmo medio de las últimas 3 vueltas completadas."""
        recent = [r.lap_time for r in self.completed[-3:]]
        return sum(recent) / len(recent) if recent else -1.0

    @property
    def best_lap_time(self) -> float:
        """Mejor tiempo de vuelta del rival (toda la sesión)."""
        return self._best_lap

    @property
    def has_pitted(self) -> bool:
        if not self.laps:
            return False
        return self.laps[-1].num_pitstops > 0

    @property
    def total_pitstops(self) -> int:
        return self.laps[-1].num_pitstops if self.laps else 0

    @property
    def is_active(self) -> bool:
        """Sigue en carrera (no DNF/DQ/finished)."""
        if not self.laps:
            return True
        return self.laps[-1].finish_status in (FinishStatus.UNAVAILABLE, FinishStatus.NONE)

    @property
    def pace_trend(self) -> str:
        """Tendencia de ritmo basada en vueltas completadas."""
        times = [r.lap_time for r in self.completed[-6:]]
        if len(times) < 4:
            return "insufficient_data"
        mid = len(times) // 2
        first_half = sum(times[:mid]) / mid
        second_half = sum(times[mid:]) / (len(times) - mid)
        diff = second_half - first_half
        if diff < -0.3:
            return "improving"
        elif diff > 0.3:
            return "degrading"
        return "stable"


# ── Estado global de la sesión ───────────────────────────────────────


@dataclass
class SessionState:
    """Resumen del estado actual de la sesión."""

    session_type: SessionType
    session_phase: SessionPhase
    track_name: str
    layout_name: str
    layout_length_m: float
    laps_total: int  # vueltas configuradas; -1 si es por tiempo
    laps_total_estimate: int  # estimación incluyendo sesiones por tiempo
    time_based: bool  # True si la sesión es por tiempo, no por vueltas
    session_duration_s: float  # duración total configurada (por tiempo)
    time_remaining_s: float  # -1.0 si es por vueltas
    player_lap: int  # vuelta actual del jugador (completed + 1)
    leader_lap: int  # vuelta actual del líder (P1)
    laps_remaining: int  # vueltas que quedan basado en líder; -1 si desconocido
    player_position: int  # posición actual del jugador
    num_cars: int
    pit_window_open: bool
    pit_window_start: int
    pit_window_end: int


@dataclass
class DamageState:
    """Estado de daños del coche del jugador."""

    engine: float  # 0.0 (perfecto) - 1.0 (destruido)
    transmission: float
    aerodynamics: float
    suspension: float

    @property
    def has_significant_damage(self) -> bool:
        return any(v > 0.1 for v in [self.engine, self.transmission, self.aerodynamics, self.suspension])


# ── Contenedor principal ─────────────────────────────────────────────


@dataclass
class RaceState:
    """Estado completo que el collector mantiene actualizado.

    Es la fuente de verdad que consultan tanto el MCP server como el agente.
    """

    session: SessionState | None = None
    current_tires: TireSetState | None = None
    current_fuel: FuelState | None = None
    damage: DamageState | None = None
    lap_history: list[LapData] = field(default_factory=list)
    rivals: dict[int, RivalHistory] = field(default_factory=dict)  # slot_id → history
    player_finish_status: FinishStatus = FinishStatus.NONE
    finish_position: int = 0  # posición final (se fija al cruzar meta)
    last_update: float = 0.0

    @property
    def current_lap(self) -> int:
        return self.session.player_lap if self.session else 0

    @property
    def laps_completed(self) -> int:
        return len(self.lap_history)
