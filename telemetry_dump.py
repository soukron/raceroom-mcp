import sys, ctypes, ctypes.wintypes
sys.path.insert(0, "src")
from race_engineer.collector.r3e_types import R3EShared

FILE_MAP_READ = 0x0004
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenFileMappingW.restype = ctypes.wintypes.HANDLE
kernel32.OpenFileMappingW.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.LPCWSTR]
kernel32.MapViewOfFile.restype = ctypes.c_void_p
kernel32.MapViewOfFile.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t]
kernel32.UnmapViewOfFile.restype = ctypes.c_int
kernel32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
kernel32.CloseHandle.restype = ctypes.c_int
kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]

handle = kernel32.OpenFileMappingW(FILE_MAP_READ, False, "$R3E")
if not handle:
    print("$R3E no disponible"); sys.exit(1)
size = ctypes.sizeof(R3EShared)
view = kernel32.MapViewOfFile(handle, FILE_MAP_READ, 0, 0, size)
if not view:
    print("MapViewOfFile failed"); sys.exit(1)
shared = R3EShared.from_address(view)

# session_length_format: 0 = por tiempo, 1 = por vueltas
time_based = shared.session_length_format == 0

if shared.number_of_laps > 0:
    lap_str = f"{shared.completed_laps + 1} / {shared.number_of_laps}"
elif time_based:
    rem_min = shared.session_time_remaining / 60.0
    dur_min = shared.session_time_duration / 60.0
    lap_str = f"{shared.completed_laps + 1} (por tiempo: {rem_min:.1f}min restantes de {dur_min:.0f}min)"
else:
    lap_str = f"{shared.completed_laps + 1} / ?"

print("=" * 60)
print(f"  RACE ENGINEER - Telemetry Dump (API v{shared.version_major}.{shared.version_minor})")
print("=" * 60)
print(f"  Struct size:  {size} bytes")
print(f"  Track:        {shared.get_track_name()}")
print(f"  Layout:       {shared.get_layout_name()}")
print(f"  Player:       {shared.get_player_name()}")
print(f"  Game mode:    {shared.game_mode}")
print(f"  Session:      type={shared.session_type} phase={shared.session_phase}")
print(f"  Format:       {'TIEMPO' if time_based else 'VUELTAS'} (session_length_format={shared.session_length_format})")
print(f"  Lap:          {lap_str}")
print(f"  number_of_laps={shared.number_of_laps}  duration={shared.session_time_duration:.0f}s  remaining={shared.session_time_remaining:.0f}s")
st = shared.session_type
if 0 <= st <= 2:
    labels = ["Practice", "Qualify", "Race"]
    print(f"  race_session_laps: P={shared.race_session_laps[0]} Q={shared.race_session_laps[1]} R={shared.race_session_laps[2]}")
    print(f"  race_session_mins: P={shared.race_session_minutes[0]} Q={shared.race_session_minutes[1]} R={shared.race_session_minutes[2]}")
print(f"  Position:     P{shared.position} of {shared.num_cars}")
print("-" * 60)
print(f"  Speed:        {shared.car_speed * 3.6:.1f} km/h")
print(f"  Gear:         {shared.gear}")
print(f"  RPM:          {shared.engine_rps * 9.5493:.0f} / {shared.max_engine_rps * 9.5493:.0f}")
print(f"  Throttle:     {shared.throttle * 100:.0f}%")
print(f"  Brake:        {shared.brake * 100:.0f}%")
print("-" * 60)
print(f"  Fuel:         {shared.fuel_left:.1f}L / {shared.fuel_capacity:.0f}L")
print(f"  Fuel/lap:     {shared.fuel_per_lap:.2f} L")
print("-" * 60)
print(f"  Grip:   FL={shared.tire_grip[0]:.3f}  FR={shared.tire_grip[1]:.3f}")
print(f"          RL={shared.tire_grip[2]:.3f}  RR={shared.tire_grip[3]:.3f}")
print(f"  Wear:   FL={shared.tire_wear[0]:.3f}  FR={shared.tire_wear[1]:.3f}")
print(f"          RL={shared.tire_wear[2]:.3f}  RR={shared.tire_wear[3]:.3f}")
t = shared.tire_temp
print(f"  Temp C: FL={t[0].current_temp[1]:.0f}  FR={t[1].current_temp[1]:.0f}")
print(f"          RL={t[2].current_temp[1]:.0f}  RR={t[3].current_temp[1]:.0f}")
print(f"  Optimal:  {t[0].optimal_temp:.0f} C")
print("-" * 60)
print(f"  Gap front:    {shared.time_delta_front:.2f}s")
print(f"  Gap behind:   {shared.time_delta_behind:.2f}s")
print(f"  Best lap:     {shared.lap_time_best_self:.3f}s")
print(f"  Prev lap:     {shared.lap_time_previous_self:.3f}s")
print(f"  Delta best:   {shared.time_delta_best_self:+.3f}s")
print("-" * 60)
d = shared.car_damage
print(f"  Damage: eng={d.engine:.2f} trans={d.transmission:.2f} aero={d.aerodynamics:.2f} susp={d.suspension:.2f}")
print(f"  Flags:  Y={shared.flags.yellow} B={shared.flags.blue} G={shared.flags.green}")
print("-" * 60)
if shared.num_cars > 0:
    print(f"  Drivers ({shared.num_cars}):")
    for i in range(min(shared.num_cars, 10)):
        dd = shared.all_drivers_data_1[i]
        n = dd.driver_info.get_name()
        prev = sum(dd.sector_time_previous_self[j] for j in range(3))
        pstr = f"{prev:.3f}s" if all(dd.sector_time_previous_self[j] > 0 for j in range(3)) else "---"
        print(f"    P{dd.place:2d} {n:20s} {dd.car_speed*3.6:6.1f}km/h lap={dd.completed_laps+1:2d} prev={pstr} pits={dd.num_pitstops}")
print("=" * 60)

kernel32.UnmapViewOfFile(ctypes.c_void_p(view))
kernel32.CloseHandle(handle)
