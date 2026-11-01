"""Version 1 command and result model shared by CLI, MCP and driver transport."""
from __future__ import annotations

import json
import math
import re
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
        if not isinstance(self.name, str) or not isinstance(self.args, (tuple, list)):
            raise NoemuError("invalid_argument", "command requires a name and string arguments")
        object.__setattr__(self, "args", tuple(self.args))
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
        # Bound before int(): Python rejects very long decimal strings, and
        # converting attacker-controlled integers need not consume unbounded CPU.
        if (not isinstance(value, str) or not value.isascii() or not value.isdecimal()
                or len(value) > 20 or not minimum <= int(value) <= maximum):
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
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-zA-Z0-9-]{1,128}", request_id):
            raise NoemuError("invalid_argument", "invalid request_id")
        if type(timeout) not in (int, float) or not 0 < timeout <= 3600:
            raise NoemuError("invalid_argument", "timeout must be positive and at most 3600 seconds")
        wire = {"version": VERSION, "request_id": request_id, "command": self.name,
                "args": list(self.args), "timeout_ms": max(1, int(timeout * 1000))}
        data = (json.dumps(wire, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")
        if len(data) > MAX_MESSAGE_BYTES:
            raise NoemuError("message_too_large", "request exceeds 1 MiB")
        return data

    @property
    def timeout_seconds(self) -> int:
        return {"crawl": 3600, "leaks": 900}.get(self.name, 120)


@dataclass(frozen=True)
class Result:
    data: dict[str, Any]

    @classmethod
    def decode(cls, payload: bytes, request_id: str, instance: str | None) -> Result:
        if len(payload) > MAX_MESSAGE_BYTES:
            raise NoemuError("message_too_large", "driver response exceeds 1 MiB")
        def reject_constant(value):
            raise ValueError(f"nonfinite JSON value {value}")
        def unique_fields(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"duplicate JSON field {key}")
                result[key] = value
            return result
        try:
            value = json.loads(payload.decode("utf-8"), parse_constant=reject_constant,
                               object_pairs_hook=unique_fields)
            if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != VERSION:
                raise ValueError("unsupported response version")
            if value.get("request_id") != request_id:
                raise ValueError("response request identity does not match")
            identity = value["identity"]
            if (identity["instance"] != instance or not isinstance(identity["profile"], str)
                    or not identity["profile"]):
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
                if not isinstance(value[key], list) or not all(isinstance(item, str) and item for item in value[key]):
                    raise ValueError(f"invalid {key}")
            if value["observation"] is not None:
                cls.validate_observation(value["observation"])
            elapsed = value["timing"]["duration_ms"]
            if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
                raise ValueError("invalid timing")
        except (UnicodeError, ValueError, KeyError, TypeError, OverflowError, RecursionError) as error:
            raise NoemuError("protocol_error", str(error)) from error
        return cls(value)

    @staticmethod
    def validate_observation(observation: dict) -> None:
        if not isinstance(observation, dict):
            raise ValueError("invalid observation")
        if not all(isinstance(observation[k], str) and observation[k] for k in ("id", "activity")):
            raise ValueError("invalid observation identity")
        elements = observation["elements"]
        if not isinstance(elements, list):
            raise ValueError("invalid observation elements")
        for index, element in enumerate(elements):
            if not isinstance(element, dict) or element["ref"] != f"{observation['id']}:{index}":
                raise ValueError("invalid observation-scoped reference")
            if not isinstance(element["label"], str) or not isinstance(element["kind"], str) or not element["kind"]:
                raise ValueError("invalid element label/kind")
            if not all(type(element[k]) is bool for k in ("enabled", "visible", "editable")):
                raise ValueError("invalid element state")
            if not all(element[k] is None or isinstance(element[k], str) for k in ("selector", "state")):
                raise ValueError("invalid element selector/state")
            bounds = element["bounds"]
            if (not isinstance(bounds, list) or len(bounds) != 4
                    or not all(type(n) is int for n in bounds)
                    or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]):
                raise ValueError("invalid element bounds")

    def text(self) -> str:
        if self.data["status"] != "passed":
            error = self.data["error"]
            raise NoemuError(error["code"], error["message"], self.data)
        return self.data["text"]


@dataclass(frozen=True)
class ToolOutput:
    content: list[dict[str, Any]]
    result: Result | None = None
