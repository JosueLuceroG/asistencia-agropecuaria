"""Bloqueo de instancia único para evitar escrituras simultáneas en los JSON."""
from __future__ import annotations

import os


class InstanceLockedError(RuntimeError):
    """Se levantó cuando otra instancia ya conserva el bloqueo de datos."""


class InstanceLock:
    def __init__(self, directory, filename=".asistencia_agropecuaria.lock"):
        self.path = os.path.join(directory, filename)
        self._handle = None

    def acquire(self):
        if self._handle is not None:
            return self
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                if os.path.getsize(self.path) == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            handle.close()
            raise InstanceLockedError("Ya hay una sesión de Asistencia Agropecuaria abierta en este servidor.") from exc
        self._handle = handle
        return self

    def release(self):
        if self._handle is None:
            return
        try:
            self._handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_args):
        self.release()