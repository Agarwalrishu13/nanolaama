"""Command line entry point.

Three ways in, in increasing order of nerdiness::

    python start.py            # start the app and open the browser
    python -m nanolaama        # the same thing
    python -m nanolaama doctor # print what this computer has, and stop
"""

from __future__ import annotations

import argparse
import sys

from . import APP_NAME, __version__, backends, hardware, native, store


def _doctor() -> int:
    """Report what the app can see. Useful when something is not working."""
    print()
    print("  %s %s — what this computer has" % (APP_NAME, __version__))
    print("  " + "-" * 56)
    print("  %s" % hardware.describe())
    print("  Python %s on %s" % (hardware.profile()["python"], hardware.profile()["os"]))
    print("  Settings and models live in: %s" % store.data_dir())
    print()
    print("  AI engines")
    for engine in backends.detect():
        state = "running" if engine.get("running") else "not running"
        print("    %-14s %-12s %s" % (engine["name"], state, engine.get("detail", "")))
        if engine.get("running"):
            for model in engine.get("models", [])[:6]:
                print("        · %s" % model["name"])
    print()
    summary = native.summary(store.load_settings().get("engine_paths", {}).get("nanollama_repo", ""))
    print("  Your own engine (nanollama.c)")
    print("    found:   %s (%s)" % ("yes" if summary["found"] else "no", summary["how"]))
    if summary["found"]:
        print("    path:    %s" % summary["path"])
        print("    built:   %s" % ("yes" if summary["built"] else "not yet"))
        print("    models:  %d" % len(summary["models"]))
    print()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nanolaama",
        description="%s — %s" % (APP_NAME, "talk to an AI on your own computer."),
        epilog="Run it with no arguments and a browser window opens. That is the whole idea.",
    )
    parser.add_argument("command", nargs="?", default="run", choices=["run", "doctor", "version"],
                        help="what to do (default: run)")
    parser.add_argument("--port", type=int, default=8760, help="which port to use (default 8760)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="address to listen on (default 127.0.0.1 — this computer only)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    parser.add_argument("--version", action="store_true", help="print the version and stop")
    args = parser.parse_args(argv)

    if args.version or args.command == "version":
        print("%s %s" % (APP_NAME, __version__))
        return 0
    if args.command == "doctor":
        return _doctor()

    from .server import create_app

    app = create_app()
    try:
        app.serve(host=args.host, port=args.port, open_browser=not args.no_browser)
    except OSError as exc:
        print("\n  Could not start on %s:%d (%s).\n  Try a different port: --port 8770\n"
              % (args.host, args.port, exc))
        return 1
    finally:
        native.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
