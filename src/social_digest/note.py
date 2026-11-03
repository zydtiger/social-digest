"""Export a run's cached digest into its own weekly Obsidian note."""

import datetime as dt
import json
import os
import re
import tempfile
from pathlib import Path

from .config import Paths
from .store import fingerprint, read, write, write_text


def covered_week(until: dt.date) -> str:
    year, week, _ = (until - dt.timedelta(days=1)).isocalendar()
    return f"{year}-W{week:02d}"


def note_name(until: dt.date) -> str:
    return f"{covered_week(until)} Social Digest"


def section_body(body: str) -> str:
    """Keep the filename as the title and start content with a level-two section."""
    lines = []
    fence = None
    for line in body.strip().splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            if fence is None:
                fence = marker[1][0]
            elif marker[1][0] == fence:
                fence = None
        if fence is None and line.startswith("# "):
            line = "#" + line
        lines.append(line)
    normalized = "\n".join(lines)
    if not normalized.startswith("## "):
        normalized = "## Developments\n\n" + normalized
    return normalized


def cell(value) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def render_note(until: dt.date, body: str, cards: list[dict], parent_note: Path) -> str:
    lines = [
        "---",
        "up: " + json.dumps(f"[[{parent_note.stem}]]", ensure_ascii=False),
        "tags:",
        "  - review/social-digest",
        f"week: {covered_week(until)}",
        "reported: " + json.dumps(sorted(c["id"] for c in cards)),
        "---",
        "",
        section_body(body),
        "",
        "## Sources and evidence",
        "",
        "| Item | Author | Published | Source ID | Verified excerpts |",
        "| --- | --- | --- | --- | --- |",
    ]
    for card in cards:
        title = cell(card["title"]).replace("[", "\\[").replace("]", "\\]")
        lines.append(
            f"| [{title}]({card['url']}) | {cell(card.get('author', ''))} | "
            f"{cell(card.get('published_at', ''))} | `{cell(card['id'])}` | "
            f"{len(card['claims'])} |"
        )
    return "\n".join(lines) + "\n"


def create_note(path: Path, content: str) -> None:
    """Publish complete content without overwriting another writer's note."""
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.link(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def write_note(directory: Path, paths: Paths, until: dt.date, body: str, cards: list[dict]) -> Path:
    checkpoint = directory / "write.json"
    state = read(checkpoint)
    if state and (
        Path(state["note"]).parent != paths.note_dir
        or Path(state["parent_note"]) != paths.parent_note
    ):
        raise ValueError(
            "note output paths differ from write checkpoint; use original output paths"
        )
    if state and state["status"] == "complete":
        # The run owns its recorded output. Changes made in Obsidian remain intact,
        # and its output target stays fixed after the first write attempt.
        return Path(state["note"])
    parent_note = Path(state["parent_note"]) if state else paths.parent_note
    if not parent_note.is_file():
        raise FileNotFoundError(f"parent note not found: {parent_note}")
    content = render_note(until, body, cards, parent_note)
    if not state:
        paths.note_dir.mkdir(parents=True, exist_ok=True)
        name = note_name(until)
        path = paths.note_dir / f"{name}.md"
        suffix = 2
        while path.exists():
            path = paths.note_dir / f"{name} {suffix}.md"
            suffix += 1
        state = {
            "status": "pending",
            "note": str(path),
            "parent_note": str(parent_note),
            "content_sha256": fingerprint(content),
            "created": False,
        }
        write(checkpoint, state)
    path = Path(state["note"])
    if not state["created"]:
        if fingerprint(content) != state["content_sha256"]:
            raise ValueError("pending note content changed; refusing to replace its reservation")
        if path.exists():
            if fingerprint(path.read_text()) != state["content_sha256"]:
                raise ValueError("reserved note differs; refusing to overwrite unrelated content")
        else:
            create_note(path, content)
        state["created"] = True
        write(checkpoint, state)
    link = f"[[{path.stem}]]"
    parent = parent_note.read_text()
    existing_link = re.compile(r"\[\[" + re.escape(path.stem) + r"(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
    if not existing_link.search(parent):
        write_text(parent_note, f"{parent.rstrip()}\n- {link}\n")
    state["status"] = "complete"
    write(checkpoint, state)
    return path
