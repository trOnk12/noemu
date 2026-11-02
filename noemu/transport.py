"""One request and one reply with a driver over loopback TCP, under a single deadline."""
from __future__ import annotations

import socket
import time

from noemu.protocol import MAX_MESSAGE_BYTES, NoemuError


def exchange(port: int, payload: bytes, timeout: float) -> bytes:
    """Returns the driver's raw reply line. Connection failures say whether the command may have run."""
    deadline = time.monotonic() + timeout
    dispatch_started = False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as connection:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            connection.settimeout(remaining)
            dispatch_started = True  # Even a failed sendall may write part of a request.
            connection.sendall(payload)
            data = bytearray()
            while b"\n" not in data:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                connection.settimeout(remaining)
                chunk = connection.recv(min(65536, MAX_MESSAGE_BYTES + 1 - len(data)))
                if not chunk:
                    raise NoemuError("protocol_error", "driver closed the connection before a complete response")
                data.extend(chunk)
                if len(data) > MAX_MESSAGE_BYTES:
                    raise NoemuError("message_too_large", "driver response exceeds 1 MiB")
            return bytes(data)
    except TimeoutError:
        raise NoemuError("deadline_exceeded", f"no answer in {timeout:g} s; outcome unknown; do not retry a mutation blindly") from None
    except OSError as error:
        if dispatch_started:
            raise NoemuError("connection_lost", f"{error}; outcome unknown; inspect state before retrying") from error
        raise NoemuError("connection_failed", f"{error}; command was not sent") from error
