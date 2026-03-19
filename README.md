# Argus — The All-Seeing Eye

![demo](demo.gif)

**A real-time TUI that watches your project so you don't have to.**

Ever been deep in a Claude Code session, vibing in the CLI, only to resurface and wonder *"wait... how big is this thing now?"* Yeah. Same.

VS Code and friends are great, but sometimes you just want to **see** what's happening in your project without opening a whole IDE. Argus sits in a terminal next to your workflow and quietly watches everything — file changes, git status, even what Claude is up to — all in a clean split-pane view with pretty colours.

No config. No plugins. No 47 extensions to install. Just run it and go.

![Python](https://img.shields.io/badge/Python-3.12+-blue) ![License](https://img.shields.io/badge/License-MIT-green) ![Vibes](https://img.shields.io/badge/Vibes-Immaculate-ff69b4)

---

## Why?

I live in the terminal. Claude Code is my IDE now. But working purely in the CLI meant I kept losing track of what was actually going on in my projects — files multiplying, directories sprawling, the classic "oh no it's bloated" moment.

I tried going back to VS Code for project overview but it felt like swatting a fly with a sledgehammer. I just wanted a window that shows me *what's changing* and *how big things are getting*.

So I built Argus. Named after the hundred-eyed giant from Greek mythology, because this thing sees **everything**.

## What It Does

- **Live file tree** with recency-based colour coding — recently changed files glow green, then fade through yellow to orange as they cool off
- **Activity feed** streaming every create, modify, delete, and move as it happens
- **Git status** baked right into the tree — `[M]` modified, `[A]` staged, `[??]` untracked, all colour-coded
- **Claude Code awareness** — watches Claude's transcript and shows you which tools it's using and which files it's touching (yes, it watches the watcher)
- **File preview** with syntax highlighting for 25+ languages — click a file, see the code
- **Ghost files** — deleted files stick around struck-through for a few seconds so you catch what just vanished
- **Project stats** — file count, directory count, active files, current branch, all at a glance

## Quick Start

```bash
git clone https://github.com/Rich478/Argus.git
cd Argus
./run.sh                    # watches current directory
./run.sh /path/to/project   # watches somewhere else
```

The `run.sh` script handles venv creation and dependency installation automatically. Or do it yourself:

```bash
pip install -r requirements.txt
python3 argus.py [directory]
```

**Requirements:** Python 3.12+

## Controls

| Key | Action |
|-----|--------|
| `q` | Quit |
| `c` | Clear activity feed |
| Click a file | Preview with syntax highlighting |

That's it. Told you it was simple.

## How It Works

Argus uses [Textual](https://github.com/Textualize/textual) for the TUI and [Watchdog](https://github.com/gorakhargosh/watchdog) for filesystem monitoring. The whole thing is one Python file because sometimes that's all you need.

It runs on a two-timer system:
- **Fast tick (0.5s)** — drains file events, polls Claude's transcript, updates the feed
- **Slow tick (5s)** — refreshes git status, prunes stale data, tidies the tree

Events get coalesced over a 1.5s window so you don't get spammed when a build tool touches 50 files at once. It also intelligently ignores the usual noise — `.git`, `__pycache__`, `node_modules`, `.venv`, and friends.

## The Claude Code Thing

This is the bit I'm most chuffed about. Argus tails Claude Code's session transcripts in real-time and shows you what tools Claude is using:

- `🔍` Reading/searching files
- `✏️` Writing/editing
- `💻` Running bash commands
- `🤖` Spawning subagents

It even auto-expands directories in the tree when Claude starts poking around in them. It's like having a live debugger for your AI pair programmer.

## Built With

- [Textual](https://github.com/Textualize/textual) — TUI framework
- [Watchdog](https://github.com/gorakhargosh/watchdog) — Filesystem events
- [Rich](https://github.com/Textualize/rich) — Terminal rendering & syntax highlighting
- Caffeine and mild frustration with IDEs

## License

MIT — do whatever you want with it.
