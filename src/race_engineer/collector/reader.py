"""Lector de la shared memory $R3E de RaceRoom.

Abre el memory-mapped file de Windows y devuelve snapshots de R3EShared.
Solo funciona en Windows (mmap sobre named shared memory).
"""

from __future__ import annotations

import ctypes
import logging
import sys
import time
from typing import TYPE_CHECKING

from .r3e_types import R3E_SHARED_MEMORY_NAME, R3EShared

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

if sys.platform == "win32":
    import mmap

    _FILE_MAP_READ = 0x0004

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    kernel32.OpenFileMappingW.restype = ctypes.c_void_p
    kernel32.OpenFileMappingW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.MapViewOfFile.restype = ctypes.c_void_p
    kernel32.MapViewOfFile.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_size_t,
    ]
    kernel32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]


class R3EReader:
    """Lee datos de la shared memory $R3E de RaceRoom.

    Uso:
        reader = R3EReader()
        if reader.connect():
            data = reader.read()
            print(data.car_speed, data.gear)
        reader.close()
    """

    def __init__(self) -> None:
        self._handle: int | None = None
        self._view: int | None = None
        self._size = ctypes.sizeof(R3EShared)
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self) -> bool:
        """Intenta abrir la shared memory. Devuelve True si éxito."""
        if sys.platform != "win32":
            logger.error("La shared memory de R3E solo funciona en Windows")
            return False

        try:
            handle = kernel32.OpenFileMappingW(
                _FILE_MAP_READ,
                False,
                R3E_SHARED_MEMORY_NAME,
            )
            if not handle:
                logger.debug("RaceRoom no detectado (shared memory no existe)")
                return False

            view = kernel32.MapViewOfFile(handle, _FILE_MAP_READ, 0, 0, self._size)
            if not view:
                kernel32.CloseHandle(handle)
                logger.error("No se pudo mapear la shared memory")
                return False

            self._handle = handle
            self._view = view
            self._connected = True
            logger.info("Conectado a shared memory $R3E (%d bytes)", self._size)
            return True

        except Exception:
            logger.exception("Error conectando a shared memory")
            return False

    def read(self) -> R3EShared | None:
        """Lee un snapshot completo de la shared memory."""
        if not self._connected or self._view is None:
            return None

        try:
            return R3EShared.from_address(self._view)
        except Exception:
            logger.exception("Error leyendo shared memory")
            return None

    def close(self) -> None:
        """Libera el mapeo de memoria."""
        if self._view is not None:
            kernel32.UnmapViewOfFile(self._view)
            self._view = None
        if self._handle is not None:
            kernel32.CloseHandle(self._handle)
            self._handle = None
        self._connected = False
        logger.info("Desconectado de shared memory")

    def wait_for_connection(self, timeout_s: float = 600, poll_s: float = 2.0) -> bool:
        """Espera hasta que RaceRoom esté corriendo y la shared memory disponible."""
        logger.info("Esperando a RaceRoom (timeout %ds)...", timeout_s)
        start = time.time()
        while time.time() - start < timeout_s:
            if self.connect():
                return True
            time.sleep(poll_s)
        logger.warning("Timeout esperando a RaceRoom")
        return False
