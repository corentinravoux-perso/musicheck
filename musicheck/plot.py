"""Spectrogram and spectrum figure for one file (needs matplotlib)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

__all__ = ["plot_file"]


def plot_file(
    path: str | Path, out: str | Path | None, record: dict, ana: dict
) -> None:
    """Draw the spectrogram and averaged spectrum with the cutoff marked.

    ``ana`` must come from :func:`~musicheck.analysis.analyze` called with
    ``spectrogram_cols > 0``. Shows the figure when ``out`` is ``None``.
    """
    try:
        import matplotlib

        if out is not None:
            matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError(
            "plotting needs matplotlib: pip install 'musicheck[plot]'"
        ) from exc

    freqs = ana["freqs"] / 1000.0
    spec = ana["spectrogram_db"]
    power = ana["power_db"]
    duration = ana.get("decoded_duration") or 1.0
    top = float(np.percentile(spec, 99.5))

    fig, (ax1, ax2) = plt.subplots(
        1,
        2,
        figsize=(13, 5),
        gridspec_kw={"width_ratios": [2.2, 1]},
        constrained_layout=True,
    )
    mesh = ax1.imshow(
        spec.T, origin="lower", aspect="auto", cmap="magma",
        extent=(0, duration, 0, freqs[-1]), vmin=top - 120, vmax=top,
    )  # fmt: skip
    fig.colorbar(mesh, ax=ax1, label="power (dB)")
    ax1.set_xlabel("time (s)")
    ax1.set_ylabel("frequency (kHz)")

    ax2.plot(power, freqs, lw=0.8, color="0.2")
    ax2.set_ylim(0, freqs[-1])
    ax2.set_xlim(top - 140, top + 5)
    ax2.set_xlabel("mean power (dB)")
    ax2.grid(alpha=0.3)
    for ax in (ax1, ax2):
        if record.get("cutoff_khz"):
            ax.axhline(
                record["cutoff_khz"],
                color="tab:red",
                lw=1,
                ls="--",
                label=f"cutoff {record['cutoff_khz']} kHz",
            )
        if record.get("expected_khz"):
            ax.axhline(
                record["expected_khz"],
                color="tab:cyan",
                lw=1,
                ls=":",
                label=f"expected {record['expected_khz']} kHz",
            )
    if record.get("cutoff_khz") or record.get("expected_khz"):
        ax2.legend(loc="lower left", fontsize=8)
    fig.suptitle(
        f"{Path(path).name}\n{record['claimed']}  ->  {record['verdict']}  (real: {record['real']})",
        fontsize=10,
    )
    if out is None:
        plt.show()
    else:
        fig.savefig(out, dpi=120)
    plt.close(fig)
