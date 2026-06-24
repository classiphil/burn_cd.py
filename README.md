# burn_cd.py

A single-file CLI for burning a folder of lossless tracks to an audio CD,
straight from the terminal — no k3b, no GUI.

Point it at a folder of FLAC (or lossless ALAC `.m4a`) tracks and it will:

1. Convert each track to Red Book WAV (16-bit / 44.1 kHz / stereo) with `sox`.
2. Build a `cdrdao` `.toc` file, pulling CD-TEXT (album/track/artist) straight
   from the source tags via `ffprobe`.
3. Burn the disc directly with `cdrdao`.

## Requirements

These need to be on your `PATH`:

- [`ffprobe`](https://ffmpeg.org/) — reads tags and codec info from FLAC/M4A
- [`sox`](http://sox.sourceforge.net/) — decodes source audio to Red Book WAV
- [`cdrdao`](https://cdrdao.sourceforge.net/) — validates and burns the `.toc`
- [`cdparanoia`](https://www.xiph.org/paranoia/) — only needed for `--verify`/`--verify2`
- `eject` — only needed for `--eject`
- `unidecode` (optional Python package) — fallback transliteration for
  non-Latin CD-TEXT strings (Cyrillic, Greek, CJK, ...)

No other dependencies, no build step, no package manifest — just run the
script directly.

## Usage

```
./burn_cd.py /path/to/album [options]
```

Folder layout expected:

```
01 - Track Name.flac
02 - Track Name.m4a
...
```

`.flac` and `.m4a` may be mixed in the same folder. `.m4a` files must be
lossless ALAC, not lossy AAC — the script checks the actual codec and
refuses to burn otherwise, regardless of file extension.

Each source file is converted to a matching `.wav` next to it. If that
`.wav` already exists, it's reused as-is unless `--force-convert` is given.

### Options

| Option | Description |
|---|---|
| `--device DEVICE` | optical drive to burn to (default: `/dev/sr0`) |
| `--driver DRIVER` | cdrdao driver string (default: `generic-mmc:0x00000010`) |
| `--speed SPEED` | write speed, e.g. `16`, `32` (default: `32`) |
| `--toc-out PATH` | where to write the `.toc` (default: `<folder>/album.toc`) |
| `--no-cdtext` | skip the CD-TEXT block entirely |
| `--language {en,de,fr}` | CD-TEXT language map entry (default: `en`) |
| `--dither` | shaped dither (sox `dither -s`) when converting to WAV |
| `--no-dither` | no dither when converting to WAV (default) |
| `--force-convert` | re-run the sox conversion even if a matching `.wav` already exists |
| `--disc-minutes MIN` | target disc capacity in minutes, e.g. `74` (default: read from the inserted blank, falling back to `80`) |
| `--dry-run` | generate and validate the `.toc`, don't burn |
| `--verify` | after burning, rip track 2 back (track 1 if there's only one track) and diff it against the source `.wav` |
| `--verify2` | after burning, rip track 2 **and** the last track back and diff against source |
| `--eject` | eject the disc after burning (and after `--verify`/`--verify2`, if given) |

`--verify` checks track 2 rather than track 1 on purpose: some drives report
spurious I/O errors when `cdparanoia`'s read-offset compensation requests
sectors just before the very start of the disc, even on a perfectly good
burn. Track 2 avoids that edge case entirely.

### Examples

```
./burn_cd.py /path/to/album --speed 32 --dither
./burn_cd.py /path/to/album --dry-run
./burn_cd.py /path/to/album --verify --eject
./burn_cd.py /path/to/album --verify2 --eject
./burn_cd.py /path/to/album --device /dev/sr1 --speed 16
./burn_cd.py /path/to/album --language de
./burn_cd.py /path/to/album --language fr
```

## Notes

- CD-TEXT is ISO-8859-1 only. Typographic punctuation gets normalized,
  accents are NFKD-decomposed, and `unidecode` is used as a fallback for
  scripts where stripping accents would discard most of the string.
- `cdrdao`'s TOC grammar only accepts the literal `EN` keyword or numeric CD-TEXT
  language codes (EBU Tech 3258 Annex 1) — there's no literal keyword for German
  or French, so they're mapped to their numeric codes internally.
- There's no mocking layer for any of the external tools — testing changes
  means running against a real folder of audio files and, for burn/verify/
  eject, real optical hardware.
