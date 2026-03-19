#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════╗
║  👁  A R G U S  —  The All-Seeing Eye         ║
║  Real-time filesystem TUI companion           ║
║  for Claude Code                              ║
╚═══════════════════════════════════════════════╝

Usage:
    python argus.py [directory]

    directory: Path to watch (defaults to current directory)
"""

import os
import re
import sys
import json
import time
import queue
import subprocess
import concurrent.futures
from pathlib import Path
from datetime import datetime
from typing import Optional

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, Static, Tree, RichLog
from textual.binding import Binding

from rich.text import Text
from rich.style import Style
from rich.syntax import Syntax

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Constants
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

IGNORE_NAMES = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", ".eggs", ".egg-info", ".DS_Store",
    "Thumbs.db", ".claude", ".idea", ".vscode",
}

TEMP_SUFFIXES = {
    ".swp", ".swo", ".swn", ".swx",      # vim swap
    ".tmp", ".temp", ".bak", ".orig",     # generic temp/backup
    ".part", ".crdownload",               # partial downloads
    ".kate-swp",                          # kate
}

TEMP_PREFIXES = (".#", ".goutputstream-", ".fuse_hidden")

DIR_ICON = "\U0001f4c1"   # 📁
FILE_ICON = "\U0001f4c4"  # 📄

EVENT_ICONS = {
    "created":  ("\u2728", "bright_green"),
    "modified": ("\U0001f4dd", "yellow"),
    "edited":   ("\u270f\ufe0f", "bright_yellow"),
    "deleted":  ("\U0001f5d1\ufe0f", "red"),
    "moved":    ("\U0001f4e6", "cyan"),
}

# (max_age_seconds, color, bold)
RECENCY_TIERS = [
    (3,    "bright_green", True),
    (15,   "green",        True),
    (60,   "yellow",       False),
    (300,  "dark_orange",  False),
    (900,  None,           False),
]

GIT_COLORS = {
    "M":  "yellow",
    "A":  "green",
    "D":  "red",
    "R":  "cyan",
    "C":  "cyan",
    "??": "bright_black",
    "!!": "bright_black",
    "MM": "yellow",
    "AM": "green",
    "UU": "bright_red",
}

LEXER_MAP = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".jsx": "javascript",
    ".json": "json", ".jsonl": "json",
    ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".md": "markdown", ".rst": "rst",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".css": "css", ".scss": "scss", ".less": "css",
    ".html": "html", ".htm": "html", ".xml": "xml", ".svg": "xml",
    ".sql": "sql",
    ".go": "go", ".rs": "rust", ".rb": "ruby",
    ".java": "java", ".c": "c", ".cpp": "cpp",
    ".h": "c", ".hpp": "cpp",
    ".r": "r", ".R": "r",
    ".ini": "ini", ".cfg": "ini",
    ".env": "bash", ".txt": "text", ".csv": "text",
}

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico",
    ".mp3", ".mp4", ".avi", ".mov", ".wav", ".flac", ".ogg",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar", ".xz",
    ".exe", ".dll", ".so", ".dylib", ".o", ".a",
    ".whl", ".egg", ".pyc", ".pyo",
    ".db", ".sqlite", ".parquet", ".arrow",
}

CLAUDE_TOOL_ICONS = {
    "Read":  "\U0001f50d",   # magnifier
    "Grep":  "\U0001f50d",
    "Glob":  "\U0001f50d",
    "Edit":  "\u270f\ufe0f", # pencil
    "Write": "\u270f\ufe0f",
    "Bash":  "\U0001f4bb",   # laptop
    "Agent": "\U0001f916",   # robot
}
CLAUDE_DEFAULT_ICON = "\U0001f916"
CLAUDE_DISPLAY_SECONDS = 8   # icons persist ~8s after Claude's last action
GHOST_DURATION = 10          # deleted files stay visible (struck through) for 10s

TICK_INTERVAL = 0.5
COALESCE_WINDOW = 1.5   # seconds to wait before flushing events
MAINTENANCE_INTERVAL = 5.0
ACTIVITY_MAX_LINES = 500
PREVIEW_MAX_BYTES = 1_000_000   # skip files larger than 1MB
PREVIEW_CHUNK_LINES = 50        # write syntax in chunks for smooth scrolling
SEEN_TOOL_IDS_MAX = 5000        # evict oldest tool IDs to prevent unbounded growth
SESSION_RECHECK_INTERVAL = 30   # seconds between session directory re-checks


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _is_temp_file(name: str) -> bool:
    """Check if a filename looks like a temporary/transient file."""
    if name.endswith("~"):
        return True
    if name.startswith("#") and name.endswith("#"):
        return True
    if name == "4913":  # vim existence check
        return True
    # Claude Code atomic writes: file.py.tmp.28752.1773786165833
    if re.search(r'\.tmp\.\d+\.\d+$', name):
        return True
    for prefix in TEMP_PREFIXES:
        if name.startswith(prefix):
            return True
    suffix = Path(name).suffix.lower()
    return suffix in TEMP_SUFFIXES


def _human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _get_git_status(root: Path) -> dict[str, str]:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "-u"],
            capture_output=True, text=True,
            cwd=root, timeout=5,
        )
        statuses = {}
        for line in result.stdout.strip().splitlines():
            if len(line) >= 4:
                status = line[:2].strip()
                filepath = line[3:].strip()
                if " -> " in filepath:
                    filepath = filepath.split(" -> ")[1]
                statuses[filepath] = status
        return statuses
    except Exception:
        return {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Claude Code Transcript Tailer
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _find_session_dir(watch_path: Path) -> Optional[Path]:
    """Find the Claude Code project transcript directory for a watched path."""
    # Claude Code mangles paths: /home/rich/foo -> -home-rich-foo
    mangled = str(watch_path).replace("/", "-")
    claude_dir = Path.home() / ".claude" / "projects" / mangled
    if claude_dir.is_dir():
        return claude_dir
    return None


class TranscriptTailer:
    """Tails Claude Code transcript JSONL files for real-time tool actions."""

    def __init__(self, session_dir: Path):
        self.session_dir = session_dir
        self._offsets: dict[str, int] = {}
        self._seen_tool_ids: list[str] = []  # dedup across progress/final entries
        self._seen_tool_set: set[str] = set()
        # Start from current end of all active files (skip history)
        self._discover_files(initial=True)

    def _discover_files(self, initial: bool = False):
        """Find transcript .jsonl files and initialise read offsets."""
        cutoff = time.time() - 300  # only files active in last 5 min
        for jsonl in self.session_dir.rglob("*.jsonl"):
            path_str = str(jsonl)
            if path_str in self._offsets:
                continue
            try:
                st = jsonl.stat()
                # On initial load, pick up all recent files regardless of age
                # On subsequent discovery, only pick up new files
                if initial or st.st_mtime > cutoff:
                    self._offsets[path_str] = st.st_size
            except OSError:
                pass

    def poll(self) -> list[tuple[str, str]]:
        """Read new lines from all transcripts. Returns [(tool_name, file_path), ...]."""
        # Pick up any new subagent files created mid-session
        self._discover_files()

        results: list[tuple[str, str]] = []
        for path_str, offset in list(self._offsets.items()):
            try:
                p = Path(path_str)
                current_size = p.stat().st_size
                if current_size <= offset:
                    continue
                # Handle file truncation (shouldn't happen, but be safe)
                if current_size < offset:
                    self._offsets[path_str] = 0
                    offset = 0

                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(offset)
                    new_data = f.read()
                    self._offsets[path_str] = f.tell()

                for line in new_data.strip().splitlines():
                    try:
                        entry = json.loads(line)
                        results.extend(self._extract_actions(entry))
                    except (json.JSONDecodeError, KeyError):
                        continue
            except Exception:
                continue

        return results

    def _extract_actions(self, entry: dict) -> list[tuple[str, str]]:
        """Extract (tool_name, file_path) pairs from a transcript entry."""
        actions: list[tuple[str, str]] = []
        msg = entry.get("message", {})
        content = msg.get("content", [])
        if not isinstance(content, list):
            return actions

        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue

            # Deduplicate: progress entries replay the same tool_use block
            tool_id = block.get("id", "")
            if tool_id and tool_id in self._seen_tool_set:
                continue
            if tool_id:
                self._seen_tool_ids.append(tool_id)
                self._seen_tool_set.add(tool_id)
                # Evict oldest entries to prevent unbounded growth
                if len(self._seen_tool_ids) > SEEN_TOOL_IDS_MAX:
                    evict = self._seen_tool_ids[:1000]
                    self._seen_tool_ids = self._seen_tool_ids[1000:]
                    self._seen_tool_set -= set(evict)

            tool_name = block.get("name", "")
            tool_input = block.get("input", {})
            if not isinstance(tool_input, dict):
                continue

            if tool_name in ("Read", "Edit", "Write"):
                fp = tool_input.get("file_path")
                if fp:
                    actions.append((tool_name, fp))
            elif tool_name in ("Grep", "Glob"):
                p = tool_input.get("path")
                if p:
                    actions.append((tool_name, p))
            elif tool_name == "Bash":
                command = tool_input.get("command", "")
                for m in re.finditer(r'(?:^|\s)(/[^\s;|&>]+)', command):
                    p = m.group(1).strip("'\"")
                    if p != "/dev/null":
                        actions.append(("Bash", p))

        return actions


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Filesystem Watcher
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ArgusHandler(FileSystemEventHandler):
    """Captures filesystem events into a thread-safe queue."""

    def __init__(self, event_queue: queue.Queue, watch_path: Path):
        self.event_queue = event_queue
        self.watch_path = watch_path

    def _should_ignore(self, path: str) -> bool:
        p = Path(path)
        if any(part in IGNORE_NAMES for part in p.parts):
            return True
        return _is_temp_file(p.name)

    def on_created(self, event):
        if not self._should_ignore(event.src_path):
            self.event_queue.put((event.src_path, "created", event.is_directory))

    def on_modified(self, event):
        # Skip directory modifications — they fire every time content changes inside
        if event.is_directory:
            return
        if not self._should_ignore(event.src_path):
            self.event_queue.put((event.src_path, "modified", event.is_directory))

    def on_deleted(self, event):
        if not self._should_ignore(event.src_path):
            self.event_queue.put((event.src_path, "deleted", event.is_directory))

    def on_moved(self, event):
        # A move-to is almost always an atomic write (temp renamed to actual).
        # Emit a single "edited" on the destination — no noise from the source.
        if not self._should_ignore(event.dest_path):
            self.event_queue.put((event.dest_path, "edited", event.is_directory))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Widgets
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class StatsBar(Static):
    """Displays project statistics."""

    def __init__(self, watch_path: Path, **kwargs):
        super().__init__(**kwargs)
        self.watch_path = watch_path

    def refresh_stats(
        self,
        file_count: int,
        dir_count: int,
        event_count: int,
        active_count: int,
    ):
        branch = self._get_git_branch()
        parts = [
            f"\U0001f4c4 {file_count} files",
            f"\U0001f4c1 {dir_count} dirs",
            f"\u26a1 {event_count} events",
            f"\U0001f525 {active_count} active",
        ]
        if branch:
            parts.append(f"\U0001f33f {branch}")
        self.update(" \u2502 ".join(parts))

    def _get_git_branch(self) -> Optional[str]:
        try:
            result = subprocess.run(
                ["git", "branch", "--show-current"],
                capture_output=True, text=True,
                cwd=self.watch_path, timeout=2,
            )
            branch = result.stdout.strip()
            return branch if branch else None
        except Exception:
            return None


class Legend(Static):
    """Displays a color coding legend."""

    def on_mount(self):
        text = Text()
        # Line 1 — Recency
        text.append("  Recency  ", style="bold dim")
        text.append("\u25cf", style=Style(color="bright_green", bold=True))
        text.append(" <3s  ", style="dim")
        text.append("\u25cf", style=Style(color="green", bold=True))
        text.append(" <15s  ", style="dim")
        text.append("\u25cf", style=Style(color="yellow"))
        text.append(" <1m  ", style="dim")
        text.append("\u25cf", style=Style(color="dark_orange"))
        text.append(" <5m", style="dim")
        text.append("\n")
        # Line 2 — Git
        text.append("  Git      ", style="bold dim")
        text.append("[M]", style=Style(color="yellow", dim=True))
        text.append(" modified  ", style="dim")
        text.append("[??]", style=Style(color="bright_black", dim=True))
        text.append(" untracked  ", style="dim")
        text.append("[A]", style=Style(color="green", dim=True))
        text.append(" staged", style="dim")
        text.append("\n")
        # Line 3 — Claude
        text.append("  Claude   ", style="bold dim")
        text.append("\U0001f916\U0001f50d", style=Style(color="bright_magenta"))
        text.append(" reading  ", style="dim")
        text.append("\U0001f916\u270f\ufe0f", style=Style(color="bright_magenta"))
        text.append(" writing  ", style="dim")
        text.append("\U0001f916\U0001f4bb", style=Style(color="bright_magenta"))
        text.append(" shell", style="dim")
        self.update(text)


class ActivityFeed(RichLog):
    """Shows real-time filesystem events."""

    def log_event(self, event_type: str, rel_path: str):
        icon, color = EVENT_ICONS.get(event_type, ("\u2753", "white"))
        now = datetime.now().strftime("%H:%M:%S")

        text = Text()
        text.append(f" {now}  ", style="dim")
        text.append(f"{icon}  ")
        text.append(f"{event_type:<10}", style=Style(color=color, bold=True))
        text.append(str(rel_path), style=Style(color=color))
        self.write(text)

    def log_claude_action(self, tool_name: str, rel_path: str):
        icon = CLAUDE_TOOL_ICONS.get(tool_name, CLAUDE_DEFAULT_ICON)
        now = datetime.now().strftime("%H:%M:%S")

        # Shorten absolute paths (files outside the project) to just the name
        display_path = rel_path
        if rel_path.startswith("/"):
            display_path = Path(rel_path).name

        text = Text()
        text.append(f" {now}  ", style="dim")
        text.append(f"\U0001f916 {icon} ")
        text.append(f"{tool_name:<10}", style=Style(color="bright_magenta", bold=True))
        text.append(display_path, style=Style(color="bright_magenta"))
        self.write(text)


class FilePreview(RichLog):
    """Shows a syntax-highlighted preview of the selected file."""

    def show_file(self, path: Path):
        self.clear()

        if not path.exists():
            self.write(Text("  File no longer exists", style="dim italic"))
            return

        if path.suffix.lower() in BINARY_EXTENSIONS:
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            self.write(Text(
                f"  Binary file ({_human_size(size)})",
                style="dim italic",
            ))
            return

        try:
            size = path.stat().st_size
            if size > PREVIEW_MAX_BYTES:
                self.write(Text(
                    f"  File too large to preview ({_human_size(size)})",
                    style="dim italic",
                ))
                return
            content = path.read_text(errors="replace")
        except Exception as e:
            self.write(Text(f"  Cannot read: {e}", style="red"))
            return

        if not content.strip():
            self.write(Text("  (empty file)", style="dim italic"))
            return

        lines = content.splitlines()
        lexer = LEXER_MAP.get(path.suffix.lower(), "text")

        # Write in chunks so RichLog can scroll through large files properly.
        # A single huge Syntax renderable breaks RichLog's scroll calculation.
        for i in range(0, len(lines), PREVIEW_CHUNK_LINES):
            chunk = "\n".join(lines[i : i + PREVIEW_CHUNK_LINES])
            try:
                syntax = Syntax(
                    chunk,
                    lexer=lexer,
                    theme="monokai",
                    line_numbers=True,
                    word_wrap=True,
                    start_line=i + 1,
                )
                self.write(syntax)
            except Exception:
                self.write(Text(chunk))

    def show_placeholder(self):
        self.clear()
        self.write(Text(
            "  Select a file in the tree to preview",
            style="dim italic",
        ))


class ProjectTree(Tree[dict]):
    """Directory tree with recency-based color coding and git status."""

    def __init__(self, watch_path: Path, **kwargs):
        super().__init__(
            f"\U0001f4c2 {watch_path.name}",
            data={"path": watch_path, "is_dir": True},
            **kwargs,
        )
        self.watch_path = watch_path
        self.file_events: dict[str, tuple[str, float]] = {}
        self.git_statuses: dict[str, str] = {}
        self.claude_active: dict[str, float] = {}   # path -> timestamp
        self.claude_tools: dict[str, str] = {}       # path -> tool name
        self.ghost_files: dict[str, float] = {}      # deleted path -> timestamp
        self._auto_expanded: set[str] = set()        # dirs we opened for Claude
        self._node_index: dict[str, object] = {}     # path -> TreeNode
        self.file_count = 0
        self.dir_count = 0

    def on_mount(self):
        self.root.expand()
        self.rebuild()

    def rebuild(self) -> tuple[int, int]:
        """Rebuild tree from filesystem, preserving expansion state."""
        expanded = self._get_expanded_paths()
        self.root.remove_children()
        self.file_count = 0
        self.dir_count = 0
        self._build_subtree(self.root, self.watch_path)
        self._restore_expanded(expanded)
        self._build_node_index()
        return self.file_count, self.dir_count

    def _build_subtree(self, parent_node, dir_path: Path):
        try:
            entries = sorted(
                dir_path.iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
        except (PermissionError, OSError):
            return

        for entry in entries:
            if self._should_ignore(entry):
                continue
            try:
                rel = str(entry.relative_to(self.watch_path))
            except ValueError:
                rel = str(entry)
            if entry.is_dir():
                self.dir_count += 1
                node = parent_node.add(
                    entry.name,
                    data={"path": entry, "is_dir": True, "rel": rel},
                )
                self._build_subtree(node, entry)
            else:
                self.file_count += 1
                parent_node.add_leaf(
                    entry.name,
                    data={"path": entry, "is_dir": False, "rel": rel},
                )

        # Add ghost nodes for recently deleted files in this directory
        dir_str = str(dir_path)
        for ghost_path, ts in self.ghost_files.items():
            ghost = Path(ghost_path)
            if str(ghost.parent) == dir_str and not ghost.exists():
                parent_node.add_leaf(
                    ghost.name,
                    data={"path": ghost, "is_dir": False, "is_ghost": True},
                )

    def render_label(self, node, base_style, style):
        data = node.data
        if data is None:
            return Text(str(node.label), style=style)

        path_obj: Path = data["path"]
        is_dir: bool = data["is_dir"]
        is_ghost: bool = data.get("is_ghost", False)
        label = Text()

        # Ghost nodes: strikethrough + dim red, then return early
        if is_ghost:
            age = time.time() - self.ghost_files.get(str(path_obj), 0)
            if age < 3:
                ghost_style = Style(color="bright_red", bold=True, strike=True)
            elif age < 7:
                ghost_style = Style(color="red", strike=True)
            else:
                ghost_style = Style(color="bright_black", dim=True, strike=True)
            label.append("\U0001f5d1\ufe0f ", style=ghost_style)
            label.append(str(node.label), style=ghost_style)
            return label

        if is_dir:
            icon = DIR_ICON + " "
        else:
            icon = FILE_ICON + " "

        path_str = str(path_obj)
        recency = self._get_recency(path_str)

        if recency:
            color, bold = recency
            indicator_style = Style(color=color, bold=True)
            text_style = Style(color=color, bold=bold)
            label.append("\u25cf ", style=indicator_style)
            label.append(icon)
            label.append(str(node.label), style=text_style)
        else:
            label.append(icon)
            label.append(str(node.label), style=style)

        rel = data.get("rel") or str(path_obj)
        git_st = self.git_statuses.get(rel)
        if git_st:
            git_color = GIT_COLORS.get(git_st, "white")
            label.append(f" [{git_st}]", style=Style(color=git_color, dim=True))

        # Claude activity indicator — on while active, gone when done
        if path_str in self.claude_active:
            tool = self.claude_tools.get(path_str, "")
            ci = CLAUDE_TOOL_ICONS.get(tool, CLAUDE_DEFAULT_ICON)
            label.append(f" \U0001f916{ci}", style=Style(color="bright_magenta", bold=True))

        return label

    def _get_recency(self, path: str) -> Optional[tuple[str, bool]]:
        if path not in self.file_events:
            return None
        _, ts = self.file_events[path]
        age = time.time() - ts
        for max_age, color, bold in RECENCY_TIERS:
            if age < max_age:
                if color:
                    return (color, bold)
                return None
        return None

    def _should_ignore(self, path: Path) -> bool:
        name = path.name
        if name in IGNORE_NAMES:
            return True
        if name.endswith(".pyc") or name.endswith(".pyo"):
            return True
        return _is_temp_file(name)

    def _get_expanded_paths(self) -> set[str]:
        expanded = set()

        def walk(node):
            if node.is_expanded and node.data:
                expanded.add(str(node.data["path"]))
            for child in node.children:
                walk(child)

        walk(self.root)
        return expanded

    def _restore_expanded(self, expanded: set[str]):
        def walk(node):
            if node.data and str(node.data["path"]) in expanded:
                node.expand()
            for child in node.children:
                walk(child)

        walk(self.root)

    def _build_node_index(self):
        """Map path strings to tree nodes for fast lookup."""
        self._node_index = {}

        def walk(node):
            if node.data:
                self._node_index[str(node.data["path"])] = node
            for child in node.children:
                walk(child)

        walk(self.root)

    def auto_expand_to(self, file_path: str):
        """Expand ancestor directories so a file Claude is touching is visible."""
        p = Path(file_path).parent
        while True:
            ps = str(p)
            node = self._node_index.get(ps)
            if node is not None and not node.is_expanded:
                node.expand()
                self._auto_expanded.add(ps)
            if p == self.watch_path or p == p.parent:
                break
            p = p.parent

    def auto_collapse_stale(self):
        """Collapse dirs we auto-expanded once no Claude activity and no recent file events."""
        now = time.time()
        recency_cutoff = 300  # 5 minutes
        to_remove = []
        for dir_path in list(self._auto_expanded):
            prefix = dir_path + os.sep
            has_claude = any(
                fp.startswith(prefix)
                for fp in self.claude_active
            )
            has_recent_files = any(
                fp.startswith(prefix)
                and now - ts < recency_cutoff
                for fp, (_, ts) in self.file_events.items()
            )
            if not has_claude and not has_recent_files:
                to_remove.append(dir_path)
        for dir_path in to_remove:
            self._auto_expanded.discard(dir_path)
            node = self._node_index.get(dir_path)
            if node is not None and node.is_expanded:
                node.collapse()



# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Main Application
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ArgusApp(App):
    """The All-Seeing Eye — real-time filesystem TUI."""

    CSS_PATH = "argus.tcss"
    TITLE = "\U0001f441  ARGUS"
    SUB_TITLE = "The All-Seeing Eye"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("c", "clear_feed", "Clear"),
    ]

    def __init__(self, watch_path: Path, **kwargs):
        super().__init__(**kwargs)
        self.watch_path = watch_path.resolve()
        self.event_queue: queue.Queue = queue.Queue()
        self.total_events = 0
        self._observer: Optional[Observer] = None
        self._pending: dict[str, tuple[str, float, bool]] = {}
        self._previewed_path: Optional[Path] = None
        self._tailer: Optional[TranscriptTailer] = None
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self._git_future: Optional[concurrent.futures.Future] = None
        self._last_session_check = 0.0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            with Vertical(id="tree-panel"):
                yield ProjectTree(self.watch_path, id="project-tree")
            with Vertical(id="right-panel"):
                yield StatsBar(self.watch_path, id="stats-bar")
                yield Legend(id="legend")
                yield FilePreview(id="file-preview", auto_scroll=False)
                yield ActivityFeed(
                    id="activity-feed",
                    max_lines=ACTIVITY_MAX_LINES,
                    auto_scroll=True,
                )
        yield Footer()

    def on_mount(self):
        self.query_one("#tree-panel").border_title = "\U0001f4c2 Project"
        self.query_one("#stats-bar").border_title = "\U0001f4ca Stats"
        self.query_one("#file-preview").border_title = "\U0001f441 Preview"
        self.query_one("#activity-feed").border_title = "\U0001f4e1 Activity"

        tree = self.query_one("#project-tree", ProjectTree)
        tree.git_statuses = _get_git_status(self.watch_path)
        self._update_stats()

        preview = self.query_one("#file-preview", FilePreview)
        preview.show_placeholder()

        self._start_watcher()
        self._start_transcript_tailer()

        self.set_interval(TICK_INTERVAL, self._tick)
        self.set_interval(MAINTENANCE_INTERVAL, self._maintenance)

        feed = self.query_one("#activity-feed", ActivityFeed)
        welcome = Text()
        welcome.append(
            " \U0001f441  Argus is watching ",
            style=Style(bold=True, color="bright_cyan"),
        )
        welcome.append(str(self.watch_path), style=Style(color="cyan", dim=True))
        feed.write(welcome)

    def _start_watcher(self):
        handler = ArgusHandler(self.event_queue, self.watch_path)
        self._observer = Observer()
        self._observer.schedule(handler, str(self.watch_path), recursive=True)
        self._observer.daemon = True
        self._observer.start()

    def _start_transcript_tailer(self):
        session_dir = _find_session_dir(self.watch_path)
        if session_dir:
            self._tailer = TranscriptTailer(session_dir)
            feed = self.query_one("#activity-feed", ActivityFeed)
            msg = Text()
            msg.append(
                " \U0001f916 Tracking Claude Code session ",
                style=Style(bold=True, color="bright_magenta"),
            )
            msg.append(str(session_dir.name)[:12] + "...", style=Style(color="magenta", dim=True))
            feed.write(msg)

    # ── Two-timer architecture ─────────────────────────

    def _tick(self):
        """Unified tick: drain events, poll Claude, prune icons, flush feed, refresh."""
        tree = self.query_one("#project-tree", ProjectTree)
        feed = self.query_one("#activity-feed", ActivityFeed)
        now = time.time()
        needs_rebuild = False
        needs_refresh = False
        touched_paths: set[str] = set()

        # ── Step 1: Drain filesystem event queue ──
        while True:
            try:
                src_path, event_type, is_dir = self.event_queue.get_nowait()
            except queue.Empty:
                break

            tree.file_events[src_path] = (event_type, now)
            touched_paths.add(src_path)
            needs_refresh = True

            if event_type in ("created", "deleted"):
                needs_rebuild = True
            elif event_type == "edited" and src_path not in tree._node_index:
                needs_rebuild = True  # atomic write created a new file

            # Track ghost files for deleted items
            if event_type == "deleted" and not is_dir:
                tree.ghost_files[src_path] = now
                needs_rebuild = True
            elif event_type in ("created", "edited") and src_path in tree.ghost_files:
                del tree.ghost_files[src_path]
                needs_rebuild = True

            # Coalesce for the activity feed
            if src_path in self._pending:
                prev_type, _, _ = self._pending[src_path]
                if event_type == "deleted" and prev_type == "created":
                    del self._pending[src_path]
                    continue
                if event_type == "modified" and prev_type == "created":
                    self._pending[src_path] = ("created", now, is_dir)
                    continue
                if event_type == "created" and prev_type == "deleted":
                    self._pending[src_path] = ("edited", now, is_dir)
                    continue
                if event_type in ("modified", "created") and prev_type == "edited":
                    self._pending[src_path] = ("edited", now, is_dir)
                    continue
            self._pending[src_path] = (event_type, now, is_dir)

        # ── Step 2: Poll Claude transcript ──
        if self._tailer:
            actions = self._tailer.poll()
            if actions:
                for tool_name, file_path in actions:
                    tree.claude_active[file_path] = now
                    tree.claude_tools[file_path] = tool_name
                    tree.auto_expand_to(file_path)
                    try:
                        rel = str(Path(file_path).relative_to(self.watch_path))
                    except ValueError:
                        rel = file_path
                    feed.log_claude_action(tool_name, rel)
                needs_refresh = True

        # ── Step 3: Prune stale Claude icons ──
        stale = [k for k, ts in tree.claude_active.items()
                 if now - ts > CLAUDE_DISPLAY_SECONDS]
        if stale:
            for k in stale:
                del tree.claude_active[k]
                tree.claude_tools.pop(k, None)
            needs_refresh = True

        # ── Step 4: Flush coalesced activity feed entries ──
        flushed = []
        for path, (evt, ts, is_d) in list(self._pending.items()):
            if now - ts >= COALESCE_WINDOW:
                flushed.append(path)
                self.total_events += 1
                try:
                    rel = str(Path(path).relative_to(self.watch_path))
                except ValueError:
                    rel = path
                if is_d:
                    rel += "/"
                feed.log_event(evt, rel)
        for path in flushed:
            del self._pending[path]

        # ── Step 5: Auto-refresh preview if touched ──
        if self._previewed_path and str(self._previewed_path) in touched_paths:
            preview = self.query_one("#file-preview", FilePreview)
            preview.show_file(self._previewed_path)

        # ── Step 6: Collect async git results if ready ──
        if self._git_future and self._git_future.done():
            try:
                tree.git_statuses = self._git_future.result()
            except Exception:
                pass
            self._git_future = None
            needs_refresh = True

        # ── Step 7: Structural change → rebuild; visual change → refresh only ──
        if needs_rebuild:
            self._request_git_refresh()
            tree.rebuild()
            self._update_stats()
        elif needs_refresh or tree.file_events or tree.claude_active or tree.ghost_files:
            tree._updates += 1
            tree.refresh()

    def _request_git_refresh(self):
        """Submit git status to background thread (non-blocking)."""
        if self._git_future is None or self._git_future.done():
            self._git_future = self._executor.submit(
                _get_git_status, self.watch_path
            )

    def _maintenance(self):
        """Periodic housekeeping: git, prune old events, prune ghosts, auto-collapse."""
        tree = self.query_one("#project-tree", ProjectTree)
        now = time.time()

        # Refresh git status in background (non-blocking)
        self._request_git_refresh()

        # Re-check for Claude session (handles restarts)
        if now - self._last_session_check > SESSION_RECHECK_INTERVAL:
            self._last_session_check = now
            session_dir = _find_session_dir(self.watch_path)
            if session_dir:
                if self._tailer is None or self._tailer.session_dir != session_dir:
                    self._tailer = TranscriptTailer(session_dir)
                    feed = self.query_one("#activity-feed", ActivityFeed)
                    msg = Text()
                    msg.append(
                        " \U0001f916 Tracking Claude Code session ",
                        style=Style(bold=True, color="bright_magenta"),
                    )
                    msg.append(str(session_dir.name)[:12] + "...", style=Style(color="magenta", dim=True))
                    feed.write(msg)

        # Prune old file events (> 15 min)
        cutoff = now - 900
        stale = [k for k, (_, ts) in tree.file_events.items() if ts < cutoff]
        for k in stale:
            del tree.file_events[k]

        # Prune expired ghosts — batch into single rebuild
        ghost_cutoff = now - GHOST_DURATION
        stale_ghosts = [k for k, ts in tree.ghost_files.items() if ts < ghost_cutoff]
        for k in stale_ghosts:
            del tree.ghost_files[k]
        if stale_ghosts:
            tree.rebuild()

        # Auto-collapse stale directories
        tree.auto_collapse_stale()

        # Update stats
        self._update_stats()

    def _update_stats(self):
        tree = self.query_one("#project-tree", ProjectTree)
        stats = self.query_one("#stats-bar", StatsBar)
        now = time.time()
        active = sum(
            1 for _, (_, ts) in tree.file_events.items() if now - ts < 60
        )
        stats.refresh_stats(
            file_count=tree.file_count,
            dir_count=tree.dir_count,
            event_count=self.total_events,
            active_count=active,
        )

    # ── Tree selection → file preview ────────────────

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        data = event.node.data
        if not data:
            return
        path: Path = data["path"]
        if data["is_dir"]:
            return
        self._previewed_path = path
        preview = self.query_one("#file-preview", FilePreview)
        preview.show_file(path)
        self.query_one("#file-preview").border_title = (
            f"\U0001f441 {path.name}"
        )

    # ── Actions ──────────────────────────────────────

    def action_clear_feed(self):
        feed = self.query_one("#activity-feed", ActivityFeed)
        feed.clear()
        feed.write(Text(
            " \U0001f9f9 Feed cleared",
            style=Style(color="bright_cyan"),
        ))

    def on_unmount(self):
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=2)
        self._executor.shutdown(wait=False)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Entry point
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    if len(sys.argv) > 1:
        target = sys.argv[1]
        if target in ("-h", "--help"):
            print(__doc__)
            sys.exit(0)
        path = Path(target).resolve()
    else:
        path = Path.cwd()

    if not path.is_dir():
        print(f"Error: '{path}' is not a directory", file=sys.stderr)
        sys.exit(1)

    app = ArgusApp(watch_path=path)
    app.run()


if __name__ == "__main__":
    main()
