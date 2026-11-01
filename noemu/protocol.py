"""Version 1 command and result model shared by CLI, MCP and driver transport."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

VERSION = 1
MAX_MESSAGE_BYTES = 1024 * 1024
COMMANDS = {
    "screen": (0, 0), "tap": (1, 1), "type": (2, 2), "scroll": (0, 2),
    "back": (0, 0), "rotate": (0, 1), "restore-activity": (0, 0),
    "kill": (0, 0), "leaks": (0, 0), "wait": (0, 1), "open": (1, 1),
    "network": (0, 2), "shot": (0, 1), "logs": (0, 0), "crawl": (0, 2), "quit": (0, 0),
}


class NoemuError(RuntimeError):
    def __init__(self, code: str, message: str, result: dict | None = None):
        super().__init__(f"noemu: {code}: {message}")
        self.code, self.result = code, result


@dataclass(frozen=True)
class Command:
    name: str
    args: tuple[str, ...] = ()

    def __post_init__(self):
        if self.name not in COMMANDS:
            raise NoemuError("unsupported", f"unknown command {self.name}")
        lo, hi = COMMANDS[self.name]
        if not lo <= len(self.args) <= hi or any(not isinstance(a, str) for a in self.args):
            raise NoemuError("invalid_argument", f"{self.name} requires {lo}..{hi} string arguments")
        a = self.args
        if self.name in ("tap", "type", "open") and not a[0]:
            raise NoemuError("invalid_argument", "target must not be empty")
        if self.name == "scroll":
            if a and a[0] not in ("up", "down", "left", "right"):
                raise NoemuError("invalid_argument", "scroll direction must be up, down, left or right")
            if len(a) == 2 and not a[1]:
                raise NoemuError("invalid_argument", "scroll target must not be empty")
        ranges = {"wait": [(0, 120000)], "rotate": [(1, 100)], "crawl": [(1, 10000), (1, 60)]}
        for value, (minimum, maximum) in zip(a, ranges.get(self.name, [])):
            self.integer(value, minimum, maximum)
        if self.name == "network" and a:
            if a[0] not in ("on", "off", "slow") or len(a) != (2 if a[0] == "slow" else 1):
                raise NoemuError("invalid_argument", "network expects on, off, or slow MS")
            if a[0] == "slow":
                self.integer(a[1], 0, 60000)

    @staticmethod
    def integer(value: str, minimum: int, maximum: int) -> int:
        if not value.isascii() or not value.isdecimal() or not minimum <= int(value) <= maximum:
            raise NoemuError("invalid_argument", f"expected an integer in {minimum}..{maximum}")
        return int(value)

    @classmethod
    def legacy(cls, line: str) -> Command:
        """Compatibility for internal callers; user arguments use Command directly."""
        name, _, rest = line.partition(" ")
        if name == "type":
            return cls(name, tuple(rest.split("\t", 1)))
        if name in ("tap", "open", "shot"):
            return cls(name, (rest,) if rest else ())
        return cls(name, tuple(rest.split()))

    def encode(self, request_id: str, timeout: float) -> bytes:
        if not math.isfinite(timeout) or not 0 < timeout <= 3600:
            raise NoemuError("invalid_argument", "timeout must be positive and at most 3600 seconds")
        wire = {"version": VERSION, "request_id": request_id, "command": self.name,
                "args": list(self.args), "timeout_ms": max(1, int(timeout * 1000))}
        data = (json.dumps(wire, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")
        if len(data) > MAX_MESSAGE_BYTES:
            raise NoemuError("message_too_large", "request exceeds 1 MiB")
        return data


@dataclass(frozen=True)
class Result:
    data: dict[str, Any]

    @classmethod
    def decode(cls, payload: bytes, request_id: str, instance: str | None) -> Result:
        def reject_constant(value):
            raise ValueError(f"nonfinite JSON value {value}")
        try:
            value = json.loads(payload.decode("utf-8"), parse_constant=reject_constant)
            if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != VERSION:
                raise ValueError("unsupported response version")
            if value.get("request_id") != request_id:
                raise ValueError("response request identity does not match")
            identity = value["identity"]
            if identity["instance"] != instance or not isinstance(identity["profile"], str):
                raise ValueError("response instance/profile identity does not match")
            if identity["build"] is not None and not isinstance(identity["build"], str):
                raise ValueError("invalid build identity")
            status = value["status"]
            if status not in ("passed", "failed", "unsupported", "inconclusive"):
                raise ValueError("invalid result status")
            error = value["error"]
            if status == "passed" and error is not None:
                raise ValueError("successful result carries an error")
            if status != "passed" and (not isinstance(error, dict) or
                    not all(isinstance(error.get(k), str) and error[k] for k in ("code", "message"))):
                raise ValueError("failed result has no typed error")
            if not isinstance(value["text"], str):
                raise ValueError("invalid result text")
            for key in ("capabilities", "limitations", "artifacts"):
                if not isinstance(value[key], list):
                    raise ValueError(f"invalid {key}")
            if value["observation"] is not None and not isinstance(value["observation"], dict):
                raise ValueError("invalid observation")
            elapsed = value["timing"]["duration_ms"]
            if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
                raise ValueError("invalid timing")
        except (UnicodeError, ValueError, KeyError, TypeError) as error:
            raise NoemuError("protocol_error", str(error)) from error
        return cls(value)

    def text(self) -> str:
        if self.data["status"] != "passed":
            error = self.data["error"]
            raise NoemuError(error["code"], error["message"], self.data)
        return self.data["text"]
