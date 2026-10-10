"""OS-held local process ownership, not a worker lease or proof against a hostile host."""

import os
from contextlib import contextmanager
from pathlib import Path

from agentic_intent_gate.domain.action import ActionCode, ActionError


@contextmanager
def single_owner(gate_path, *, predecessor_stopped=False):
    if predecessor_stopped is not True:
        raise ActionError(ActionCode.NOT_AUTHORIZED)
    path = Path(str(gate_path) + ".owner")
    stream = None
    locked = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = path.open("a+b")
        if path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
        yield
    except OSError:
        raise ActionError(ActionCode.DATABASE_UNAVAILABLE) from None
    finally:
        if stream is not None:
            if locked:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            stream.close()
