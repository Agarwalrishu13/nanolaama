<div align="center">

# nanoLaama

**Talk to an AI on your own computer.** No code. No terminal. No account. No internet.

Double-click one file, a page opens in your browser, you type, it answers.
Under the hood it drives the AI engines that already exist on your machine — and
it can set one up for you.

[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.9+-58a6ff.svg)]()
[![dependencies](https://img.shields.io/badge/runtime%20deps-0-f0883e.svg)]()
[![runs](https://img.shields.io/badge/runs-on%20your%20machine-f0883e.svg)]()

</div>

---

> **Part of [the nano family](https://github.com/Agarwalrishu13/nano)** — eleven offline-first apps for people who do not code. This is the map of the whole project.


## What this is, in one paragraph

Most people who would benefit from a local AI never get one, because getting one
means learning a terminal. nanoLaama is the missing front door. It finds the AI
engines already installed on your computer (or installs one for you with one
click), lists the models you have, downloads new ones with a progress bar, and
gives you a chat window. It also wraps the author's own
[nanollama.c](https://github.com/Agarwalrishu13/nanollama.c) — it will find that
C project, compile it, and start it for you, so models trained by
[nanobrain](https://github.com/Agarwalrishu13/nanobrain) are one click away.

**Zero dependencies.** The entire app is the Python standard library plus three
static files. There is nothing to `pip install`, ever.

---

## Use it

1. Install Python if you do not have it — [python.org/downloads](https://www.python.org/downloads/).
   On Windows, tick **“Add python.exe to PATH”** during setup.
2. Download this repo (green **Code** button → *Download ZIP*) and unzip it.
3. **Windows:** double-click `run.bat`. **macOS / Linux:** double-click `run.sh`
   (or `./run.sh` in a terminal).
4. Your browser opens at `http://127.0.0.1:8760`. That is the app.

If no AI is set up yet, the page starts with a card that walks you through it.
The easy route is Ollama; press **“Install it for me”** and about three minutes
later you are chatting with a 1 GB model on your own laptop.

<details>
<summary>Prefer the command line? (you do not need to)</summary>

```bash
python start.py                 # start and open the browser
python -m nanolaama             # the same thing
python -m nanolaama doctor      # print what this computer has, then exit
python -m nanolaama --port 9000 --no-browser
```

</details>

---

## What it can talk to

| engine | how nanoLaama reaches it | who it is for |
|---|---|---|
| **Ollama** | its HTTP API on `127.0.0.1:11434` | the easiest start — one-click install, one-click model downloads |
| **LM Studio** | OpenAI-compatible API on `127.0.0.1:1234` | people who prefer a point-and-click model browser |
| **llama.cpp** (`llama-server`) | OpenAI-compatible API on `127.0.0.1:8080` | one `.gguf` file, maximum speed |
| **nanollama.c** | `nanollama serve` on `127.0.0.1:8090` | models trained from scratch by `nanobrain` |
| **your own service** | any OpenAI-compatible base URL + key | people who already pay for an API |

The last one is the interesting one. nanoLaama looks for a `nanollama.c`
checkout on your computer, offers to **build** it (`make`, or a direct `gcc`
line if you have no `make`), and then **starts its server** for you — streaming
the engine's own compiler and startup output into the settings panel so you can
see exactly what is happening.

---

## What you can do in the window

- **Chat** with streaming answers, stop button, and a copy button.
- **Drag a model file anywhere onto the window.** A `.gguf` gets imported into
  Ollama automatically (nanoLaama writes the Modelfile and calls
  `/api/create`); a `.bin` gets registered for your own engine. Files are
  copied in 8 MB slices, so a 4 GB model does not have to fit in memory.
- **Download models** from a curated list. Each one is labelled with how much
  memory it needs, and the ones your computer cannot run are dimmed with an
  explanation instead of quietly failing.
- **Simple mode by default.** Temperature, token counts and tokens-per-second
  stay hidden behind an *Expert mode* switch. Nobody has to see the word
  "token" on their first day.
- **Saved chats** you can revisit, and **Save** turns any chat into a markdown
  file.

---

## Where your stuff lives

Everything is in one folder in your home directory:

```
~/.nanolaama/
├── settings.json     your choices (engine, model, style)
├── chats.json        your conversations
├── models/           model files you dropped in
└── uploads/          scratch space while a file is arriving
```

Delete that folder and the app forgets everything. It never contacts a server
of ours — there isn't one. The only time it reaches the internet is when *you*
press a download or install button, and it then talks to that vendor directly.

---

## How it works (for the curious)

```
browser  ──►  httpbase.py     routing, static files, SSE streaming   (stdlib)
              server.py       the /api/* addresses
              backends.py     engine detection, chat proxying, downloads
              native.py       find+build+run nanollama.c
              hardware.py     "will this model fit on my laptop?"
              store.py        settings and chats as JSON
```

The server binds to `127.0.0.1` only, and the browser talks to it with
`fetch`. Streaming uses Server-Sent Events over chunked transfer encoding —
implemented in about thirty lines of `httpbase.py`, because a chat that types
itself is the entire point.

```bash
python -m unittest discover tests -v
```

## Tests

A smoke test boots the real server on a spare port and exercises every endpoint,
including a chunked upload and a chat request against a fake engine.

---

## Honest limitations

- nanoLaama is a **front end**. It does not run models itself; it needs one of
  the engines above. It says so plainly when none is found.
- Only **Ollama** can download models for you. Every other engine has to be
  given a file — drag one in.
- Answers are **exactly as good as the model you picked**. A 1 GB model on a
  laptop is quick and useful, not GPT-4.
- The install button runs the vendor's official installer
  (`winget`/`brew`/`curl | sh`). The exact command is shown in the log before
  it runs, and installing by hand from the vendor's site always works too.
- A small engine that is busy may drop a connection. nanoLaama retries once
  automatically before telling you anything went wrong.

### A note on engines that answer in imperfect JSON

Writing JSON by hand in C is easy to get subtly wrong, and nanoLaama is built
to be forgiving about it. If a reply is not quite valid JSON — because the
model's text contained a quote or a newline that the server did not escape —
nanoLaama digs the answer out of the response, shows it to you, and says so
rather than displaying a parse error. There is a unit test for exactly this
case (`tests/test_smoke.py::TestEngineReplyParsing`).

## The rest of the family

| app | what it is for |
|---|---|
| 🧭 [nanoHome](https://github.com/Agarwalrishu13/nanohome) | one front door for every nano app on this computer |
| 🧠 [**nanoLaama**](https://github.com/Agarwalrishu13/nanolaama) | talk to an AI on your own computer, offline — *this repo* |
| 📚 [nanoDoc](https://github.com/Agarwalrishu13/nanodoc) | drop in a document, ask it anything |
| 📊 [nanoLearn](https://github.com/Agarwalrishu13/nanolearn) | drop a spreadsheet, get an answer machine |
| 🔊 [nanoSay](https://github.com/Agarwalrishu13/nanosay) | have anything read out loud |
| 🧲 [nanoPick](https://github.com/Agarwalrishu13/nanopick) | find your files by saying what you remember |
| 🎵 [nanoTune](https://github.com/Agarwalrishu13/nanotune) | your music, one page, no account |
| 🧰 [nanoWrap](https://github.com/Agarwalrishu13/nanowrap) | the best-known programs, with ready-made buttons |
| ⌨️ [nanoShell](https://github.com/Agarwalrishu13/nanoshell) | any program at all, with words instead of flags |
| 🗂 [nanoGit](https://github.com/Agarwalrishu13/nanogit) | your folder, kept safe without learning git |
| 🖥 [nanoDesk](https://github.com/Agarwalrishu13/nanodesk) | every nano-style app you have, one click away |
| 🃏 [nonoForge](https://github.com/Agarwalrishu13/nonoforge) | pick a card, press one button, you have an app |

And underneath them, for people who want to see the gears: [nanollama.c](https://github.com/Agarwalrishu13/nanollama.c) (the C engine), [nanobrain](https://github.com/Agarwalrishu13/nanobrain) (training from scratch), [nanoforge](https://github.com/Agarwalrishu13/nanoforge) (the model studio) and [nanorl](https://github.com/Agarwalrishu13/nanorl) (alignment).

The map of the whole project — what each app is for, and how they fit together — lives in [the nano family](https://github.com/Agarwalrishu13/nano).
## License

MIT © Priyanshu Agarwal
