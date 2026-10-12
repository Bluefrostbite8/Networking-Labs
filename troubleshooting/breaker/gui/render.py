"""Markdown files (tickets, debriefs, READMEs) as HTML inside the GUI, with
relative links pointed at the GUI's own routes."""

from __future__ import annotations

import html
import os
import re
from pathlib import Path

import markdown

from ..lab_model import REPO_ROOT
from ..session import SESSIONS_DIR

ALERT = re.compile(r"<blockquote>\s*<p>\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*", re.I)
LINK = re.compile(r'(src|href)="([^"#][^"]*)"')
DIFF = re.compile(r'<code class="language-diff">(.*?)</code>', re.S)


def url_for_file(target: Path) -> str | None:
    """GUI route for a repo file: session documents open on their session page."""
    try:
        rel = target.resolve().relative_to(REPO_ROOT.resolve())
    except ValueError:
        return None
    sessions = SESSIONS_DIR.resolve()
    if sessions in target.resolve().parents and target.suffix == ".md" and target.parent.parent == sessions:
        view = {"ticket.md": "ticket", "debrief.md": "debrief"}.get(target.name)
        if view:
            return f"/s/{target.parent.name}?view={view}"
    return "/files/" + rel.as_posix()


def _rewrite_links(text: str, base: Path) -> str:
    def repl(m: re.Match) -> str:
        attr, url = m.group(1), m.group(2)
        if re.match(r"^[a-z][a-z0-9+.-]*:", url, re.I) or url.startswith("/"):
            return m.group(0)
        path, _, frag = url.partition("#")
        new = url_for_file(Path(os.path.normpath(base / path)))
        if not new:
            return m.group(0)
        return f'{attr}="{new}{"#" + frag if frag else ""}"'
    return LINK.sub(repl, text)


def _diff(m: re.Match) -> str:
    out = []
    for line in m.group(1).split("\n"):
        raw = html.unescape(line)
        cls = "add" if raw.startswith("+") else "del" if raw.startswith("-") else "ctx"
        out.append(f'<span class="d-{cls}">{line}</span>')
    return '<code class="language-diff">' + "\n".join(out) + "</code>"


def render_markdown(text: str, base: Path) -> str:
    """Markdown to HTML; `base` is the folder the file lives in (for its links)."""
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists"], output_format="html")
    body = ALERT.sub(lambda m: f'<blockquote class="alert alert-{m.group(1).lower()}"><p>', body)
    body = DIFF.sub(_diff, body)
    return _rewrite_links(body, base)


def render_file(path: Path) -> str:
    return render_markdown(path.read_text(encoding="utf-8"), path.parent)
