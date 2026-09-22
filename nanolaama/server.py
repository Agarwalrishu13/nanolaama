"""Every address the browser talks to.

The front end is a single page that calls these JSON/streaming endpoints. The
naming is deliberately boring — ``/api/chat``, ``/api/models`` — because the
interesting work happens in :mod:`backends` and :mod:`native`.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from . import APP_NAME, __version__, backends, hardware, native, store
from .httpbase import App, Error, Json, Stream, Text

WEB_DIR = Path(__file__).parent / "web"

# One in-flight upload per upload id; small and process-local on purpose.
_uploads: dict[str, dict] = {}
_uploads_lock = threading.Lock()

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._\-+ ]+")


def _safe_filename(name: str) -> str:
    cleaned = _SAFE_NAME.sub("_", os.path.basename(name or "")).strip(" .")
    return cleaned[:180] or "model.bin"


def create_app() -> App:
    app = App(APP_NAME, WEB_DIR, __version__)

    # ---------------------------------------------------------------- status
    @app.get("/api/health")
    def health(_request):
        return Json({"ok": True, "app": APP_NAME, "version": __version__})

    @app.get("/api/status")
    def status(request):
        settings = store.load_settings()
        engines = backends.detect(settings.get("custom_engines"))
        running = [engine for engine in engines if engine.get("running")]
        return Json({
            "app": APP_NAME,
            "version": __version__,
            "hardware": hardware.profile(),
            "hardware_sentence": hardware.describe(),
            "memory_budget_gb": round(hardware.memory_budget_gb(), 1),
            "engines": engines,
            "engines_running": [engine["id"] for engine in running],
            "needs_setup": not running,
            "settings": settings,
            "native": native.summary(settings.get("engine_paths", {}).get("nanollama_repo", "")),
        })

    # --------------------------------------------------------------- engines
    @app.get("/api/engines")
    def engines(request):
        settings = store.load_settings()
        return Json({"engines": backends.detect(settings.get("custom_engines"))})

    @app.post("/api/engines/add")
    def add_engine(request):
        """Let someone plug in any OpenAI-compatible service (Groq, OpenRouter, vLLM…)."""
        body = request.json()
        base = str(body.get("url", "")).strip().rstrip("/")
        if not base.startswith(("http://", "https://")):
            return Error("The address needs to start with http:// or https://")
        name = str(body.get("name", "")).strip() or "My AI service"
        engine = {
            "id": "custom-" + uuid.uuid4().hex[:8],
            "name": name,
            "kind": "openai",
            "url": base,
            "api_key": str(body.get("api_key", "")).strip(),
            "blurb": "An AI service you added yourself.",
            "site": base,
            "custom": True,
        }
        settings = store.load_settings()
        customs = [item for item in settings.get("custom_engines", []) if item.get("url") != base]
        customs.append(engine)
        store.save_settings({"custom_engines": customs})
        return Json({"engine": backends.probe(engine)})

    @app.post("/api/engines/remove")
    def remove_engine(request):
        engine_id = str(request.json().get("id", ""))
        settings = store.load_settings()
        customs = [item for item in settings.get("custom_engines", []) if item.get("id") != engine_id]
        store.save_settings({"custom_engines": customs})
        return Json({"ok": True, "engines": backends.detect(customs)})

    @app.post("/api/install")
    def install(request):
        """Run the official installer for an engine, streaming the output.

        The command is shown to the user before they press the button, and it
        is the vendor's own documented install command.
        """
        engine_id = str(request.json().get("engine", ""))
        engine = backends.find_engine(engine_id) or {}
        if not engine:
            return Error("I do not know that one.")
        if engine_id.startswith("custom-"):
            return Error("That one has to be started by you — I only know how to talk to it.")
        command_map = engine.get("install") or {}
        command = command_map.get(sys.platform)
        if not command:
            return Error(
                "%s does not have an automatic installer on this system. Open %s and follow the "
                "instructions there — it takes a couple of minutes."
                % (engine["name"], engine.get("site", ""))
            )

        commands = command if isinstance(command, list) else [command]
        program = commands[0]
        if not shutil.which(program) and not os.path.isabs(program):
            return Error(
                "I need “%s” to install %s automatically, and it is not on this computer. "
                "Download it by hand from %s instead — that always works."
                % (program, engine["name"], engine.get("site", ""))
            )

        def events():
            yield {"type": "log", "message": "Running: %s" % " ".join(commands)}
            try:
                process = subprocess.Popen(
                    commands, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, errors="replace", shell=isinstance(command, str),
                )
            except OSError as exc:
                yield {"type": "error", "message": "The installer would not start: %s" % exc}
                return
            assert process.stdout is not None
            for line in iter(process.stdout.readline, ""):
                line = line.rstrip()
                if line:
                    yield {"type": "log", "message": line}
            process.wait()
            if process.returncode == 0:
                yield {"type": "done", "message": "%s is installed. Looking for it…" % engine["name"]}
                for _ in range(30):
                    found = backends.probe(engine, timeout=1.5)
                    if found.get("running"):
                        yield {"type": "ready", "engine": found}
                        return
                    yield {"type": "log", "message": "Waiting for %s to wake up…" % engine["name"]}
                    threading.Event().wait(2.0)
                yield {"type": "log",
                       "message": "%s is installed but not running yet. Start it from your programs menu, "
                                  "then press “Look again”." % engine["name"]}
            else:
                yield {"type": "error", "message":
                       "The installer stopped with code %d. You can always install it by hand from %s."
                       % (process.returncode, engine.get("site", ""))}

        return Stream.sse(events())

    # ---------------------------------------------------------------- models
    @app.get("/api/models")
    def models(request):
        settings = store.load_settings()
        detected = backends.detect(settings.get("custom_engines"))
        return Json({
            "catalog": backends.recommended_models(),
            "engines": detected,
            "memory_budget_gb": round(hardware.memory_budget_gb(), 1),
        })

    @app.post("/api/pull")
    def pull(request):
        body = request.json()
        settings = store.load_settings()
        engine = backends.find_engine(str(body.get("engine", "")), settings.get("custom_engines"))
        model = str(body.get("model", "")).strip()
        if not engine:
            return Error("Pick which engine should get this model.")
        if not model:
            return Error("Which model would you like?")
        return Stream.sse(backends.pull_stream(engine, model))

    @app.post("/api/import")
    def import_model(request):
        """Someone dropped a .gguf on the window — make it runnable."""
        body = request.json()
        settings = store.load_settings()
        engine = backends.find_engine(str(body.get("engine", "ollama")), settings.get("custom_engines"))
        path = str(body.get("path", ""))
        if not path or not Path(path).is_file():
            return Error("I lost track of that file. Try dropping it again.")
        if not engine:
            return Error("Install Ollama first — that is what turns a file into a usable model.")
        name = str(body.get("name", "")).strip() or Path(path).stem
        return Stream.sse(backends.import_gguf(engine, path, name))

    @app.get("/api/files")
    def files(_request):
        """Model files already sitting in the app's folder."""
        found = []
        for path in sorted(store.models_dir().glob("*")):
            if path.is_file() and path.suffix.lower() in (".bin", ".gguf", ".safetensors"):
                found.append({
                    "name": path.name,
                    "path": str(path),
                    "size_gb": round(path.stat().st_size / (1024 ** 3), 3),
                    "kind": path.suffix.lower().lstrip("."),
                })
        return Json({"files": found, "folder": str(store.models_dir())})

    # --------------------------------------------------------------- uploads
    @app.post("/api/upload/start")
    def upload_start(request):
        body = request.json()
        name = _safe_filename(str(body.get("name", "model.bin")))
        upload_id = uuid.uuid4().hex
        target = store.uploads_dir() / (upload_id + ".part")
        target.write_bytes(b"")
        with _uploads_lock:
            _uploads[upload_id] = {"name": name, "path": target, "received": 0}
        return Json({"id": upload_id, "name": name, "chunk_bytes": 8 * 1024 * 1024})

    @app.post("/api/upload/chunk")
    def upload_chunk(request):
        upload_id = request.q("id", "")
        with _uploads_lock:
            entry = _uploads.get(upload_id)
        if not entry:
            return Error("That upload expired. Start again.")
        try:
            offset = int(request.q("offset", "-1"))
        except ValueError:
            return Error("Bad offset.")
        if offset != entry["received"]:
            return Error("The pieces arrived out of order.", 409, expected=entry["received"])
        with open(entry["path"], "ab") as handle:
            handle.write(request.body)
        entry["received"] += len(request.body)
        return Json({"received": entry["received"]})

    @app.post("/api/upload/finish")
    def upload_finish(request):
        upload_id = str(request.json().get("id", ""))
        with _uploads_lock:
            entry = _uploads.pop(upload_id, None)
        if not entry:
            return Error("That upload expired. Start again.")
        destination = store.models_dir() / entry["name"]
        os.replace(entry["path"], destination)
        size_gb = round(destination.stat().st_size / (1024 ** 3), 2)
        return Json({
            "name": destination.name,
            "path": str(destination),
            "size_gb": size_gb,
            "kind": destination.suffix.lower().lstrip("."),
            "message": "%s arrived (%s GB)." % (destination.name, size_gb),
        })

    # ------------------------------------------------------------- your engine
    @app.get("/api/native")
    def native_status(_request):
        settings = store.load_settings()
        return Json(native.summary(settings.get("engine_paths", {}).get("nanollama_repo", "")))

    @app.post("/api/native/locate")
    def native_locate(request):
        path = str(request.json().get("path", "")).strip().strip('"')
        found = native.find_repo(path)
        if not found.get("found"):
            return Error("I could not find a nanollama.c project in “%s”. Pick the folder that has src/main.c in it." % path)
        paths = store.load_settings().get("engine_paths", {})
        paths["nanollama_repo"] = found["path"]
        store.save_settings({"engine_paths": paths})
        return Json(native.summary(found["path"]))

    @app.post("/api/native/build")
    def native_build(request):
        settings = store.load_settings()
        repo = str(request.json().get("path", "")) or settings.get("engine_paths", {}).get("nanollama_repo", "")
        found = native.find_repo(repo)
        if not found.get("found"):
            return Error("I do not know where nanollama.c is yet. Use “Find it” first.")
        return Stream.sse(native.build(found["path"]))

    @app.post("/api/native/serve")
    def native_serve(request):
        body = request.json()
        settings = store.load_settings()
        repo = str(body.get("path", "")) or settings.get("engine_paths", {}).get("nanollama_repo", "")
        found = native.find_repo(repo)
        if not found.get("found"):
            return Error("I do not know where nanollama.c is yet. Use “Find it” first.")
        return Stream.sse(native.serve(
            found["path"],
            str(body.get("model", "")),
            str(body.get("tokenizer", "")),
            int(body.get("port", native.DEFAULT_PORT) or native.DEFAULT_PORT),
            int(body.get("threads", 0) or 0),
            bool(body.get("quantize", False)),
        ))

    @app.post("/api/native/stop")
    def native_stop(_request):
        return Json(native.stop())

    @app.get("/api/native/log")
    def native_log(_request):
        return Json({"log": native.log_tail()})

    # ------------------------------------------------------------------ chat
    @app.post("/api/chat")
    def chat(request):
        body = request.json()
        settings = store.load_settings()
        engine = backends.find_engine(str(body.get("engine", "")), settings.get("custom_engines"))
        if not engine:
            return Error("Choose which AI should answer (open Settings).")
        if not engine.get("url"):
            return Error("That engine has no address saved.")
        messages = body.get("messages") or []
        if not isinstance(messages, list):
            return Error("The conversation could not be read.")
        return Stream.sse(backends.chat_stream(
            engine,
            str(body.get("model", "")),
            messages,
            float(body.get("temperature", settings["temperature"])),
            int(body.get("max_tokens", settings["max_tokens"])),
            str(body.get("system", settings["system_prompt"])),
        ))

    # -------------------------------------------------------------- settings
    @app.get("/api/settings")
    def get_settings(_request):
        return Json(store.load_settings())

    @app.post("/api/settings")
    def set_settings(request):
        patch = request.json()
        if not isinstance(patch, dict):
            return Error("Settings must be a set of named values.")
        return Json(store.save_settings(patch))

    # ---------------------------------------------------------------- chats
    @app.get("/api/chats")
    def get_chats(_request):
        return Json({"chats": store.load_chats()})

    @app.post("/api/chats")
    def put_chat(request):
        body = request.json()
        if not isinstance(body, dict) or not body.get("id"):
            return Error("A conversation needs an id.")
        return Json({"chats": store.save_chat(body)})

    @app.delete("/api/chats/{chat_id}")
    def drop_chat(request):
        return Json({"chats": store.delete_chat(request.params["chat_id"])})

    @app.get("/api/about")
    def about(_request):
        return Json({
            "app": APP_NAME,
            "version": __version__,
            "data_folder": str(store.data_dir()),
            "python": sys.version.split()[0],
            "offline": True,
            "siblings": [
                {"name": "nanollama.c", "url": "https://github.com/Agarwalrishu13/nanollama.c",
                 "what": "the C inference engine"},
                {"name": "nanobrain", "url": "https://github.com/Agarwalrishu13/nanobrain",
                 "what": "the from-scratch trainer"},
                {"name": "nanoforge", "url": "https://github.com/Agarwalrishu13/nanoforge",
                 "what": "the offline studio"},
            ],
        })

    @app.get("/api/text")
    def text(request):
        """Small helper used by the “copy this command” buttons."""
        return Text(str(request.q("value", "")))

    return app
