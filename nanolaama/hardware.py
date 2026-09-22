"""Look at the computer we are running on and describe it like a human would.

The point is to answer one question for someone who has never run an AI model:
*"which of these things will actually work on my laptop?"*
"""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
import sys
from functools import lru_cache


def _run(command, timeout: float = 4.0) -> str:
    try:
        done = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
        return (done.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


@lru_cache(maxsize=1)
def total_ram_gb() -> float:
    """Total physical memory in gigabytes (0.0 if it cannot be read)."""
    try:
        if sys.platform == "win32":
            class _MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return round(status.ullTotalPhys / (1024 ** 3), 1)

        elif sys.platform == "darwin":
            out = _run(["sysctl", "-n", "hw.memsize"])
            if out.isdigit():
                return round(int(out) / (1024 ** 3), 1)

        else:
            with open("/proc/meminfo", "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return round(kb / (1024 ** 2), 1)
    except Exception:
        pass
    return 0.0


@lru_cache(maxsize=1)
def gpus() -> list[dict]:
    """Detect NVIDIA GPUs via nvidia-smi, and Apple Silicon as unified memory."""
    found: list[dict] = []
    nvidia = shutil.which("nvidia-smi")
    if nvidia:
        out = _run(
            [
                nvidia,
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ]
        )
        for line in out.splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) >= 2 and parts[1].replace(".", "", 1).isdigit():
                found.append({"name": parts[0], "vram_gb": round(float(parts[1]) / 1024, 1), "kind": "nvidia"})
    if not found and sys.platform == "darwin" and platform.machine() == "arm64":
        found.append({"name": "Apple Silicon (unified memory)", "vram_gb": total_ram_gb() * 0.7, "kind": "apple"})
    return found


@lru_cache(maxsize=1)
def profile() -> dict:
    """A single dictionary describing this machine."""
    cores = os.cpu_count() or 1
    return {
        "os": platform.system() or sys.platform,
        "os_release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_cores": cores,
        "ram_gb": total_ram_gb(),
        "gpus": gpus(),
    }


def describe() -> str:
    """One friendly sentence: "This computer has 16 GB of memory and 8 cores."."""
    info = profile()
    bits = []
    if info["ram_gb"]:
        bits.append("%g GB of memory" % info["ram_gb"])
    bits.append("%d processor core%s" % (info["cpu_cores"], "" if info["cpu_cores"] == 1 else "s"))
    sentence = "This computer has " + " and ".join(bits)
    if info["gpus"]:
        names = ", ".join(gpu["name"] for gpu in info["gpus"])
        sentence += ", plus a graphics card (%s) that can make the AI much faster" % names
    return sentence + "."


def memory_budget_gb() -> float:
    """How much memory we dare to recommend a model to use."""
    info = profile()
    ram = info["ram_gb"] or 8.0
    vram = max((gpu["vram_gb"] for gpu in info["gpus"]), default=0.0)
    # Leave room for the operating system; a GPU budget wins when it is smaller.
    ram_budget = ram * 0.6
    if vram and vram < ram_budget:
        return vram
    return ram_budget


def fits(required_gb: float) -> bool:
    return required_gb <= memory_budget_gb()
