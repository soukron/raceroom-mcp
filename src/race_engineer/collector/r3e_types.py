"""Definiciones ctypes del shared memory de RaceRoom ($R3E).

Traducción directa de r3e.h (API v2.16) a ctypes para lectura desde Python.
Solo se incluyen los campos que usa el race engineer; el struct completo
mantiene el layout correcto para que los offsets cuadren.

Referencia: https://github.com/sector3studios/r3e-api/blob/master/sample-c/src/r3e.h
"""

from __future__ import annotations

import ctypes

R3E_SHARED_MEMORY_NAME = "$R3E"
R3E_NUM_DRIVERS_MAX = 128
R3E_TIRE_INDEX_MAX = 4
R3E_TIRE_TEMP_INDEX_MAX = 3
R3E_PIT_MENU_MAX = 11


# ── Tipos base ───────────────────────────────────────────────────────

class Vec3F32(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("x", ctypes.c_float), ("y", ctypes.c_float), ("z", ctypes.c_float)]


class Vec3F64(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("z", ctypes.c_double)]


class OriF32(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("pitch", ctypes.c_float), ("yaw", ctypes.c_float), ("roll", ctypes.c_float)]


class SectorStarts(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("sector1", ctypes.c_float), ("sector2", ctypes.c_float), ("sector3", ctypes.c_float)]


# ── Player data (alta precisión) ─────────────────────────────────────

class PlayerData(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("game_simulation_ticks", ctypes.c_int32),
        ("game_simulation_time", ctypes.c_double),
        ("position", Vec3F64),
        ("velocity", Vec3F64),
        ("local_velocity", Vec3F64),
        ("acceleration", Vec3F64),
        ("local_acceleration", Vec3F64),
        ("orientation", Vec3F64),
        ("rotation", Vec3F64),
        ("angular_acceleration", Vec3F64),
        ("angular_velocity", Vec3F64),
        ("local_angular_velocity", Vec3F64),
        ("local_g_force", Vec3F64),
        ("steering_force", ctypes.c_double),
        ("steering_force_percentage", ctypes.c_double),
        ("engine_torque", ctypes.c_double),
        ("current_downforce", ctypes.c_double),
        ("voltage", ctypes.c_double),
        ("ers_level", ctypes.c_double),
        ("power_mgu_h", ctypes.c_double),
        ("power_mgu_k", ctypes.c_double),
        ("torque_mgu_k", ctypes.c_double),
        ("suspension_deflection", ctypes.c_double * R3E_TIRE_INDEX_MAX),
        ("suspension_velocity", ctypes.c_double * R3E_TIRE_INDEX_MAX),
        ("camber", ctypes.c_double * R3E_TIRE_INDEX_MAX),
        ("ride_height", ctypes.c_double * R3E_TIRE_INDEX_MAX),
        ("front_wing_height", ctypes.c_double),
        ("front_roll_angle", ctypes.c_double),
        ("rear_roll_angle", ctypes.c_double),
        ("third_spring_suspension_deflection_front", ctypes.c_double),
        ("third_spring_suspension_velocity_front", ctypes.c_double),
        ("third_spring_suspension_deflection_rear", ctypes.c_double),
        ("third_spring_suspension_velocity_rear", ctypes.c_double),
        ("unused1", ctypes.c_double),
    ]


# ── Flags ─────────────────────────────────────────────────────────────

class Flags(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("yellow", ctypes.c_int32),
        ("yellow_caused_it", ctypes.c_int32),
        ("yellow_overtake", ctypes.c_int32),
        ("yellow_positions_gained", ctypes.c_int32),
        ("sector_yellow", ctypes.c_int32 * 3),
        ("closest_yellow_distance_into_track", ctypes.c_float),
        ("blue", ctypes.c_int32),
        ("black", ctypes.c_int32),
        ("green", ctypes.c_int32),
        ("checkered", ctypes.c_int32),
        ("white", ctypes.c_int32),
        ("black_and_white", ctypes.c_int32),
    ]


# ── Car damage ────────────────────────────────────────────────────────

class CarDamage(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("engine", ctypes.c_float),
        ("transmission", ctypes.c_float),
        ("aerodynamics", ctypes.c_float),
        ("suspension", ctypes.c_float),
        ("unused1", ctypes.c_float),
        ("unused2", ctypes.c_float),
    ]


# ── Cut track penalties ───────────────────────────────────────────────

class CutTrackPenalties(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("drive_through", ctypes.c_int32),
        ("stop_and_go", ctypes.c_int32),
        ("pit_stop", ctypes.c_int32),
        ("time_deduction", ctypes.c_int32),
        ("slow_down", ctypes.c_int32),
    ]


# ── DRS ───────────────────────────────────────────────────────────────

class DRS(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("equipped", ctypes.c_int32),
        ("available", ctypes.c_int32),
        ("num_activations_left", ctypes.c_int32),
        ("engaged", ctypes.c_int32),
    ]


# ── Push to pass ──────────────────────────────────────────────────────

class PushToPass(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("available", ctypes.c_int32),
        ("engaged", ctypes.c_int32),
        ("amount_left", ctypes.c_int32),
        ("engaged_time_left", ctypes.c_float),
        ("wait_time_left", ctypes.c_float),
    ]


# ── Tire temps ────────────────────────────────────────────────────────

class TireTemp(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("current_temp", ctypes.c_float * R3E_TIRE_TEMP_INDEX_MAX),
        ("optimal_temp", ctypes.c_float),
        ("cold_temp", ctypes.c_float),
        ("hot_temp", ctypes.c_float),
    ]


class BrakeTemp(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("current_temp", ctypes.c_float),
        ("optimal_temp", ctypes.c_float),
        ("cold_temp", ctypes.c_float),
        ("hot_temp", ctypes.c_float),
    ]


# ── Aid settings ──────────────────────────────────────────────────────

class AidSettings(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("abs", ctypes.c_int32),
        ("tc", ctypes.c_int32),
        ("esp", ctypes.c_int32),
        ("countersteer", ctypes.c_int32),
        ("cornering", ctypes.c_int32),
    ]


# ── Driver info (por piloto) ─────────────────────────────────────────

class DriverInfo(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("name", ctypes.c_uint8 * 64),
        ("car_number", ctypes.c_int32),
        ("class_id", ctypes.c_int32),
        ("model_id", ctypes.c_int32),
        ("team_id", ctypes.c_int32),
        ("livery_id", ctypes.c_int32),
        ("manufacturer_id", ctypes.c_int32),
        ("user_id", ctypes.c_int32),
        ("slot_id", ctypes.c_int32),
        ("class_performance_index", ctypes.c_int32),
        ("engine_type", ctypes.c_int32),
        ("car_width", ctypes.c_float),
        ("car_length", ctypes.c_float),
    ]

    def get_name(self) -> str:
        raw = bytes(self.name)
        return raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")


# ── Driver data (por cada coche en pista) ─────────────────────────────

class DriverData(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("driver_info", DriverInfo),
        ("finish_status", ctypes.c_int32),
        ("place", ctypes.c_int32),
        ("place_class", ctypes.c_int32),
        ("lap_distance", ctypes.c_float),
        ("position", Vec3F32),
        ("track_sector", ctypes.c_int32),
        ("completed_laps", ctypes.c_int32),
        ("current_lap_valid", ctypes.c_int32),
        ("lap_time_current_self", ctypes.c_float),
        ("sector_time_current_self", ctypes.c_float * 3),
        ("sector_time_previous_self", ctypes.c_float * 3),
        ("sector_time_best_self", ctypes.c_float * 3),
        ("time_delta_front", ctypes.c_float),
        ("time_delta_behind", ctypes.c_float),
        ("pitstop_status", ctypes.c_int32),
        ("in_pitlane", ctypes.c_int32),
        ("num_pitstops", ctypes.c_int32),
        ("penalties", CutTrackPenalties),
        ("car_speed", ctypes.c_float),
        ("tire_type_front", ctypes.c_int32),
        ("tire_type_rear", ctypes.c_int32),
        ("tire_subtype_front", ctypes.c_int32),
        ("tire_subtype_rear", ctypes.c_int32),
        ("base_penalty_weight", ctypes.c_float),
        ("aid_penalty_weight", ctypes.c_float),
        ("drs_state", ctypes.c_int32),
        ("ptp_state", ctypes.c_int32),
        ("penalty_type", ctypes.c_int32),
        ("penalty_reason", ctypes.c_int32),
        ("engine_state", ctypes.c_int32),
        ("orientation", Vec3F32),
    ]


# ── Struct principal: r3e_shared ──────────────────────────────────────

class R3EShared(ctypes.Structure):
    """Estructura raíz del shared memory $R3E.

    Layout idéntico al r3e_shared de r3e.h v2.16.
    Todos los campos packed sin padding.
    """

    _pack_ = 1
    _fields_ = [
        # Version
        ("version_major", ctypes.c_int32),
        ("version_minor", ctypes.c_int32),
        ("all_drivers_offset", ctypes.c_int32),
        ("driver_data_size", ctypes.c_int32),

        # Game state
        ("game_paused", ctypes.c_int32),
        ("game_in_menus", ctypes.c_int32),
        ("game_in_replay", ctypes.c_int32),
        ("game_using_vr", ctypes.c_int32),
        ("game_unused1", ctypes.c_int32),

        # High detail player data
        ("player", PlayerData),

        # Event and session
        ("track_name", ctypes.c_uint8 * 64),
        ("layout_name", ctypes.c_uint8 * 64),
        ("track_id", ctypes.c_int32),
        ("layout_id", ctypes.c_int32),
        ("layout_length", ctypes.c_float),
        ("sector_start_factors", SectorStarts),
        ("race_session_laps", ctypes.c_int32 * 3),
        ("race_session_minutes", ctypes.c_int32 * 3),
        ("event_index", ctypes.c_int32),
        ("session_type", ctypes.c_int32),
        ("session_iteration", ctypes.c_int32),
        ("session_length_format", ctypes.c_int32),
        ("session_pit_speed_limit", ctypes.c_float),
        ("session_phase", ctypes.c_int32),
        ("start_lights", ctypes.c_int32),
        ("tire_wear_active", ctypes.c_int32),
        ("fuel_use_active", ctypes.c_int32),
        ("number_of_laps", ctypes.c_int32),
        ("session_time_duration", ctypes.c_float),
        ("session_time_remaining", ctypes.c_float),
        ("max_incident_points", ctypes.c_int32),
        ("event_unused2", ctypes.c_float),

        # Pit
        ("pit_window_status", ctypes.c_int32),
        ("pit_window_start", ctypes.c_int32),
        ("pit_window_end", ctypes.c_int32),
        ("in_pitlane", ctypes.c_int32),
        ("pit_menu_selection", ctypes.c_int32),
        ("pit_menu_state", ctypes.c_int32 * R3E_PIT_MENU_MAX),
        ("pit_state", ctypes.c_int32),
        ("pit_total_duration", ctypes.c_float),
        ("pit_elapsed_time", ctypes.c_float),
        ("pit_action", ctypes.c_int32),
        ("num_pitstops", ctypes.c_int32),
        ("pit_min_duration_total", ctypes.c_float),
        ("pit_min_duration_left", ctypes.c_float),

        # Scoring & timings
        ("flags", Flags),
        ("position", ctypes.c_int32),
        ("position_class", ctypes.c_int32),
        ("finish_status", ctypes.c_int32),
        ("cut_track_warnings", ctypes.c_int32),
        ("penalties", CutTrackPenalties),
        ("num_penalties", ctypes.c_int32),
        ("completed_laps", ctypes.c_int32),
        ("current_lap_valid", ctypes.c_int32),
        ("track_sector", ctypes.c_int32),
        ("lap_distance", ctypes.c_float),
        ("lap_distance_fraction", ctypes.c_float),
        ("lap_time_best_leader", ctypes.c_float),
        ("lap_time_best_leader_class", ctypes.c_float),
        ("session_best_lap_sector_times", ctypes.c_float * 3),
        ("lap_time_best_self", ctypes.c_float),
        ("sector_time_best_self", ctypes.c_float * 3),
        ("lap_time_previous_self", ctypes.c_float),
        ("sector_time_previous_self", ctypes.c_float * 3),
        ("lap_time_current_self", ctypes.c_float),
        ("sector_time_current_self", ctypes.c_float * 3),
        ("lap_time_delta_leader", ctypes.c_float),
        ("lap_time_delta_leader_class", ctypes.c_float),
        ("time_delta_front", ctypes.c_float),
        ("time_delta_behind", ctypes.c_float),
        ("time_delta_best_self", ctypes.c_float),
        ("best_individual_sector_time_self", ctypes.c_float * 3),
        ("best_individual_sector_time_leader", ctypes.c_float * 3),
        ("best_individual_sector_time_leader_class", ctypes.c_float * 3),
        ("incident_points", ctypes.c_int32),
        ("lap_valid_state", ctypes.c_int32),
        ("score_unused1", ctypes.c_float),
        ("score_unused2", ctypes.c_float),

        # Vehicle information
        ("vehicle_info", DriverInfo),
        ("player_name", ctypes.c_uint8 * 64),

        # Vehicle state
        ("control_type", ctypes.c_int32),
        ("car_speed", ctypes.c_float),
        ("engine_rps", ctypes.c_float),
        ("max_engine_rps", ctypes.c_float),
        ("upshift_rps", ctypes.c_float),
        ("gear", ctypes.c_int32),
        ("num_gears", ctypes.c_int32),
        ("car_cg_location", Vec3F32),
        ("car_orientation", OriF32),
        ("local_acceleration", Vec3F32),
        ("total_mass", ctypes.c_float),
        ("fuel_left", ctypes.c_float),
        ("fuel_capacity", ctypes.c_float),
        ("fuel_per_lap", ctypes.c_float),
        ("engine_water_temp", ctypes.c_float),
        ("engine_oil_temp", ctypes.c_float),
        ("fuel_pressure", ctypes.c_float),
        ("engine_oil_pressure", ctypes.c_float),
        ("turbo_pressure", ctypes.c_float),
        ("throttle", ctypes.c_float),
        ("throttle_raw", ctypes.c_float),
        ("brake", ctypes.c_float),
        ("brake_raw", ctypes.c_float),
        ("clutch", ctypes.c_float),
        ("clutch_raw", ctypes.c_float),
        ("steer_input_raw", ctypes.c_float),
        ("steer_lock_degrees", ctypes.c_int32),
        ("steer_wheel_range_degrees", ctypes.c_int32),
        ("aid_settings", AidSettings),
        ("drs", DRS),
        ("pit_limiter", ctypes.c_int32),
        ("push_to_pass", PushToPass),
        ("brake_bias", ctypes.c_float),
        ("drs_num_activations_total", ctypes.c_int32),
        ("ptp_num_activations_total", ctypes.c_int32),
        ("battery_soc", ctypes.c_float),
        ("water_left", ctypes.c_float),
        ("abs_setting", ctypes.c_int32),
        ("headlights", ctypes.c_int32),
        ("vehicle_unused1", ctypes.c_float),

        # Tires
        ("tire_type", ctypes.c_int32),
        ("tire_rps", ctypes.c_float * R3E_TIRE_INDEX_MAX),
        ("tire_speed", ctypes.c_float * R3E_TIRE_INDEX_MAX),
        ("tire_grip", ctypes.c_float * R3E_TIRE_INDEX_MAX),
        ("tire_wear", ctypes.c_float * R3E_TIRE_INDEX_MAX),
        ("tire_flatspot", ctypes.c_int32 * R3E_TIRE_INDEX_MAX),
        ("tire_pressure", ctypes.c_float * R3E_TIRE_INDEX_MAX),
        ("tire_dirt", ctypes.c_float * R3E_TIRE_INDEX_MAX),
        ("tire_temp", TireTemp * R3E_TIRE_INDEX_MAX),
        ("tire_type_front", ctypes.c_int32),
        ("tire_type_rear", ctypes.c_int32),
        ("tire_subtype_front", ctypes.c_int32),
        ("tire_subtype_rear", ctypes.c_int32),
        ("brake_temp", BrakeTemp * R3E_TIRE_INDEX_MAX),
        ("brake_pressure", ctypes.c_float * R3E_TIRE_INDEX_MAX),

        # Electronics
        ("traction_control_setting", ctypes.c_int32),
        ("engine_map_setting", ctypes.c_int32),
        ("engine_brake_setting", ctypes.c_int32),
        ("traction_control_percent", ctypes.c_float),
        ("tire_on_mtrl", ctypes.c_int32 * R3E_TIRE_INDEX_MAX),
        ("tire_load", ctypes.c_float * R3E_TIRE_INDEX_MAX),

        # Damage
        ("car_damage", CarDamage),

        # Driver info
        ("num_cars", ctypes.c_int32),
        ("all_drivers_data_1", DriverData * R3E_NUM_DRIVERS_MAX),
    ]

    def get_track_name(self) -> str:
        raw = bytes(self.track_name)
        return raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")

    def get_layout_name(self) -> str:
        raw = bytes(self.layout_name)
        return raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")

    def get_player_name(self) -> str:
        raw = bytes(self.player_name)
        return raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
