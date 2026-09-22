"""Talking to local AI engines.

nanoLaama does not implement a model itself. It finds the engine you already
have — or that it can install for you — and turns its command-line interface
into buttons and a chat box.

Two families are supported, which between them cover almost everything:

* **ollama**  — its own friendly HTTP API (``/api/chat``, ``/api/pull``)
* **openai**  — anything that speaks ``/v1/chat/completions``: llama.cpp's
  ``llama-server``, LM Studio, vLLM, and the author's own ``nanollama.c serve``
"""

from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.request

from . import hardware

# --------------------------------------------------------------------------
# Engines we know how to talk to
# --------------------------------------------------------------------------
ENGINE_CATALOG = [
    {
        "id": "ollama",
        "name": "Ollama",
        "kind": "ollama",
        "url": "http://127.0.0.1:11434",
        "blurb": "The easiest way to run AI on a normal computer. Install it once — "
                 "then nanoLaama can download models and chat with one click.",
        "site": "https://ollama.com/download",
        "install": {
            "win32": ["winget", "install", "--id", "Ollama.Ollama", "-e",
                      "--accept-source-agreements", "--accept-package-agreements"],
            "darwin": ["brew", "install", "ollama"],
            "linux": ["sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"],
        },
        "best_for": "Easiest start",
        "can_download": True,
    },
    {
        "id": "lmstudio",
        "name": "LM Studio",
        "kind": "openai",
        "url": "http://127.0.0.1:1234",
        "blurb": "A point-and-click desktop app for running models. Open it, start its "
                 "local server, and it appears here automatically.",
        "site": "https://lmstudio.ai",
        "best_for": "Browsing models visually",
    },
    {
        "id": "llamacpp",
        "name": "llama.cpp",
        "kind": "openai",
        "url": "http://127.0.0.1:8080",
        "blurb": "Tiny and very fast. If you already run llama-server with a .gguf file, "
                 "nanoLaama will find it on port 8080.",
        "site": "https://github.com/ggml-org/llama.cpp",
        "best_for": "A single file you already have",
    },
    {
        "id": "nanollama",
        "name": "nanollama.c",
        "kind": "openai",
        "url": "http://127.0.0.1:8090",
        "blurb": "The from-scratch C engine from the same family as this app. nanoLaama can "
                 "compile it, start it, and chat with the models it runs — all offline.",
        "site": "https://github.com/Agarwalrishu13/nanollama.c",
        "best_for": "Offline models trained by nanobrain",
        "native": True,
        "can_download": False,
    },
]

ENGINES_BY_ID = {engine["id"]: engine for engine in ENGINE_CATALOG}

# --------------------------------------------------------------------------
# Models a beginner can download with one click (Ollama names, approximate sizes)
# --------------------------------------------------------------------------
MODEL_CATALOG = [
    {
        "name": "qwen2.5:0.5b",
        "label": "Qwen 2.5 — Tiny",
        "size_gb": 0.4,
        "ram_gb": 2,
        "blurb": "The smallest one that is still useful. Runs on almost any computer, "
                 "even a very old laptop.",
        "level": "Any computer",
    },
    {
        "name": "llama3.2:1b",
        "label": "Llama 3.2 — Small",
        "size_gb": 1.3,
        "ram_gb": 4,
        "blurb": "A great first model. Fast, friendly, good at quick questions, "
                 "rewriting and summarising.",
        "level": "Recommended",
    },
    {
        "name": "qwen2.5:1.5b",
        "label": "Qwen 2.5 — Small+",
        "size_gb": 1.0,
        "ram_gb": 4,
        "blurb": "A little smarter than the 1B models and still quick on a laptop.",
        "level": "Recommended",
    },
    {
        "name": "qwen2.5-coder:1.5b",
        "label": "Qwen 2.5 Coder",
        "size_gb": 1.0,
        "ram_gb": 4,
        "blurb": "Tuned for programming questions — good if you want help reading code.",
        "level": "For coding",
    },
    {
        "name": "llama3.2:3b",
        "label": "Llama 3.2 — Medium",
        "size_gb": 2.0,
        "ram_gb": 6,
        "blurb": "Noticeably better answers than the 1B. Still comfortable on 8 GB of memory.",
        "level": "Better answers",
    },
    {
        "name": "gemma2:2b",
        "label": "Gemma 2 — Medium",
        "size_gb": 1.6,
        "ram_gb": 6,
        "blurb": "Google's small model. Clear writing, good at explaining things simply.",
        "level": "Better answers",
    },
    {
        "name": "phi3:mini",
        "label": "Phi-3 Mini",
        "size_gb": 2.3,
        "ram_gb": 6,
        "blurb": "Strong at reasoning for its size. Likes clear, direct instructions.",
        "level": "Better answers",
    },
    {
        "name": "mistral:7b",
        "label": "Mistral 7B",
        "size_gb": 4.1,
        "ram_gb": 10,
        "blurb": "A proper all-rounder. Wants 16 GB of memory to feel smooth.",
        "level": "Powerful",
    },
    {
        "name": "llama3.1:8b",
        "label": "Llama 3.1 — Large",
        "size_gb": 4.7,
        "ram_gb": 12,
        "blurb": "The best general answers in this list. Needs a recent machine with 16 GB.",
        "level": "Powerful",
    },
]


def recommended_models() -> list:
    """The catalogue, each entry marked with whether this computer can run it."""
    budget = hardware.memory_budget_gb()
    out = []
    for model in MODEL_CATALOG:
        entry = dict(model)
        entry["fits"] = model["ram_gb"] <= budget
        out.append(entry)
    return out


# --------------------------------------------------------------------------
# Small HTTP helpers
# --------------------------------------------------------------------------
def _request(url: str, payload=None, method: str = "POST", timeout: float = 4.0, headers: dict | None = None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json, text/event-stream")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    return urllib.request.urlopen(request, timeout=timeout)


def _get_json(url: str, timeout: float = 3.0, headers: dict | None = None):
    with _request(url, payload=None, method="GET", timeout=timeout, headers=headers) as response:
        body = response.read().decode("utf-8", errors="replace")
    return json.loads(body) if body.strip() else {}


def port_open(url: str, timeout: float = 0.5) -> bool:
    """Cheap check: is anything listening there at all?"""
    match = re.match(r"https?://([^/:]+):(\d+)", url)
    if not match:
        return False
    host, port = match.group(1), int(match.group(2))
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# --------------------------------------------------------------------------
# Engine detection
# --------------------------------------------------------------------------
def probe(engine: dict, timeout: float = 2.0) -> dict:
    """Ask one engine what it is and which models it has."""
    info = dict(engine)
    info["running"] = False
    info["models"] = []
    info["detail"] = ""

    if not port_open(engine["url"], timeout=min(timeout, 0.6)):
        info["detail"] = "Not running"
        return info

    try:
        if engine["kind"] == "ollama":
            data = _get_json(engine["url"] + "/api/tags", timeout=timeout)
            info["models"] = [
                {
                    "name": item.get("name", ""),
                    "size_gb": round(item.get("size", 0) / (1024 ** 3), 2),
                    "family": (item.get("details") or {}).get("family", ""),
                    "params": (item.get("details") or {}).get("parameter_size", ""),
                }
                for item in data.get("models", [])
                if item.get("name")
            ]
            info["running"] = True
            info["detail"] = "%d model%s ready" % (len(info["models"]), "" if len(info["models"]) == 1 else "s")
        else:
            # OpenAI-compatible: /v1/models is the standard, /health a nice bonus
            data = _get_json(engine["url"] + "/v1/models", timeout=timeout)
            entries = data.get("data", data if isinstance(data, list) else [])
            names = []
            for item in entries:
                if isinstance(item, dict):
                    name = item.get("id") or item.get("name")
                    if name:
                        names.append({"name": name, "size_gb": 0, "family": "", "params": ""})
                elif isinstance(item, str):
                    names.append({"name": item, "size_gb": 0, "family": "", "params": ""})
            info["models"] = names
            info["running"] = True
            info["detail"] = "%d model%s available" % (len(names), "" if len(names) == 1 else "s")
    except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout, json.JSONDecodeError, OSError) as exc:
        info["detail"] = "Answered oddly: %s" % exc
    return info


def detect(extra_engines: list | None = None) -> list:
    """Probe every known engine plus any the user added."""
    catalog = list(ENGINE_CATALOG) + list(extra_engines or [])
    return [probe(engine) for engine in catalog]


def find_engine(engine_id: str, extra_engines: list | None = None) -> dict | None:
    for engine in list(ENGINE_CATALOG) + list(extra_engines or []):
        if engine["id"] == engine_id:
            return engine
    return None


# --------------------------------------------------------------------------
# Chat (streaming)
# --------------------------------------------------------------------------
def _system_messages(system: str, messages: list) -> list:
    cleaned = [
        {"role": str(message.get("role", "user")), "content": str(message.get("content", ""))}
        for message in messages
        if str(message.get("content", "")).strip()
    ]
    if system.strip():
        return [{"role": "system", "content": system.strip()}] + cleaned
    return cleaned


def chat_stream(engine: dict, model: str, messages: list, temperature: float = 0.7,
                max_tokens: int = 512, system: str = ""):
    """Yield ``{"type": "delta"|"stats"|"error", ...}`` as the answer arrives."""
    if not model:
        yield {"type": "error", "message": "Pick a model first — open Settings and choose one."}
        return
    payload_messages = _system_messages(system, messages)
    if not payload_messages:
        yield {"type": "error", "message": "Type a message first."}
        return

    if engine["kind"] == "ollama":
        yield from _ollama_chat(engine, model, payload_messages, temperature, max_tokens)
    else:
        yield from _openai_chat(engine, model, payload_messages, temperature, max_tokens)


def _ollama_chat(engine, model, messages, temperature, max_tokens):
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": {"temperature": float(temperature), "num_predict": int(max_tokens)},
    }
    started = time.time()
    first_token_at = None
    pieces = 0
    try:
        with _request(engine["url"] + "/api/chat", payload, timeout=600.0) as response:
            for raw in response:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("error"):
                    yield {"type": "error", "message": str(event["error"])}
                    return
                content = (event.get("message") or {}).get("content", "")
                if content:
                    if first_token_at is None:
                        first_token_at = time.time()
                    pieces += 1
                    yield {"type": "delta", "text": content}
                if event.get("done"):
                    yield {
                        "type": "stats",
                        "model": model,
                        "engine": engine["name"],
                        "seconds": round(time.time() - started, 2),
                        "answer_tokens": event.get("eval_count", 0),
                        "prompt_tokens": event.get("prompt_eval_count", 0),
                        "tokens_per_second": round(
                            (event.get("eval_count", 0) or 0)
                            / max(event.get("eval_duration", 1) / 1e9, 1e-6),
                            1,
                        ) if event.get("eval_duration") else None,
                        "first_token_seconds": round(first_token_at - started, 2) if first_token_at else None,
                    }
                    return
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        if exc.code == 404:
            yield {"type": "error",
                   "message": "That engine does not have the model “%s”. Download it in Settings." % model}
        else:
            yield {"type": "error", "message": "The engine refused the request (%s). %s" % (exc.code, detail)}
        return
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        yield {"type": "error",
               "message": "Lost the connection to %s. Is it still running? (%s)" % (engine["name"], exc)}
        return
    yield {"type": "stats", "seconds": round(time.time() - started, 2), "answer_tokens": pieces}


def _openai_chat(engine, model, messages, temperature, max_tokens, allow_retry: bool = True):
    """Works with llama.cpp, LM Studio, nanollama.c and anything OpenAI-shaped.

    Handles both a real SSE stream and a plain one-shot JSON reply, because
    small engines (like nanollama.c's C server) answer in one piece. If the
    connection dies before any text arrives — small single-threaded servers do
    that when they are busy — the request is retried once without streaming.
    """
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
    }
    headers = {}
    if engine.get("api_key"):
        headers["Authorization"] = "Bearer " + engine["api_key"]

    started = time.time()
    first_token_at = None
    produced = False
    try:
        with _request(engine["url"] + "/v1/chat/completions", payload, timeout=600.0, headers=headers) as response:
            content_type = (response.headers.get("Content-Type") or "").lower()
            if "event-stream" not in content_type:
                # One-shot JSON answer.
                body = response.read().decode("utf-8", errors="replace")
                data = _parse_reply(body)
                if data is None:
                    yield {"type": "error",
                           "message": "The engine sent something unreadable. First 200 characters: %s" % body[:200]}
                    return
                text = _extract_openai_text(data)
                if text:
                    yield {"type": "delta", "text": text}
                    produced = True
                yield {"type": "stats", "model": model, "engine": engine["name"],
                       "seconds": round(time.time() - started, 2),
                       "salvaged": data.get("_salvaged", False),
                       "answer_tokens": (data.get("usage") or {}).get("completion_tokens", 0)}
                return

            sent_done = False
            for raw in response:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                data_text = line[5:].strip()
                if data_text == "[DONE]":
                    sent_done = True
                    break
                try:
                    data = json.loads(data_text)
                except json.JSONDecodeError:
                    continue
                if data.get("error"):
                    message = data["error"]
                    yield {"type": "error", "message": message if isinstance(message, str) else str(message)}
                    return
                text = _extract_openai_text(data)
                if text:
                    if first_token_at is None:
                        first_token_at = time.time()
                    produced = True
                    yield {"type": "delta", "text": text}
            yield {
                "type": "stats",
                "model": model,
                "engine": engine["name"],
                "seconds": round(time.time() - started, 2),
                "first_token_seconds": round(first_token_at - started, 2) if first_token_at else None,
                "answer_tokens": None,
                "note": "" if produced else "The engine returned an empty answer.",
                "done": sent_done,
            }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        yield {"type": "error", "message": "The engine refused the request (%s). %s" % (exc.code, detail)}
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        if allow_retry and not produced:
            yield {"type": "log", "message": "%s dropped the connection — trying once more." % engine["name"]}
            yield from _openai_chat(engine, model, messages, temperature, max_tokens, allow_retry=False)
            return
        yield {"type": "error",
               "message": "Lost the connection to %s. Is it still running? (%s)" % (engine["name"], exc)}


_CONTENT_START = re.compile(r'"content"\s*:\s*"')
_UNESCAPE = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f"}


def _unescape_loosely(text: str) -> str:
    out = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            out.append(_UNESCAPE.get(text[index + 1], text[index + 1]))
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _salvage_content(body: str) -> str:
    """Dig the answer out of a reply that is not quite valid JSON.

    Small hand-written engines — including nanollama.c's C server — sometimes
    write model output straight into the JSON string without escaping the
    quotes and newlines inside it. The text is real and the user wants to see
    it, so we take everything between ``"content": "`` and the structure that
    follows, instead of showing an error about JSON.
    """
    match = _CONTENT_START.search(body)
    if not match:
        return ""
    rest = body[match.end():]
    for marker in ('"finish_reason"', '"usage"', '"choices"', '"logprobs"'):
        cut = rest.find(marker)
        if cut != -1:
            rest = rest[:cut]
    rest = rest.rstrip()
    # Drop the JSON that closed the string: a quote followed by nothing but
    # braces, brackets, commas and whitespace.
    rest = re.sub(r'"\s*[\}\],\s]*$', "", rest)
    return _unescape_loosely(rest)


def _parse_reply(body: str):
    """Parse an engine's reply, tolerating the sloppiness of small C servers."""
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        pass
    try:
        # strict=False also allows raw control characters inside strings.
        return json.loads(body, strict=False)
    except json.JSONDecodeError:
        pass
    salvaged = _salvage_content(body)
    if salvaged:
        return {"choices": [{"message": {"content": salvaged}}], "_salvaged": True}
    return None


def _extract_openai_text(data: dict) -> str:
    """Pull the text out of either a streaming chunk or a whole reply."""
    try:
        choices = data.get("choices") or []
        if not choices:
            return ""
        choice = choices[0]
        delta = choice.get("delta") or {}
        if isinstance(delta, dict) and delta.get("content"):
            return delta["content"]
        message = choice.get("message") or {}
        if isinstance(message, dict) and message.get("content"):
            return message["content"]
        if choice.get("text"):
            return choice["text"]
    except (AttributeError, IndexError, TypeError):
        pass
    return ""


# --------------------------------------------------------------------------
# Downloading models (Ollama only — it is the one engine with a download API)
# --------------------------------------------------------------------------
def pull_stream(engine: dict, model: str):
    """Yield progress events while Ollama downloads a model."""
    if engine["kind"] != "ollama":
        yield {"type": "error",
               "message": "%s cannot download models for you. Download the file yourself, then drop it here."
                          % engine["name"]}
        return

    yield {"type": "log", "message": "Asking %s for “%s”…" % (engine["name"], model)}
    payload = {"name": model, "stream": True}
    last_status = ""
    try:
        with _request(engine["url"] + "/api/pull", payload, timeout=3600.0) as response:
            for raw in response:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("error"):
                    yield {"type": "error", "message": str(event["error"])}
                    return
                status = event.get("status") or ""
                completed = event.get("completed") or 0
                total = event.get("total") or 0
                if status != last_status:
                    last_status = status
                    yield {"type": "log", "message": status}
                if total:
                    yield {
                        "type": "progress",
                        "percent": round(100.0 * completed / total, 1),
                        "completed_gb": round(completed / (1024 ** 3), 2),
                        "total_gb": round(total / (1024 ** 3), 2),
                        "status": status,
                    }
        yield {"type": "done", "model": model, "message": "“%s” is ready. Start chatting!" % model}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        if exc.code == 404:
            yield {"type": "error", "message": "There is no model called “%s” to download." % model}
        else:
            yield {"type": "error", "message": "The download failed (%s). %s" % (exc.code, detail)}
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        yield {"type": "error",
               "message": "Lost the connection while downloading (%s). Check that Ollama is still running." % exc}


def import_gguf(engine: dict, path: str, name: str):
    """Turn a dropped .gguf file into a model Ollama can run."""
    if engine["kind"] != "ollama":
        yield {"type": "error", "message": "Importing a file needs Ollama. Install it, or use llama.cpp instead."}
        return
    import os
    import tempfile

    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", name).strip("-").lower() or "my-model"
    modelfile = 'FROM "%s"\n' % path.replace("\\", "/")
    handle = tempfile.NamedTemporaryFile("w", suffix=".Modelfile", delete=False, encoding="utf-8")
    try:
        handle.write(modelfile)
        handle.close()
        yield {"type": "log", "message": "Teaching Ollama about “%s”…" % safe_name}
        payload = {"name": safe_name, "modelfile": modelfile, "stream": True}
        with _request(engine["url"] + "/api/create", payload, timeout=1800.0) as response:
            for raw in response:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("error"):
                    yield {"type": "error", "message": str(event["error"])}
                    return
                status = event.get("status")
                if status:
                    yield {"type": "log", "message": status}
        yield {"type": "done", "model": safe_name, "message": "“%s” is ready to chat with." % safe_name}
    except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout, OSError) as exc:
        yield {"type": "error", "message": "Could not import that file (%s)." % exc}
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
