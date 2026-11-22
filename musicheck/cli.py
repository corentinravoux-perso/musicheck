"""Command-line interface: ``musicheck scan | info | organize | fields | cache``."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from . import __version__
from .evaluate import build_record
from .report import (
    FIELDS,
    PRESETS,
    filter_records,
    parse_sort,
    render_table,
    resolve_columns,
    sort_records,
    summary_counts,
    write_csv,
    write_json,
)
from .scan import Cache, scan


def _console():
    from rich.console import Console

    return Console(stderr=False)


def _common_root(paths: tuple[str, ...]) -> Path | None:
    resolved = [Path(p).expanduser().resolve() for p in paths]
    if len(resolved) == 1:
        return resolved[0] if resolved[0].is_dir() else resolved[0].parent
    try:
        import os

        return Path(os.path.commonpath(resolved))
    except ValueError:
        return None


def _collect(paths, recursive, jobs, no_cache, max_duration, quiet) -> list[dict]:
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        TextColumn,
        TimeRemainingColumn,
    )

    cache = None if no_cache else Cache()
    root = _common_root(paths)
    try:
        if quiet:
            measured = scan(
                paths,
                recursive=recursive,
                jobs=jobs,
                cache=cache,
                max_duration=max_duration,
            )
        else:
            with Progress(
                TextColumn("[bold]scanning"), BarColumn(), MofNCompleteColumn(),
                TimeRemainingColumn(), TextColumn("{task.description}"),
                transient=True, console=_console(),
            ) as progress:  # fmt: skip
                task = progress.add_task("", total=None)

                def update(path: Path, done: int, total: int) -> None:
                    progress.update(
                        task, completed=done, total=total, description=path.name[:40]
                    )

                measured = scan(paths, recursive=recursive, jobs=jobs, cache=cache,
                                max_duration=max_duration, on_progress=update)  # fmt: skip
    finally:
        if cache:
            cache.close()
    return [build_record(p, m, root) for p, m in measured]


def _bad(exc: Exception):
    raise click.BadParameter(str(exc)) from exc


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="musicheck")
def main():
    """Check that audio files really have the quality their format claims.

    Finds fake lossless files (FLAC made from MP3), up-converted MP3/AAC
    (a "320 kbps" file encoded from a 128 kbps one), upsampled hi-res and
    padded 24-bit files, plus damaged files; then sorts, filters and organises
    a library by any property.
    """


_scan_options = [
    click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True)),
    click.option(
        "--no-recursive", is_flag=True, help="Do not descend into sub-folders."
    ),
    click.option(
        "-j",
        "--jobs",
        type=int,
        default=None,
        help="Parallel workers (default: all cores).",
    ),
    click.option(
        "--no-cache", is_flag=True, help="Ignore and do not update the cache."
    ),
    click.option(
        "--max-duration",
        type=float,
        default=None,
        metavar="S",
        help="Analyse only the first S seconds of each file (faster).",
    ),  # fmt: skip
    click.option(
        "-w",
        "--where",
        "where",
        multiple=True,
        metavar="EXPR",
        help="Filter, repeatable: 'verdict!=OK', 'cutoff_khz<19', 'artist~daft', "
        "'verdict=FAKE|SUSPECT'.",
    ),  # fmt: skip
]


def scan_options(f):
    for opt in reversed(_scan_options):
        f = opt(f)
    return f


@main.command("scan")
@scan_options
@click.option("-s", "--sort", "sort", default="path", show_default=True, metavar="KEYS",
              help="Comma-separated fields, '-' for descending: '-verdict,cutoff_khz'.")  # fmt: skip
@click.option("-c", "--columns", default=None, metavar="COLS",
              help=f"Fields or presets ({', '.join(PRESETS)}), comma-separated.")  # fmt: skip
@click.option(
    "-n", "--limit", type=int, default=None, help="Show only the first N rows."
)
@click.option(
    "--csv",
    "csv_path",
    type=click.Path(dir_okay=False),
    help="Write all fields to a CSV file.",
)
@click.option(
    "--json",
    "json_path",
    type=click.Path(dir_okay=False),
    help="Write all fields to a JSON file.",
)
@click.option(
    "-q", "--quiet", is_flag=True, help="No progress bar and no table (exports only)."
)
@click.option("--fail-on", type=click.Choice(["fake", "suspect"]), default=None,
              help="Exit with status 1 if any file is FAKE (or SUSPECT and worse).")  # fmt: skip
def scan_cmd(paths, no_recursive, jobs, no_cache, max_duration, where, sort, columns, limit,
             csv_path, json_path, quiet, fail_on):  # fmt: skip
    """Analyse audio files and list them with their verdicts."""
    try:
        keys = parse_sort(sort)
        cols = resolve_columns(columns)
        records = _collect(paths, not no_recursive, jobs, no_cache, max_duration, quiet)
        records = sort_records(filter_records(records, where), keys)
    except ValueError as exc:
        _bad(exc)
    if csv_path:
        write_csv(records, csv_path)
    if json_path:
        write_json(records, json_path)
    shown = records[:limit] if limit else records
    if not quiet:
        console = _console()
        if shown:
            render_table(shown, cols, console)
        counts = summary_counts(records)
        parts = [
            f"{counts.get(v, 0)} {v}"
            for v in ("OK", "SUSPECT", "FAKE", "ERROR")
            if counts.get(v)
        ]
        console.print(f"{len(records)} file(s): " + (", ".join(parts) or "none"))
    if fail_on:
        bad = {"fake": {"FAKE", "ERROR"}, "suspect": {"SUSPECT", "FAKE", "ERROR"}}[
            fail_on
        ]
        if any(r["verdict"] in bad for r in records):
            sys.exit(1)


@main.command("info")
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
@click.option("--plot", "plot_path", type=click.Path(dir_okay=False), default=None,
              help="Save a spectrogram + spectrum figure (PNG, PDF...).")  # fmt: skip
@click.option("--show", is_flag=True, help="Open the figure in a window.")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Print the full record and raw measures as JSON.",
)
def info_cmd(file, plot_path, show, as_json):
    """Show every property and issue of one file."""
    import json

    from .analysis import analyze
    from .evaluate import evaluate
    from .metadata import probe

    path = Path(file).resolve()
    meta = probe(path)
    want_plot = bool(plot_path or show)
    try:
        ana = analyze(path, spectrogram_cols=900 if want_plot else 0)
        err = None
    except Exception as exc:
        ana, err = None, f"{type(exc).__name__}: {exc}"
    plain = {
        k: v
        for k, v in (ana or {}).items()
        if k not in ("spectrogram_db", "freqs", "power_db")
    }
    measured = {"meta": meta, "analysis": plain if ana else None, "error": err}
    rec = build_record(path, measured, path.parent)
    ev = evaluate(meta, plain if ana else {"error": err})

    if as_json:
        plain.pop("spectrum_db", None)
        click.echo(json.dumps({"record": rec, "issues": ev["issues"], "meta": meta, "analysis": plain},
                              indent=1, ensure_ascii=False, default=str))  # fmt: skip
    else:
        from rich.table import Table

        console = _console()
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column(style="bold")
        table.add_column()
        style = {
            "OK": "green",
            "SUSPECT": "yellow",
            "FAKE": "bold red",
            "ERROR": "magenta",
        }
        for key in ("verdict", "claimed", "real", "cutoff_khz", "expected_khz", "lowpass_khz", "encoder",
                    "format", "codec", "bitrate", "mode", "sample_rate", "bits", "eff_bits", "channels",
                    "duration", "size_mb", "dr", "peak_db", "rms_db", "clip_pct", "stereo_db",
                    "decode_errors", "artist", "album", "title", "year", "genre", "bpm", "key"):  # fmt: skip
            v = rec.get(key)
            if v in (None, ""):
                continue
            text = str(v).replace("[", r"\[")
            if key == "verdict":
                text = f"[{style.get(text, '')}]{text}[/]"
            table.add_row(FIELDS[key][0], text)
        console.print(f"[bold]{path.name}[/]")
        console.print(table)
        for i in ev["issues"]:
            colour = {"FAKE": "red", "SUSPECT": "yellow", "ERROR": "magenta"}.get(
                i["level"], "cyan"
            )
            console.print(f"  [{colour}]{i['level']:<7}[/] {i['code']}: {i['message']}")
    if want_plot and ana:
        from .plot import plot_file

        try:
            plot_file(path, plot_path, rec, ana)
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc
        if plot_path:
            click.echo(f"figure written to {plot_path}", err=True)


@main.command("organize")
@scan_options
@click.option("-o", "--into", "dest", required=True, type=click.Path(file_okay=False),
              help="Destination root folder.")  # fmt: skip
@click.option("-b", "--by", "template", default="{verdict}/{real}", show_default=True,
              help="Folder template from fields, e.g. '{format}/{artist}/{album}'.")  # fmt: skip
@click.option(
    "--move", "mode", flag_value="move", help="Move files instead of copying."
)
@click.option(
    "--link", "mode", flag_value="link", help="Create symlinks instead of copying."
)
@click.option(
    "--hardlink",
    "mode",
    flag_value="hardlink",
    help="Create hard links instead of copying.",
)
@click.option(
    "--keep-tree",
    is_flag=True,
    help="Keep sub-folders (album folders) under each template folder.",
)
@click.option(
    "--apply",
    "do_apply",
    is_flag=True,
    help="Do it. Without this flag only the plan is printed.",
)
def organize_cmd(paths, no_recursive, jobs, no_cache, max_duration, where, dest, template, mode,
                 keep_tree, do_apply):  # fmt: skip
    """Sort files into folders by their properties (dry run by default).

    \b
    Examples:
      musicheck organize ~/Music -o ~/Sorted                    # {verdict}/{real}
      musicheck organize ~/Music -o ~/Sorted -b '{format}/{real}' --link --apply
      musicheck organize ~/Music -o ~/Fakes -w verdict=FAKE --move --apply
    """
    from .organize import apply, plan

    mode = mode or "copy"
    try:
        records = filter_records(
            _collect(paths, not no_recursive, jobs, no_cache, max_duration, False),
            where,
        )
        moves = plan(records, dest, template, keep_tree)
    except (ValueError, KeyError) as exc:
        _bad(exc)
    console = _console()
    dest_root = Path(dest).expanduser().resolve()
    for m in moves:
        rel = m.dst.relative_to(dest_root) if m.dst.is_relative_to(dest_root) else m.dst
        note = f"  [yellow](skip: {m.skip})[/]" if m.skip else ""
        console.print(f"{m.src.name}  ->  {rel}{note}", markup=True, highlight=False)
    todo = sum(1 for m in moves if not m.skip)
    if not do_apply:
        console.print(f"\n[bold]dry run[/]: {todo} file(s) would be {mode}{'d' if mode != 'copy' else 'ied'}"
                      f" into {dest_root}. Add --apply to do it.")  # fmt: skip
        return
    done, errors = apply(moves, mode)
    for e in errors:
        console.print(f"[red]error[/] {e}")
    console.print(f"{done} file(s) done ({mode}), {len(errors)} error(s).")


@main.command("fields")
def fields_cmd():
    """List the fields usable with --sort, --where, --columns and --by."""
    from rich.table import Table

    table = Table("field", "description", box=None, header_style="bold")
    for name, (_, desc) in FIELDS.items():
        table.add_row(name, desc)
    console = _console()
    console.print(table)
    console.print("\npresets for --columns: " + ", ".join(f"{k}" for k in PRESETS))


@main.group("cache")
def cache_group():
    """Manage the measurement cache."""


@cache_group.command("clear")
def cache_clear():
    """Delete all cached measurements."""
    cache = Cache()
    n = cache.clear()
    cache.close()
    click.echo(f"removed {n} cached measurement(s) from {cache.path}")


@cache_group.command("path")
def cache_path():
    """Print the cache file location."""
    click.echo(Cache().path)


if __name__ == "__main__":  # pragma: no cover
    main()
