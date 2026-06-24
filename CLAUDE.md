# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

`burn_cd.py` is a single-file CLI script. It takes a folder of FLAC/lossless-M4A
tracks, converts them to Red Book WAV (16-bit/44.1kHz/stereo) with `sox`,
builds a cdrdao `.toc` with CD-TEXT pulled from the source tags via `ffprobe`,
and burns the disc directly with `cdrdao` (bypassing GUI tools like k3b).

There is no build step, package manifest, or test suite — it's run directly:

```
./burn_cd.py /path/to/album [options]
./burn_cd.py /path/to/album --dry-run      # generate + validate .toc, don't burn
./burn_cd.py /path/to/album --verify --eject
```

Run `./burn_cd.py --help` for the full option list (device, driver, speed,
language, dither, force-convert, etc.) — the argparse help text in `main()`
and the module docstring are the source of truth and should stay in sync if
options change.

## External tool dependencies

The script shells out to (and assumes present on PATH):
- `ffprobe` — read FLAC/M4A tags and codec info
- `sox` — decode source audio to Red Book WAV
- `cdrdao` — validate (`show-toc`) and burn (`write`) the `.toc`
- `cdparanoia` — used only by `--verify`/`--verify2` to rip a track back for
  comparison. Defaults to track 2 (not track 1): cdparanoia's read-offset
  compensation can request sectors just before the disc's start when ripping
  track 1, which some drives report as I/O errors even on a good burn (see
  `verify_track2_or_1()`).
- `eject` — used only by `--eject`
- `unidecode` (optional Python package) — fallback transliteration for
  non-Latin CD-TEXT strings when `latin1_safe()`'s NFKD decomposition would
  drop most of the string

There's no mocking layer for any of these; testing changes generally means
running against a real folder of audio files and (for burn/verify/eject) real
optical hardware.

## Key constraints baked into the code

- **CD-TEXT is ISO-8859-1 only.** `latin1_safe()` normalizes typographic
  punctuation, NFKD-decomposes accents, and falls back to `unidecode` for
  scripts (Cyrillic, Greek, CJK, ...) where stripping accents would discard
  most of the string. Any change to CD-TEXT field handling needs to go
  through this function, not bypass it.
- **cdrdao's TOC grammar only accepts literal `EN` or numeric CD-TEXT language
  codes** (EBU Tech 3258 Annex 1) — there's no literal keyword for German, so
  `LANGUAGE_CODES` maps `"de"` to the numeric code `"8"`. Adding a new
  `--language` choice means adding both the argparse choice and an entry here.
- **`.m4a` must be lossless ALAC, not lossy AAC.** `check_lossless_m4a()`
  probes the actual codec and refuses to burn if it isn't `alac`, regardless
  of file extension.
- **WAV reuse:** if a `.wav` already exists next to its source file,
  `collect_tracks()` reuses it as-is and skips re-conversion unless
  `--force-convert` is passed — don't assume every run re-encodes.
- **`verify_track()`** does an offset-corrected byte comparison (handles
  drive read-offset by searching for a short needle from the source in the
  ripped audio) rather than a naive diff — preserve this when touching that
  function, since optical drives commonly have a nonzero sample read offset.
