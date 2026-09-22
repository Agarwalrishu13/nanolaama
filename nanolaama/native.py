"""The bridge to nanollama.c — the author's own C inference engine.

This module is what makes nanoLaama more than "another Ollama front end": it
can find the nanollama.c checkout on this computer, compile it, list the
models sitting in its ``models/`` folder, and start its built-in
OpenAI-compatible server. After that it behaves exactly like any other engine.

Every step is optional and every failure is explained in plain language —
the app works fine with Ollama alone.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ENGINE_HOME = "https://github.com/Agarwalrishu13/nanollama.c"
DEFAULT_PORT = 8090

# Where a checkout might reasonably live, checked in order.
_REPO_HINTS = ("nanollama.c", "nanollama", "nanollama-c")

_state_lock = threading.Lock()
_process: subprocess.Popen | None = None
_log: list[str] = []
_running_model = ""


# --------------------------------------------------------------------------
# Finding the checkout
# --------------------------------------------------------------------------
def _candidate_roots() -> list[Path]:
    """Folders worth searching for a nanollama.c checkout."""
    here = Path(__file__).resolve()
    roots = [
        here.parent.parent,           # the folder containing this app
        here.parent.parent.parent,    # one level up (a workspace holding both repos)
        Path.cwd(),
        Path.home(),
        Path.home() / "Desktop",
        Path.home() / "Documents",
        Path.home() / "projects",
        Path.home() / "code",
    ]
    out = []
    for root in roots:
        if root not in out and root.is_dir():
            out.append(root)
    return out


def find_repo(configured: str = "") -> dict:
    """Return ``{"path": ..., "how": ...}`` for the best checkout we can find."""
    if configured:
        candidate = Path(configured).expanduser()
        for path in (candidate, candidate / "nanollama.c"):
            if (path / "src" / "main.c").is_file():
                return {"path": str(path), "how": "you told me where it is", "found": True}
        if candidate.is_dir():
            return {"path": str(candidate), "how": "saved earlier, but it does not look like nanollama.c",
                    "found": False}

    for root in _candidate_roots():
        for hint in _REPO_HINTS:
            path = root / hint
            if (path / "src" / "main.c").is_file():
                return {"path": str(path), "how": "found next to this app", "found": True}
        # Some people unzip it without the folder name we expect.
        try:
            for entry in root.iterdir():
                if entry.is_dir() and (entry / "src" / "main.c").is_file() and (entry / "Makefile").is_file():
                    return {"path": str(entry), "how": "found while looking around", "found": True}
        except (OSError, PermissionError):
            continue

    on_path = shutil.which("nanollama") or shutil.which("nanollama.exe")
    if on_path:
        return {"path": str(Path(on_path).parent), "how": "found on your PATH", "found": True,
                "binary": str(on_path)}
    return {"path": "", "how": "not found on this computer", "found": False}


def find_binary(repo: str) -> str:
    """The compiled executable, if it has already been built."""
    if not repo:
        return ""
    base = Path(repo)
    names = ["nanollama.exe", "nanollama", "nanollama.out"] if os.name == "nt" else \
            ["nanollama", "nanollama.exe", "nanollama.out"]
    for name in names:
        for folder in (base, base / "build", base / "bin"):
            candidate = folder / name
            if candidate.is_file():
                return str(candidate)
    return ""


def compiler_available() -> tuple[bool, str]:
    """Which C compiler can we use? Returns (ok, name)."""
    for name in ("gcc", "clang", "cc", "tcc"):
        found = shutil.which(name)
        if found:
            return True, found
    for name in ("gcc",):  # MSYS2 / MinGW installs that are not on PATH
        for guess in (r"C:\msys64\mingw64\bin\gcc.exe", r"C:\mingw64\bin\gcc.exe", r"C:\MinGW\bin\gcc.exe"):
            if os.path.isfile(guess):
                return True, guess
    if shutil.which("cl"):
        return True, "cl"
    return False, ""


def _make_command(repo: str) -> list[str]:
    for name in ("make", "mingw32-make", "gmake", "nmake"):
        found = shutil.which(name)
        if found:
            return [found]
    return []


def build(repo: str):
    """Compile the engine, streaming the compiler's output line by line."""
    ok, compiler = compiler_available()
    if not ok:
        yield {"type": "error", "message":
               "I could not find a C compiler on this computer. That is the one thing needed to build "
               "the engine. On Windows install “MSYS2” (which brings gcc), on macOS run "
               "“xcode-select --install”, on Linux run “sudo apt install build-essential”. "
               "You do not need the engine to use nanoLaama with Ollama."}
        return

    yield {"type": "log", "message": "Using compiler: %s" % compiler}
    command = _make_command(repo)
    if command:
        yield {"type": "log", "message": "Running %s in %s" % (" ".join(command), repo)}
    else:
        sources = sorted(str(path) for path in (Path(repo) / "src").glob("*.c"))
        if not sources:
            yield {"type": "error", "message": "There are no .c files in %s/src — is that the right folder?" % repo}
            return
        output = str(Path(repo) / ("nanollama.exe" if os.name == "nt" else "nanollama"))
        command = [compiler, "-O2", "-std=c11", "-o", output] + sources
        if os.name == "nt":
            command.append("-lws2_32")
        command.append("-lpthread")
        yield {"type": "log", "message": "No make found, compiling directly: %s" % " ".join(command)}

    try:
        process = subprocess.Popen(
            command, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, errors="replace",
        )
    except OSError as exc:
        yield {"type": "error", "message": "Could not start the compiler: %s" % exc}
        return

    for line in iter(process.stdout.readline, ""):
        line = line.rstrip()
        if line:
            yield {"type": "log", "message": line}
    process.wait()
    if process.returncode == 0:
        binary = find_binary(repo)
        yield {"type": "done", "message": "Built! The engine is ready to start.",
               "binary": binary or "(check the folder)"}
    else:
        yield {"type": "error", "message": "The build stopped with an error (exit code %d). The lines above say why."
                                           % process.returncode}


# --------------------------------------------------------------------------
# Local models
# --------------------------------------------------------------------------
def _read_meta(path: Path) -> dict:
    for suffix in (".meta.json", ".json"):
        meta_path = Path(str(path) + suffix) if suffix != ".json" else path.with_suffix(".json")
        if meta_path.is_file():
            try:
                with open(meta_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                return data if isinstance(data, dict) else {}
            except (OSError, json.JSONDecodeError):
                pass
    return {}


def list_local_models(repo: str = "") -> list:
    """Find .bin models the engine can run, and pair each with its tokenizer."""
    folders: list[Path] = []
    for candidate in (repo, str(Path(repo) / "models") if repo else "", str(Path(repo) / "checkpoints") if repo else ""):
        if candidate and Path(candidate).is_dir():
            folders.append(Path(candidate))
    from . import store
    folders.append(store.models_dir())

    tokenizers: list[Path] = []
    models: list[Path] = []
    for folder in folders:
        try:
            for path in folder.glob("*.bin"):
                if path.is_file():
                    (tokenizers if "tokenizer" in path.name.lower() else models).append(path)
        except OSError:
            continue

    out = []
    for path in models:
        try:
            size_gb = round(path.stat().st_size / (1024 ** 3), 3)
        except OSError:
            size_gb = 0
        meta = _read_meta(path)
        stem = path.name[:-4]
        tokenizer = next(
            (t for t in tokenizers if stem.replace("-tiny", "").replace("-flagship", "") in t.name.lower()
             or t.name.lower().startswith("nanobrain-tokenizer")),
            tokenizers[0] if tokenizers else None,
        )
        out.append({
            "name": path.name,
            "path": str(path),
            "tokenizer": str(tokenizer) if tokenizer else "",
            "tokenizer_name": tokenizer.name if tokenizer else "",
            "size_gb": size_gb,
            "params": meta.get("params") or meta.get("n_params") or "",
            "detail": meta.get("description", ""),
            "src": str(path.parent),
        })
    out.sort(key=lambda item: item["size_gb"])
    return out


def pair_tokenizer(repo: str, model_path: str) -> str:
    """Best guess at the tokenizer file that goes with a model file."""
    models = list_local_models(repo)
    for entry in models:
        if entry["path"] == model_path:
            return entry["tokenizer"]
    return ""


# --------------------------------------------------------------------------
# Running the engine's server
# --------------------------------------------------------------------------
def status() -> dict:
    with _state_lock:
        alive = _process is not None and _process.poll() is None
        return {
            "running": alive,
            "model": _running_model,
            "port": DEFAULT_PORT,
            "url": "http://127.0.0.1:%d" % DEFAULT_PORT,
            "log": _log[-60:],
        }


def _port_free(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return False
    except OSError:
        return True


def serve(repo: str, model_path: str, tokenizer_path: str = "", port: int = DEFAULT_PORT,
          threads: int = 0, quantize: bool = False):
    """Start ``nanollama serve`` and stream its startup log."""
    global _process, _log, _running_model

    with _state_lock:
        if _process is not None and _process.poll() is None:
            yield {"type": "log", "message": "The engine is already running."}
            yield {"type": "done", "message": "Already running.", "url": "http://127.0.0.1:%d" % port}
            return
        _log = []

    binary = find_binary(repo)
    if not binary:
        yield {"type": "error", "message": "The engine is not built yet. Press “Build it” first."}
        return
    if not model_path or not Path(model_path).is_file():
        yield {"type": "error", "message": "Pick a model file first (a .bin file from the engine's models folder)."}
        return
    if not tokenizer_path:
        tokenizer_path = pair_tokenizer(repo, model_path)
    if not tokenizer_path or not Path(tokenizer_path).is_file():
        yield {"type": "error", "message":
               "This model needs its tokenizer file too (the .bin with “tokenizer” in its name). "
               "Keep it next to the model file."}
        return
    if not _port_free(port):
        yield {"type": "error", "message":
               "Port %d is already busy. Close whatever is using it, or change the port in Settings." % port}
        return

    command = [binary, "serve", "-m", model_path, "-z", tokenizer_path, "--port", str(port)]
    if threads:
        command += ["-th", str(threads)]
    if quantize:
        command.append("-q")

    yield {"type": "log", "message": "Starting: %s" % " ".join('"%s"' % part if " " in part else part
                                                              for part in command)}
    try:
        process = subprocess.Popen(
            command, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, errors="replace",
        )
    except OSError as exc:
        yield {"type": "error", "message": "Could not start the engine: %s" % exc}
        return

    with _state_lock:
        _process = process
        _running_model = Path(model_path).name

    # Relay the engine's own output in the background, then wait for the port.
    def pump():
        assert process.stdout is not None
        for line in iter(process.stdout.readline, ""):
            line = line.rstrip()
            if line:
                with _state_lock:
                    _log.append(line)

    threading.Thread(target=pump, daemon=True).start()

    deadline = time.time() + 90
    ready = False
    while time.time() < deadline:
        if process.poll() is not None:
            break
        if not _port_free(port):
            ready = True
            break
        time.sleep(0.4)

    if ready:
        yield {"type": "done", "message": "The engine is running on port %d." % port,
               "url": "http://127.0.0.1:%d" % port, "model": Path(model_path).name}
    else:
        code = process.poll()
        tail = "\n".join(status()["log"][-8:])
        yield {"type": "error", "message":
               "The engine stopped before it was ready%s. Its own message was:\n%s"
               % (" (exit code %s)" % code if code is not None else "", tail or "(no output)")}


def stop() -> dict:
    global _process, _running_model
    with _state_lock:
        process, _process = _process, None
        _running_model = ""
    if process is None or process.poll() is not None:
        return {"stopped": False, "message": "The engine was not running."}
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
    return {"stopped": True, "message": "The engine has stopped."}


def log_tail() -> list:
    with _state_lock:
        return list(_log[-40:])


def summary(repo: str = "") -> dict:
    """Everything the UI needs to render the “your own engine” card."""
    found = find_repo(repo)
    path = found.get("path", "")
    binary = find_binary(path)
    ok, compiler = compiler_available()
    return {
        "found": found.get("found", False),
        "how": found.get("how", ""),
        "path": path,
        "binary": binary,
        "built": bool(binary),
        "compiler": compiler,
        "can_build": ok,
        "models": list_local_models(path) if found.get("found") else [],
        "running": status()["running"],
        "engine_home": ENGINE_HOME,
        "port": DEFAULT_PORT,
    }
