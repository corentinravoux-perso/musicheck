"""Copy, move or link files into folders named after their properties."""

from __future__ import annotations

import os
import re
import shutil
import string
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Move", "plan", "apply", "template_fields"]

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class Move:
    src: Path
    dst: Path
    skip: str = ""  # reason the move is skipped, empty when it will happen


def template_fields(template: str) -> list[str]:
    return [name for _, name, _, _ in string.Formatter().parse(template) if name]


def _safe(value) -> str:
    if value is None or value == "":
        return "unknown"
    if isinstance(value, bool):
        return "lossless" if value else "lossy"
    if isinstance(value, float):
        value = f"{value:g}"
    text = _UNSAFE.sub("_", str(value)).strip(" .")
    return text or "unknown"


class _Values(dict):
    def __missing__(self, key):
        raise KeyError(key)


def plan(
    records: list[dict], dest: str | Path, template: str, keep_tree: bool = False
) -> list[Move]:
    """Destination of each record: ``dest/<template>/<file>``.

    ``template`` uses record fields, e.g. ``"{verdict}/{real}"`` or
    ``"{format}/{artist}/{album}"``. With ``keep_tree`` the path relative to the
    scanned root is kept under the template folder instead of the bare name.
    """
    from .report import FIELDS

    for name in template_fields(template):
        if name not in FIELDS:
            raise ValueError(f"unknown field {name!r} in template")
    dest = Path(dest).expanduser().resolve()
    moves: list[Move] = []
    taken: set[Path] = set()
    for rec in records:
        src = Path(rec["abspath"])
        values = _Values({k: _safe(v) for k, v in rec.items()})
        sub = template.format_map(values)
        parts = [p for p in Path(sub).parts if p not in ("", ".", "..")]
        tail = (
            Path(rec["path"])
            if keep_tree and not Path(rec["path"]).is_absolute()
            else Path(src.name)
        )
        dst = dest.joinpath(*parts, tail)
        skip = ""
        if dst == src:
            skip = "already in place"
        elif dst.exists() or dst in taken:
            skip = "destination exists"
        taken.add(dst)
        moves.append(Move(src, dst, skip))
    return moves


def apply(moves: list[Move], mode: str = "copy") -> tuple[int, list[str]]:
    """Carry out ``moves`` with ``mode`` in ``copy``, ``move``, ``link``
    (symlink) or ``hardlink``. Never overwrites. Returns (done, errors)."""
    done, errors = 0, []
    for m in moves:
        if m.skip:
            continue
        try:
            m.dst.parent.mkdir(parents=True, exist_ok=True)
            if m.dst.exists():
                errors.append(f"{m.dst}: destination exists")
                continue
            if mode == "copy":
                shutil.copy2(m.src, m.dst)
            elif mode == "move":
                shutil.move(m.src, m.dst)
            elif mode == "link":
                os.symlink(m.src, m.dst)
            elif mode == "hardlink":
                os.link(m.src, m.dst)
            else:
                raise ValueError(f"unknown mode {mode!r}")
            done += 1
        except OSError as exc:
            errors.append(f"{m.src}: {exc}")
    return done, errors
