"""Where nanoLaama keeps its settings and conversations.

Everything lives in a single folder in the user's home directory, as plain
JSON. Delete the folder and the app forgets everything — no database, no
cloud, no account.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path

APP_DIR_NAME = ".nanolaama"

_lock = threading.Lock()

DEFAULT_SETTINGS = {
    "engine": "",            # engine id, e.g. "ollama"
    "model": "",             # model name inside that engine
    "temperature": 0.7,
    "max_tokens": 512,
    "system_prompt": "",
    "expert_mode": False,    # hide the jargon by default
    "theme": "dark",
    "custom_engines": [],    # user-added OpenAI-compatible endpoints
    "engine_paths": {},      # e.g. {"nanollama_repo": "C:/.../nanollama.c"}
    "completed_setup": False,
}


def data_dir() -> Path:
    """The app's folder in the user's home directory."""
    override = os.environ.get("NANOLAAMA_HOME")
    path = Path(override) if override else Path.home() / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def models_dir() -> Path:
    """Where dragged-in model files are stored."""
    path = data_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def uploads_dir() -> Path:
    """Scratch space for in-progress file uploads."""
    path = data_dir() / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_json(path: Path, fallback):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, type(fallback)) else fallback
    except (OSError, json.JSONDecodeError):
        return fallback


def _write_json(path: Path, value) -> None:
    """Write JSON atomically so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), prefix=path.name + ".", suffix=".tmp", delete=False
    )
    try:
        with handle:
            json.dump(value, handle, indent=2)
        os.replace(handle.name, path)
    except Exception:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------- settings
def settings_path() -> Path:
    return data_dir() / "settings.json"


def load_settings() -> dict:
    with _lock:
        stored = _read_json(settings_path(), {})
    merged = dict(DEFAULT_SETTINGS)
    merged.update({key: value for key, value in stored.items() if key in DEFAULT_SETTINGS})
    return merged


def save_settings(patch: dict) -> dict:
    with _lock:
        current = dict(DEFAULT_SETTINGS)
        current.update({k: v for k, v in _read_json(settings_path(), {}).items() if k in DEFAULT_SETTINGS})
        for key, value in patch.items():
            if key in DEFAULT_SETTINGS:
                current[key] = value
        _write_json(settings_path(), current)
    return current


# ------------------------------------------------------------------ conversations
def chats_path() -> Path:
    return data_dir() / "chats.json"


def load_chats() -> list:
    chats = _read_json(chats_path(), [])
    return [chat for chat in chats if isinstance(chat, dict)]


def save_chat(chat: dict) -> list:
    """Insert or update one conversation (newest first) and return them all."""
    if not isinstance(chat, dict) or not chat.get("id"):
        raise ValueError("a conversation needs an id")
    chat.setdefault("title", "New chat")
    chat["updated"] = time.time()
    with _lock:
        chats = [item for item in _read_json(chats_path(), []) if isinstance(item, dict)]
        chats = [item for item in chats if item.get("id") != chat["id"]]
        chats.insert(0, chat)
        del chats[40:]  # keep the file small; nobody scrolls past 40 chats
        _write_json(chats_path(), chats)
    return chats


def delete_chat(chat_id: str) -> list:
    with _lock:
        chats = [item for item in _read_json(chats_path(), []) if isinstance(item, dict)]
        chats = [item for item in chats if item.get("id") != chat_id]
        _write_json(chats_path(), chats)
    return chats
