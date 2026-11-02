"""Checked benchmark commands. Command diagnostics are never UI observations."""
import os
import subprocess
import sys

NOEMU = [sys.executable, "-m", "noemu"]


class CommandError(RuntimeError):
    def __init__(self, command, status, message, *, returncode=None, stdout="", stderr=""):
        super().__init__(message)
        self.status = status
        self.record = {"command": list(command), "status": status, "message": message,
                       "returncode": returncode, "stdout": stdout, "stderr": stderr}


def text(value):
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""


def run_checked(command, *, cwd, timeout, env=None):
    try:
        result = subprocess.run(command, cwd=cwd, env=env, timeout=timeout,
                                capture_output=True, text=True)
    except subprocess.TimeoutExpired as error:
        raise CommandError(command, "inconclusive", f"command exceeded {timeout}s; outcome unknown",
                           stdout=text(error.stdout), stderr=text(error.stderr)) from error
    except OSError as error:
        raise CommandError(command, "inconclusive", f"command could not start: {error}") from error
    if result.returncode:
        raise CommandError(command, "failed", f"command exited {result.returncode}",
                           returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
    return result


def app_environment():
    env = dict(os.environ)
    # Benchmarks own the default session in their disposable project. A shell's
    # selected fleet instance must not redirect their actions or cleanup.
    env.pop("NOEMU_ID", None)
    return env
