# musicheck

[![tests](https://github.com/corentinravoux-perso/musicheck/actions/workflows/tests.yml/badge.svg)](https://github.com/corentinravoux-perso/musicheck/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Check that the songs in a folder really have the quality their format claims,
then sort, filter and organise them by any property.

A FLAC made from an MP3 is still an MP3. A "320 kbps" file encoded from a
128 kbps download still sounds like 128 kbps. Neither shows up in the file
size or the tags; it shows up in the spectrum. musicheck decodes every file,
finds where its content really stops, and compares that with what the
format, the bitrate and the encoder header promise.

It sits between [whatsmybitrate](https://github.com/oren-cohen/whatsmybitrate)
(spectral cutoff only) and [Fakin' The Funk](https://fakinthefunk.net)
(GUI, Windows/macOS): a command-line tool, a few small pip dependencies, no
system FFmpeg needed.

## What it checks

| Check | How | Verdict |
|---|---|---|
| Lossless made from lossy | brick-wall low-pass in a FLAC/ALAC/WAV/AIFF (e.g. 16 kHz: from a 128k MP3) | FAKE below 19 kHz, SUSPECT up to 20.6 kHz |
| Up-converted lossy | cutoff of an MP3/AAC/Opus/Vorbis/WMA far below what its bitrate gives | FAKE / SUSPECT |
| LAME header mismatch | the MP3's LAME tag records the encoder's low-pass; audio stopping well below it means the source was already lossy | FAKE / SUSPECT (tighter margins) |
| Upsampled hi-res | 88.2–192 kHz file whose content stops near 22–24 kHz | FAKE |
| Padded bit depth | 24-bit file whose 8 low bits are always zero | FAKE |
| Damaged files | decode errors, decoded length shorter than the header | SUSPECT |
| Unreadable files | no audio stream, decoder failure | ERROR |
| Mono as stereo, clipping | side/mid energy, clipped samples | note only |

Every file also gets measured properties you can sort on: cutoff, the bitrate
class the content is typical of ("Real": `~128k`, `~320k`, `full band`),
DR value (dynamic range, DR-meter style), peak and RMS level, clipping,
stereo width, effective bits, encoder, and the tags (artist, album, title,
year, genre, BPM, key).

The "Real" column compares the cutoff with LAME's MP3 low-pass table, so it
reads as an MP3-equivalent class. Opus always cuts at 20 kHz, whatever its
bitrate.

## Install

```bash
git clone https://github.com/corentinravoux-perso/musicheck
cd musicheck
pip install -e ".[plot]"      # plot extra: matplotlib, for `info --plot`
```

Dependencies: `av` (PyAV, ships its own FFmpeg), `mutagen`, `numpy`, `click`,
`rich`. Python 3.10 or newer.

## Usage

```bash
musicheck scan ~/Music                         # table of every file + verdict
musicheck scan ~/Music -s -verdict,cutoff_khz  # worst first, then lowest cutoff
musicheck scan ~/Music -w verdict!=OK -c quality
musicheck scan ~/Music -w 'format=FLAC' -w 'cutoff_khz<19'
musicheck scan ~/Music -c dj -s bpm -w 'genre~techno'
musicheck scan ~/Music -q --csv report.csv     # every field to CSV (or --json)
musicheck scan ~/Music -q --fail-on fake       # exit 1 if any fake: for scripts

musicheck info track.flac                      # every property and issue
musicheck info track.flac --plot spec.png      # spectrogram + spectrum, cutoff marked

musicheck organize ~/Music -o ~/Sorted                      # dry run: {verdict}/{real}
musicheck organize ~/Music -o ~/Sorted -b '{format}/{real}' --link --apply
musicheck organize ~/Music -o ~/Fakes -w verdict=FAKE --move --apply --keep-tree

musicheck fields                               # every field for -s / -w / -c / -b
musicheck cache clear
```

- **`-s/--sort`** takes comma-separated fields; `-` sorts descending.
  `verdict` sorts by severity (OK < SUSPECT < FAKE < ERROR). Empty values
  always come last.
- **`-w/--where`** filters, repeatable (all must match): `=` `!=` `<` `<=` `>`
  `>=` (numeric when possible), `~` / `!~` (case-insensitive substring), and
  `|` for alternatives: `verdict=FAKE|SUSPECT`.
- **`-c/--columns`** takes fields or presets: `default`, `quality`, `levels`,
  `tags`, `dj`, `all`.
- **`organize`** copies by default (`--move`, `--link`, `--hardlink`), prints
  the plan unless `--apply` is given, and never overwrites a file.

Measurements are cached in `~/.cache/musicheck/cache.sqlite`, keyed by path,
size and modification time. Verdicts are recomputed from the cached
measurements on every run, so re-sorting or re-filtering a scanned library
takes a second. Files are analysed in parallel on all cores (`-j` to change);
a 4-minute MP3 takes about a second on one core.

## How the cutoff is found

Each file is decoded once, streaming. The mid channel goes through a Hann
STFT (~11 Hz bins), and the power spectrum is averaged over the whole track.
A bin is a cutoff candidate when

1. the median level over the 600 Hz below it is at least 15 dB above the
   median over the 600 Hz above it (a steep fall, not a natural roll-off);
2. it is 18 dB above the 95th percentile of everything higher (nothing comes
   back above the cut);
3. there is real content below it (within 85 dB of the 1–6 kHz level).

The steepest candidate wins. Its frequency is compared with

- for lossless files: the 19 / 20.6 kHz limits above, or the source Nyquist
  (× 1.18) for hi-res;
- for MP3 with a LAME tag: the low-pass the encoder recorded (FAKE if the
  audio stops 1.2 kHz below it);
- for other lossy files: a conservative per-codec table of the lowest cutoff
  each bitrate gives across common encoders (FAKE 2.5 kHz below it, or 0.9 kHz
  below when the content is typical of half the claimed bitrate).

### Limits

- A master can be low-passed on purpose, and some old encoders (Xing,
  early FhG) cut at 16 kHz at every bitrate. Ambiguous cases come out as
  SUSPECT, not FAKE: check them with `info --plot`.
- Very quiet or very short tracks (< 5 s) are not judged on their spectrum.
- A file transcoded from a high-bitrate lossy source (V0, 320k) into
  lossless shows a cutoff around 19.5–20.5 kHz: SUSPECT, since some genuine
  CDs are mastered like that.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

The test suite generates genuine and transcoded files with PyAV's encoders
(LAME, AAC, Opus, FLAC, PCM) and checks every verdict end to end.

## Release

```bash
uvx bump-my-version bump patch   # or minor / major: commits + tags v<version>
git push --follow-tags
```

## License

MIT — see [`LICENSE`](LICENSE).
