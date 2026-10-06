"""Tracker de vueltas y rivales.

Transforma los snapshots crudos de R3EShared en modelos de alto nivel
(LapData, RivalState, etc.) y mantiene el histórico en RaceState.
"""

from __future__ import annotations

import logging
import time

from ..models import (
    DamageState,
    FinishStatus,
    FuelState,
    LapData,
    RaceState,
    RivalHistory,
    RivalInfo,
    RivalState,
    SectorTimes,
    SessionPhase,
    SessionState,
    SessionType,
    TireSetState,
    TireState,
    TireSubtype,
)
from .r3e_types import R3EShared

logger = logging.getLogger(__name__)


def _safe_enum(enum_cls, value, default=None):
    """Convierte un valor entero a enum, devolviendo default si no es válido."""
    try:
        return enum_cls(value)
    except ValueError:
        return default if default is not None else list(enum_cls)[0]


def _tire_state(shared: R3EShared, idx: int) -> TireState:
    """Extrae el estado de un neumático por índice (0=FL, 1=FR, 2=RL, 3=RR)."""
    temps = shared.tire_temp[idx]
    return TireState(
        grip=shared.tire_grip[idx],
        wear=shared.tire_wear[idx],
        temp_inner=temps.current_temp[0],  # left = inner en pista horaria
        temp_center=temps.current_temp[1],
        temp_outer=temps.current_temp[2],
        temp_optimal=temps.optimal_temp,
        pressure_kpa=shared.tire_pressure[idx],
        dirt=shared.tire_dirt[idx],
        has_flatspot=shared.tire_flatspot[idx] == 1,
    )


def _sector_times(s1: float, s2: float, s3: float) -> SectorTimes:
    return SectorTimes(s1=s1, s2=s2, s3=s3)


class RaceTracker:
    """Sigue la carrera vuelta a vuelta y mantiene el RaceState actualizado.

    Se llama a `update(shared)` con cada lectura de la shared memory (~10Hz).
    Detecta cambios de vuelta y acumula datos.
    """

    def __init__(self, lap_history_size: int = 50, rivals_count: int = 5) -> None:
        self.state = RaceState()
        self._lap_history_size = lap_history_size
        self._rivals_count = rivals_count
        self._last_completed_laps = -1
        self._speed_samples: list[float] = []
        self._fuel_samples: list[float] = []
        self._last_session_type: int = -1
        self._last_session_phase: int = -1
        self._last_shared: R3EShared | None = None  # última lectura raw

        # Estado para detección de eventos en tiempo real
        self._evt_in_pitlane: bool = False
        self._evt_best_lap: float = -1.0
        self._evt_race_best_lap: float = -1.0
        self._evt_race_best_driver: str = ""
        self._evt_damage_sum: float = 0.0
        self._evt_rivals_in_pit: set[int] = set()  # slot_ids en boxes
        self._evt_rivals_finished: set[int] = set()  # slot_ids que ya cruzaron meta
        # slot_id → (posición, nombre). Player usa slot _PLAYER_SLOT.
        self._evt_all_positions: dict[int, tuple[int, str]] = {}
        self._evt_grid_position: int = 0  # posición de salida del jugador

    def _reset_state(self, reason: str) -> None:
        """Limpia todo el estado acumulado al cambiar de sesión."""
        logger.info("Reset de estado: %s", reason)
        self.state.lap_history.clear()
        self.state.rivals.clear()
        self.state.current_tires = None
        self.state.current_fuel = None
        self.state.damage = None
        self.state.player_finish_status = FinishStatus.NONE
        self.state.finish_position = 0
        self._last_completed_laps = -1
        self._speed_samples.clear()
        self._fuel_samples.clear()
        self._evt_in_pitlane = False
        self._evt_best_lap = -1.0
        self._evt_race_best_lap = -1.0
        self._evt_race_best_driver = ""
        self._evt_damage_sum = 0.0
        self._evt_rivals_in_pit.clear()
        self._evt_rivals_finished.clear()
        self._evt_all_positions.clear()
        self._evt_grid_position = 0

    def _detect_session_change(self, shared: R3EShared) -> None:
        """Detecta cambio de sesión (qualy→carrera, etc.) y resetea."""
        session_type = shared.session_type
        session_phase = shared.session_phase

        if self._last_session_type == -1:
            # Primera lectura, solo registrar
            self._last_session_type = session_type
            self._last_session_phase = session_phase
            return

        # Cambio de tipo de sesión (ej: QUALIFY→RACE, PRACTICE→QUALIFY)
        if session_type != self._last_session_type:
            self._reset_state(
                f"cambio de sesión: {SessionType(self._last_session_type).name} → "
                f"{SessionType(session_type).name}"
            )
            self._last_session_type = session_type
            self._last_session_phase = session_phase
            return

        # Restart de la misma sesión (fase vuelve a FORMATION/COUNTDOWN
        # desde GREEN/CHECKERED)
        went_backwards = (
            self._last_session_phase in (SessionPhase.GREEN, SessionPhase.CHECKERED)
            and session_phase in (SessionPhase.FORMATION, SessionPhase.COUNTDOWN, SessionPhase.GRIDWALK)
        )
        if went_backwards:
            self._reset_state(
                f"restart de sesión: fase {SessionPhase(self._last_session_phase).name} → "
                f"{SessionPhase(session_phase).name}"
            )

        self._last_session_phase = session_phase

    def update(self, shared: R3EShared) -> None:
        """Procesa un snapshot de la shared memory."""
        now = time.time()
        self.state.last_update = now

        # Siempre actualizar fase y finish_status (incluso en menús/resultados)
        self._last_shared = shared
        self._update_finish(shared)

        # Capturar última vuelta bajo bandera a cuadros: el juego pone
        # game_in_menus=1 en la pantalla de resultados pero completed_laps
        # ya refleja la vuelta final. Sin esto, esa vuelta se pierde.
        if (shared.game_in_menus == 1 or shared.game_paused == 1):
            if shared.completed_laps > self._last_completed_laps >= 0:
                self._update_tires(shared)
                self._update_fuel(shared)
                self._check_lap_completed(shared)
            return

        self._detect_session_change(shared)
        self._update_session(shared)
        self._update_tires(shared)
        self._update_fuel(shared)
        self._update_damage(shared)
        self._update_rivals(shared)
        self._collect_intra_lap_samples(shared)
        self._check_lap_completed(shared)
        self._detect_events(shared)

    def _update_finish(self, s: R3EShared) -> None:
        """Actualiza fase de sesión y estado de finalización del jugador.

        Se ejecuta SIEMPRE, incluso en pantalla de resultados, para capturar
        el momento en que la carrera acaba.
        """
        phase = _safe_enum(SessionPhase, s.session_phase, SessionPhase.UNAVAILABLE)
        finish = _safe_enum(FinishStatus, s.finish_status, FinishStatus.UNAVAILABLE)

        if self.state.session is not None:
            self.state.session.session_phase = phase

        self.state.player_finish_status = finish

        # Capturar posición final cuando se recibe CHECKERED/FINISHED
        if finish == FinishStatus.FINISHED and self.state.finish_position == 0:
            self.state.finish_position = s.position
            logger.info("🏁 RACE FINISHED — P%d (%s)", s.position, finish.name)

        # Detectar rivales que cruzan meta
        for i in range(min(s.num_cars, 128)):
            drv = s.all_drivers_data_1[i]
            if drv.place == s.position or drv.place <= 0:
                continue
            slot = drv.driver_info.slot_id
            drv_finish = _safe_enum(FinishStatus, drv.finish_status, FinishStatus.UNAVAILABLE)
            if drv_finish == FinishStatus.FINISHED and slot not in self._evt_rivals_finished:
                self._evt_rivals_finished.add(slot)
                name = drv.driver_info.get_name()
                logger.info("🏁 %s crosses the line — P%d", name, drv.place)

    def _update_session(self, s: R3EShared) -> None:
        # session_length_format: 0 = por tiempo, 1 = por vueltas
        time_based = s.session_length_format == 0
        laps_total = s.number_of_laps  # -1 si es por tiempo

        # Estimar vueltas totales en sesiones por tiempo
        laps_estimate = laps_total
        if laps_total <= 0 and time_based:
            avg_lap = self._avg_lap_time()
            if avg_lap > 0:
                laps_done = s.completed_laps
                laps_remaining = int(s.session_time_remaining / avg_lap) + 1
                laps_estimate = laps_done + laps_remaining
            elif s.session_time_duration > 0:
                # Sin historial, estimacion basada en longitud del circuito
                # Asumimos ~1.8 min/km como aproximacion muy burda
                est_lap_s = (s.layout_length / 1000.0) * 108
                if est_lap_s > 0:
                    laps_estimate = int(s.session_time_duration / est_lap_s)

        # Buscar la vuelta del lider (P1) en el array de drivers
        player_lap = s.completed_laps + 1
        leader_lap = player_lap  # fallback: asumir misma que el jugador
        for i in range(min(s.num_cars, 128)):
            drv = s.all_drivers_data_1[i]
            if drv.place == 1:
                leader_lap = drv.completed_laps + 1
                break

        # Vueltas restantes basadas en el lider (no en el jugador)
        effective_total = laps_estimate if laps_estimate > 0 else laps_total
        if effective_total > 0:
            laps_remaining = max(0, effective_total - leader_lap + 1)
        elif time_based and s.session_time_remaining > 0:
            avg_lap = self._avg_lap_time()
            laps_remaining = int(s.session_time_remaining / avg_lap) + 1 if avg_lap > 0 else -1
        else:
            laps_remaining = -1

        self.state.session = SessionState(
            session_type=_safe_enum(SessionType, s.session_type, SessionType.UNAVAILABLE),
            session_phase=_safe_enum(SessionPhase, s.session_phase, SessionPhase.UNAVAILABLE),
            track_name=s.get_track_name(),
            layout_name=s.get_layout_name(),
            layout_length_m=s.layout_length,
            laps_total=laps_total,
            laps_total_estimate=laps_estimate,
            time_based=time_based,
            session_duration_s=s.session_time_duration,
            time_remaining_s=s.session_time_remaining,
            player_lap=player_lap,
            leader_lap=leader_lap,
            laps_remaining=laps_remaining,
            player_position=s.position,
            num_cars=s.num_cars,
            pit_window_open=s.pit_window_status == 2,
            pit_window_start=s.pit_window_start,
            pit_window_end=s.pit_window_end,
        )

        # Ajustar cap de snapshots de rivales: 2 vueltas completas.
        # Prioridad: avg_lap real > estimación por longitud > mínimo fijo.
        # Factor 30 s/km ≈ GT3/GTE conservador (Nordschleife ~624 s ≈ real 510 s).
        avg_lap = self._avg_lap_time()
        if avg_lap > 0:
            est_lap_s = avg_lap
        elif s.layout_length > 0:
            est_lap_s = (s.layout_length / 1000.0) * 30
        else:
            est_lap_s = 60.0
        two_laps = int(est_lap_s * 2 * self._POLL_HZ)
        self._MAX_RIVAL_SNAPSHOTS = max(
            self._MIN_RIVAL_SNAPSHOTS,
            min(two_laps, self._CAP_RIVAL_SNAPSHOTS),
        )

    def _avg_lap_time(self) -> float:
        """Media de las ultimas 5 vueltas validas."""
        recent = [l.lap_time for l in self.state.lap_history[-5:] if l.lap_time > 0]
        return sum(recent) / len(recent) if recent else 0.0

    def _update_tires(self, s: R3EShared) -> None:
        self.state.current_tires = TireSetState(
            front_left=_tire_state(s, 0),
            front_right=_tire_state(s, 1),
            rear_left=_tire_state(s, 2),
            rear_right=_tire_state(s, 3),
            compound_front=_safe_enum(TireSubtype, s.tire_subtype_front, TireSubtype.UNAVAILABLE),
            compound_rear=_safe_enum(TireSubtype, s.tire_subtype_rear, TireSubtype.UNAVAILABLE),
        )

    def _update_fuel(self, s: R3EShared) -> None:
        fpl = s.fuel_per_lap if s.fuel_per_lap > 0 else 0.0
        laps_on_fuel = s.fuel_left / fpl if fpl > 0 else float("inf")

        laps_remaining_session = -1
        if s.number_of_laps > 0:
            laps_remaining_session = s.number_of_laps - s.completed_laps
        elif s.session_time_remaining > 0:
            avg_lap = self._avg_lap_time()
            if avg_lap > 0:
                laps_remaining_session = int(s.session_time_remaining / avg_lap) + 1

        fuel_needed = fpl * laps_remaining_session if laps_remaining_session > 0 and fpl > 0 else 0
        deficit = fuel_needed - s.fuel_left

        self.state.current_fuel = FuelState(
            fuel_left_liters=s.fuel_left,
            fuel_capacity_liters=s.fuel_capacity,
            fuel_per_lap=fpl,
            laps_remaining_fuel=laps_on_fuel,
            laps_remaining_session=laps_remaining_session,
            deficit=deficit,
        )

    def _update_damage(self, s: R3EShared) -> None:
        d = s.car_damage
        self.state.damage = DamageState(
            engine=d.engine if d.engine >= 0 else 0.0,
            transmission=d.transmission if d.transmission >= 0 else 0.0,
            aerodynamics=d.aerodynamics if d.aerodynamics >= 0 else 0.0,
            suspension=d.suspension if d.suspension >= 0 else 0.0,
        )

    # Snapshots por rival: 2 vueltas completas como mínimo.
    # Se recalcula dinámicamente con la longitud del circuito.
    _POLL_HZ = 10
    _MIN_RIVAL_SNAPSHOTS = 600          # suelo: 60s (circuitos cortos)
    _CAP_RIVAL_SNAPSHOTS = 12_000       # techo: 20 min (Nordschleife con margen)
    _MAX_RIVAL_SNAPSHOTS = _MIN_RIVAL_SNAPSHOTS  # se actualiza en _update_session

    def _update_rivals(self, s: R3EShared) -> None:
        if s.num_cars <= 0:
            return

        player_pos = s.position
        player_progress = s.completed_laps + s.lap_distance_fraction

        # ── Paso 1: recoger time_delta_front de TODOS los coches por posición ──
        # delta_front_by_pos[P] = gap en segundos desde P hasta P-1
        delta_front_by_pos: dict[int, float] = {}
        drivers: list[tuple[int, object]] = []  # (index, drv)

        for i in range(min(s.num_cars, 128)):
            drv = s.all_drivers_data_1[i]
            pos = drv.place
            if drv.time_delta_front > 0:
                delta_front_by_pos[pos] = drv.time_delta_front
            if pos != player_pos:
                drivers.append((i, drv))

        # ── Paso 2: calcular gap acumulado para cada rival ──
        gaps = self._compute_chained_gaps(
            player_pos, player_progress, delta_front_by_pos, drivers, s,
        )

        # ── Paso 3: construir RivalState para cada rival ──
        for _idx, drv in drivers:
            slot = drv.driver_info.slot_id
            info = RivalInfo(
                name=drv.driver_info.get_name(),
                car_number=drv.driver_info.car_number,
                class_id=drv.driver_info.class_id,
                model_id=drv.driver_info.model_id,
                slot_id=slot,
            )

            cur_sectors = drv.sector_time_current_self
            prev_sectors = drv.sector_time_previous_self
            best_sectors = drv.sector_time_best_self
            gap = gaps.get(slot, 0.0)

            rival_state = RivalState(
                info=info,
                position=drv.place,
                position_class=drv.place_class,
                completed_laps=drv.completed_laps,
                current_lap=drv.completed_laps + 1,
                lap_distance_fraction=drv.lap_distance_fraction,
                finish_status=_safe_enum(FinishStatus, drv.finish_status, FinishStatus.UNAVAILABLE),
                gap_to_player=gap,
                last_lap_time=sum(prev_sectors[j] for j in range(3)) if all(prev_sectors[j] > 0 for j in range(3)) else -1.0,
                current_lap_time=drv.lap_time_current_self if drv.lap_time_current_self > 0 else -1.0,
                current_lap_valid=drv.current_lap_valid == 1,
                current_sectors=_sector_times(cur_sectors[0], cur_sectors[1], cur_sectors[2]),
                last_sectors=_sector_times(prev_sectors[0], prev_sectors[1], prev_sectors[2]),
                best_sectors=_sector_times(best_sectors[0], best_sectors[1], best_sectors[2]),
                car_speed=drv.car_speed,
                in_pitlane=drv.in_pitlane == 1,
                pitstop_status=drv.pitstop_status,
                num_pitstops=drv.num_pitstops,
                tire_front=_safe_enum(TireSubtype, drv.tire_subtype_front, TireSubtype.UNAVAILABLE),
                tire_rear=_safe_enum(TireSubtype, drv.tire_subtype_rear, TireSubtype.UNAVAILABLE),
                drs_active=drv.drs_state == 1,
                ptp_active=drv.ptp_state == 1,
            )

            if slot not in self.state.rivals:
                self.state.rivals[slot] = RivalHistory(info=info)
            history = self.state.rivals[slot]

            # Detectar nueva vuelta completada por el rival
            prev_completed = history.laps[-1].completed_laps if history.laps else -1
            if rival_state.completed_laps > prev_completed and rival_state.last_lap_time > 0:
                history.record_lap(rival_state)

            history.laps.append(rival_state)
            if len(history.laps) > self._MAX_RIVAL_SNAPSHOTS:
                del history.laps[:-self._MAX_RIVAL_SNAPSHOTS]

    def _compute_chained_gaps(
        self,
        player_pos: int,
        player_progress: float,
        delta_front_by_pos: dict[int, float],
        drivers: list[tuple[int, object]],
        s: R3EShared,
    ) -> dict[int, float]:
        """Calcula gap en segundos de cada rival al jugador.

        Método primario: encadenar time_delta_front entre posiciones adyacentes.
        Rival en P3 con jugador en P10 → sumar delta_front[4]+...+delta_front[10].
        Si algún eslabón de la cadena es inválido, fallback a estimación por
        distancia recorrida × tiempo medio de vuelta.

        Retorna dict[slot_id → gap_seconds], positivo = delante, negativo = detrás.
        """
        avg_lap = self._avg_lap_time() or 90.0
        gaps: dict[int, float] = {}

        for _idx, drv in drivers:
            slot = drv.driver_info.slot_id
            rival_pos = drv.place
            rival_progress = drv.completed_laps + drv.lap_distance_fraction

            if rival_pos < player_pos:
                # Rival delante: sumar delta_front desde rival_pos+1 hasta player_pos
                chain_sum = 0.0
                chain_ok = True
                for p in range(rival_pos + 1, player_pos + 1):
                    if p in delta_front_by_pos:
                        chain_sum += delta_front_by_pos[p]
                    else:
                        chain_ok = False
                        break
                if chain_ok and chain_sum > 0:
                    gaps[slot] = chain_sum
                else:
                    gaps[slot] = (rival_progress - player_progress) * avg_lap

            elif rival_pos > player_pos:
                # Rival detrás: sumar delta_front desde player_pos+1 hasta rival_pos
                chain_sum = 0.0
                chain_ok = True
                for p in range(player_pos + 1, rival_pos + 1):
                    if p in delta_front_by_pos:
                        chain_sum += delta_front_by_pos[p]
                    else:
                        chain_ok = False
                        break
                if chain_ok and chain_sum > 0:
                    gaps[slot] = -chain_sum
                else:
                    gaps[slot] = (rival_progress - player_progress) * avg_lap

        return gaps

    def _collect_intra_lap_samples(self, s: R3EShared) -> None:
        if s.car_speed > 0:
            self._speed_samples.append(s.car_speed)
        if s.fuel_left > 0:
            self._fuel_samples.append(s.fuel_left)

    def _record_lap_zero(self, s: R3EShared) -> None:
        """Registra la vuelta 0: posición de salida, neumáticos y fuel iniciales."""
        logger.info("🏎️ Grid position: P%d", s.position)
        self._evt_grid_position = s.position
        empty_sectors = SectorTimes(s1=0.0, s2=0.0, s3=0.0)
        lap = LapData(
            lap_number=0,
            lap_time=0.0,
            sectors=empty_sectors,
            sectors_best=empty_sectors,
            tires=self.state.current_tires,  # type: ignore[arg-type]
            fuel=self.state.current_fuel,    # type: ignore[arg-type]
            position=s.position,
            position_class=s.position_class,
            gap_front=0.0,
            gap_behind=0.0,
            delta_best_self=0.0,
            delta_leader=0.0,
            delta_leader_class=0.0,
            car_speed_avg=0.0,
            num_pitstops=0,
            valid=True,
            timestamp=time.time(),
        )
        self.state.lap_history.append(lap)

    def _check_lap_completed(self, s: R3EShared) -> None:
        current = s.completed_laps

        # Vuelta 0: posición de salida (solo al inicio de sesión)
        if self._last_completed_laps == -1:
            if current == 0 and self.state.current_tires is not None:
                self._record_lap_zero(s)
            self._last_completed_laps = current
            if current < 1:
                return

        if current <= self._last_completed_laps:
            return

        logger.info("Lap %d completed", current)
        self._last_completed_laps = current

        prev_sectors = s.sector_time_previous_self
        best_sectors = s.sector_time_best_self
        avg_speed = sum(self._speed_samples) / len(self._speed_samples) if self._speed_samples else 0.0

        lap = LapData(
            lap_number=current,
            lap_time=s.lap_time_previous_self,
            sectors=_sector_times(prev_sectors[0], prev_sectors[1], prev_sectors[2]),
            sectors_best=_sector_times(best_sectors[0], best_sectors[1], best_sectors[2]),
            tires=self.state.current_tires,  # type: ignore[arg-type]
            fuel=self.state.current_fuel,  # type: ignore[arg-type]
            position=s.position,
            position_class=s.position_class,
            gap_front=s.time_delta_front,
            gap_behind=s.time_delta_behind,
            delta_best_self=s.time_delta_best_self,
            delta_leader=s.lap_time_delta_leader,
            delta_leader_class=s.lap_time_delta_leader_class,
            car_speed_avg=avg_speed,
            num_pitstops=s.num_pitstops,
            valid=s.current_lap_valid == 1,
            timestamp=time.time(),
        )

        self.state.lap_history.append(lap)
        if len(self.state.lap_history) > self._lap_history_size:
            self.state.lap_history.pop(0)

        self._speed_samples.clear()
        self._fuel_samples.clear()

    # ── Detección de eventos en tiempo real ──────────────────────────────

    _PLAYER_SLOT = -1  # sentinel para el jugador en _evt_all_positions

    def _detect_events(self, s: R3EShared) -> None:
        """Detecta y loguea eventos relevantes de carrera."""
        self._evt_check_positions(s)
        self._evt_check_pit(s)
        self._evt_check_damage(s)
        self._evt_check_best_lap(s)
        self._evt_check_race_best_lap(s)
        self._evt_check_rival_pits(s)

    def _evt_check_positions(self, s: R3EShared) -> None:
        """Cambios de posición de todos los pilotos.

        - Rival sube:  ↑ Name P8 → P7 (overtaken_driver)
        - Player sube: 🟢 P8 → P7 - Gains position on Name - +N positions
        - Player baja: 🔴 P3 → P4 - Loses position to Name - +N positions
        - Rival baja:  no se loguea (redundante con el que sube)

        El contador ±N se calcula respecto a la posición de salida.
        """
        current: dict[int, tuple[int, str]] = {}
        if s.position > 0:
            current[self._PLAYER_SLOT] = (s.position, "Player")
        for i in range(min(s.num_cars, 128)):
            drv = s.all_drivers_data_1[i]
            if drv.place <= 0 or drv.place == s.position:
                continue
            current[drv.driver_info.slot_id] = (drv.place, drv.driver_info.get_name())

        if not self._evt_all_positions:
            self._evt_all_positions = current
            return

        prev_pos_to_name = {pos: name for _, (pos, name) in self._evt_all_positions.items()}
        cur_pos_to_name = {pos: name for _, (pos, name) in current.items()}

        for slot, (pos, name) in current.items():
            prev = self._evt_all_positions.get(slot)
            if prev is None or pos == prev[0]:
                continue
            prev_pos = prev[0]
            gained = prev_pos > pos

            if gained:
                overtaken = prev_pos_to_name.get(pos, "?")
                if slot == self._PLAYER_SLOT:
                    vs_grid = self._grid_delta_str(pos)
                    logger.info(
                        "🟢 P%d → P%d - Gains position on %s - %s",
                        prev_pos, pos, overtaken, vs_grid,
                    )
                else:
                    logger.info("   ↑ %s P%d → P%d (%s)", name, prev_pos, pos, overtaken)
            elif slot == self._PLAYER_SLOT:
                overtaker = cur_pos_to_name.get(prev_pos, "?")
                vs_grid = self._grid_delta_str(pos)
                logger.info(
                    "🔴 P%d → P%d - Loses position to %s - %s",
                    prev_pos, pos, overtaker, vs_grid,
                )

        self._evt_all_positions = current

    def _grid_delta_str(self, current_pos: int) -> str:
        """Devuelve cadena ±N positions respecto a la salida."""
        if self._evt_grid_position <= 0:
            return ""
        delta = self._evt_grid_position - current_pos  # positivo = ganadas
        if delta > 0:
            return f"+{delta} position{'s' if delta > 1 else ''}"
        elif delta < 0:
            return f"{delta} position{'s' if abs(delta) > 1 else ''}"
        return "even"

    def _evt_check_pit(self, s: R3EShared) -> None:
        """Entrada/salida de boxes del jugador."""
        in_pit = s.pit_limiter == 1
        if in_pit and not self._evt_in_pitlane:
            logger.info("🔧 PIT IN — player enters pit (P%d)", s.position)
        elif not in_pit and self._evt_in_pitlane:
            logger.info("🔧 PIT OUT — player exits pit (P%d)", s.position)
        self._evt_in_pitlane = in_pit

    def _evt_check_damage(self, s: R3EShared) -> None:
        """Detección de impacto/daño significativo."""
        d = s.car_damage
        damage_sum = max(0, d.engine) + max(0, d.transmission) + max(0, d.aerodynamics) + max(0, d.suspension)
        if self._evt_damage_sum > 0 or damage_sum > 0:
            increase = damage_sum - self._evt_damage_sum
            if increase > 0.05:  # umbral para evitar ruido de micro-daños
                parts = []
                if d.aerodynamics > 0.1:
                    parts.append(f"aero {d.aerodynamics:.0%}")
                if d.suspension > 0.1:
                    parts.append(f"susp {d.suspension:.0%}")
                if d.engine > 0.1:
                    parts.append(f"motor {d.engine:.0%}")
                if d.transmission > 0.1:
                    parts.append(f"trans {d.transmission:.0%}")
                detail = ", ".join(parts) if parts else f"total +{increase:.0%}"
                logger.info("💥 IMPACT — damage: %s", detail)
        self._evt_damage_sum = damage_sum

    def _evt_check_best_lap(self, s: R3EShared) -> None:
        """Nuevo mejor tiempo personal del jugador."""
        best = s.lap_time_best_self
        if best <= 0:
            return
        if self._evt_best_lap <= 0:
            self._evt_best_lap = best
            return
        if best < self._evt_best_lap:
            improvement = self._evt_best_lap - best
            logger.info(
                "⚡ PERSONAL BEST — %s (-%s)",
                self._format_time(best), self._format_time(improvement),
            )
            self._evt_best_lap = best

    def _evt_check_race_best_lap(self, s: R3EShared) -> None:
        """Vuelta rápida de la carrera (cualquier piloto)."""
        for i in range(min(s.num_cars, 128)):
            drv = s.all_drivers_data_1[i]
            prev = sum(drv.sector_time_previous_self[j] for j in range(3))
            if prev <= 0 or any(drv.sector_time_previous_self[j] <= 0 for j in range(3)):
                continue
            if self._evt_race_best_lap <= 0 or prev < self._evt_race_best_lap:
                name = drv.driver_info.get_name() if drv.place != s.position else "Player"
                if abs(prev - self._evt_race_best_lap) > 0.001 or name != self._evt_race_best_driver:
                    if self._evt_race_best_lap > 0:
                        logger.info("🏆 FASTEST LAP — %s: %s", name, self._format_time(prev))
                    self._evt_race_best_lap = prev
                    self._evt_race_best_driver = name

    def _evt_check_rival_pits(self, s: R3EShared) -> None:
        """Entradas/salidas de boxes de rivales."""
        current_in_pit: set[int] = set()
        for i in range(min(s.num_cars, 128)):
            drv = s.all_drivers_data_1[i]
            if drv.place == s.position:
                continue
            slot = drv.driver_info.slot_id
            if drv.in_pitlane == 1:
                current_in_pit.add(slot)
                if slot not in self._evt_rivals_in_pit:
                    name = drv.driver_info.get_name()
                    logger.info("🔧 PIT IN — %s (P%d)", name, drv.place)

        for slot in self._evt_rivals_in_pit - current_in_pit:
            for i in range(min(s.num_cars, 128)):
                drv = s.all_drivers_data_1[i]
                if drv.driver_info.slot_id == slot:
                    logger.info("🔧 PIT OUT — %s (P%d)", drv.driver_info.get_name(), drv.place)
                    break

        self._evt_rivals_in_pit = current_in_pit

    @staticmethod
    def _format_time(seconds: float) -> str:
        """Formatea segundos como M:SS.mmm o SS.mmm."""
        if seconds >= 60:
            mins = int(seconds // 60)
            secs = seconds - mins * 60
            return f"{mins}:{secs:06.3f}"
        return f"{seconds:.3f}s"
