"""Tracker de vueltas y rivales.

Transforma los snapshots crudos de R3EShared en modelos de alto nivel
(LapData, RivalState, etc.) y mantiene el histórico en RaceState.
"""

from __future__ import annotations

import logging
import time

from ..models import (
    DamageState,
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

    def update(self, shared: R3EShared) -> None:
        """Procesa un snapshot de la shared memory."""
        now = time.time()
        self.state.last_update = now

        if shared.game_paused == 1 or shared.game_in_menus == 1:
            return

        self._update_session(shared)
        self._update_tires(shared)
        self._update_fuel(shared)
        self._update_damage(shared)
        self._update_rivals(shared)
        self._collect_intra_lap_samples(shared)
        self._check_lap_completed(shared)

    def _update_session(self, s: R3EShared) -> None:
        self.state.session = SessionState(
            session_type=SessionType(s.session_type) if s.session_type >= -1 else SessionType.UNAVAILABLE,
            session_phase=SessionPhase(s.session_phase) if s.session_phase >= -1 else SessionPhase.UNAVAILABLE,
            track_name=s.get_track_name(),
            layout_name=s.get_layout_name(),
            layout_length_m=s.layout_length,
            laps_total=s.number_of_laps,
            time_remaining_s=s.session_time_remaining,
            current_lap=s.completed_laps + 1,
            num_cars=s.num_cars,
            pit_window_open=s.pit_window_status == 2,
            pit_window_start=s.pit_window_start,
            pit_window_end=s.pit_window_end,
        )

    def _update_tires(self, s: R3EShared) -> None:
        self.state.current_tires = TireSetState(
            front_left=_tire_state(s, 0),
            front_right=_tire_state(s, 1),
            rear_left=_tire_state(s, 2),
            rear_right=_tire_state(s, 3),
            compound_front=TireSubtype(s.tire_subtype_front) if s.tire_subtype_front >= -1 else TireSubtype.UNAVAILABLE,
            compound_rear=TireSubtype(s.tire_subtype_rear) if s.tire_subtype_rear >= -1 else TireSubtype.UNAVAILABLE,
        )

    def _update_fuel(self, s: R3EShared) -> None:
        fpl = s.fuel_per_lap if s.fuel_per_lap > 0 else 0.0
        laps_on_fuel = s.fuel_left / fpl if fpl > 0 else float("inf")

        laps_remaining_session = -1
        if s.number_of_laps > 0:
            laps_remaining_session = s.number_of_laps - s.completed_laps
        elif s.session_time_remaining > 0 and len(self.state.lap_history) > 0:
            avg_lap = sum(l.lap_time for l in self.state.lap_history[-5:] if l.lap_time > 0) / max(
                1, sum(1 for l in self.state.lap_history[-5:] if l.lap_time > 0)
            )
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

    def _update_rivals(self, s: R3EShared) -> None:
        if s.num_cars <= 0:
            return

        player_pos = s.position
        for i in range(min(s.num_cars, 128)):
            drv = s.all_drivers_data_1[i]
            slot = drv.driver_info.slot_id

            delta_front = drv.time_delta_front
            delta_behind = drv.time_delta_behind
            pos_diff = drv.place - player_pos

            if abs(pos_diff) > self._rivals_count and pos_diff != 0:
                continue

            if drv.place == player_pos:
                continue

            info = RivalInfo(
                name=drv.driver_info.get_name(),
                car_number=drv.driver_info.car_number,
                class_id=drv.driver_info.class_id,
                model_id=drv.driver_info.model_id,
                slot_id=slot,
            )

            prev_sectors = drv.sector_time_previous_self
            best_sectors = drv.sector_time_best_self

            gap = delta_front if pos_diff < 0 else -delta_behind
            rival_state = RivalState(
                info=info,
                position=drv.place,
                position_class=drv.place_class,
                completed_laps=drv.completed_laps,
                gap_to_player=gap,
                last_lap_time=sum(prev_sectors[j] for j in range(3)) if all(prev_sectors[j] > 0 for j in range(3)) else -1.0,
                last_sectors=_sector_times(prev_sectors[0], prev_sectors[1], prev_sectors[2]),
                best_sectors=_sector_times(best_sectors[0], best_sectors[1], best_sectors[2]),
                car_speed=drv.car_speed,
                in_pitlane=drv.in_pitlane == 1,
                num_pitstops=drv.num_pitstops,
                tire_front=TireSubtype(drv.tire_subtype_front) if drv.tire_subtype_front >= -1 else TireSubtype.UNAVAILABLE,
                tire_rear=TireSubtype(drv.tire_subtype_rear) if drv.tire_subtype_rear >= -1 else TireSubtype.UNAVAILABLE,
                drs_active=drv.drs_state == 1,
                ptp_active=drv.ptp_state == 1,
            )

            if slot not in self.state.rivals:
                self.state.rivals[slot] = RivalHistory(info=info)
            self.state.rivals[slot].laps.append(rival_state)

    def _collect_intra_lap_samples(self, s: R3EShared) -> None:
        if s.car_speed > 0:
            self._speed_samples.append(s.car_speed)
        if s.fuel_left > 0:
            self._fuel_samples.append(s.fuel_left)

    def _check_lap_completed(self, s: R3EShared) -> None:
        current = s.completed_laps
        if current <= self._last_completed_laps or current < 1:
            self._last_completed_laps = current
            return

        logger.info("Vuelta %d completada", current)
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
            valid=s.current_lap_valid == 1,
            timestamp=time.time(),
        )

        self.state.lap_history.append(lap)
        if len(self.state.lap_history) > self._lap_history_size:
            self.state.lap_history.pop(0)

        self._speed_samples.clear()
        self._fuel_samples.clear()
