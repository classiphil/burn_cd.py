#!/usr/bin/env python3
"""
Build a cdrdao .toc (with CD-TEXT pulled from FLAC/lossless-M4A tags) for a
folder of matching audio/WAV pairs, then burn it directly with cdrdao (no
k3b).

Folder layout expected:
    01 - Track Name.flac
    02 - Track Name.m4a
    ...
(.flac and .m4a may be mixed in the same folder; .m4a must be lossless
ALAC, not lossy AAC, or the CD audio will sound encoded.)

Each source file is converted to a matching Red Book .wav (16-bit/44.1kHz/
stereo) with sox. If a .wav already exists next to its source file, it's
reused as-is unless --force-convert is given.

Usage:
    ./burn_cd.py /path/to/album [options]

Options:
    --device DEVICE      optical drive to burn to (default: /dev/sr0)
    --driver DRIVER      cdrdao driver string (default: generic-mmc:0x00000010)
    --speed SPEED        write speed, e.g. 16, 32 (default: 32)
    --toc-out PATH        where to write the .toc (default: <folder>/album.toc)
    --no-cdtext           skip CD-TEXT block entirely
    --language {en,de,fr}  CD-TEXT language map entry (default: en)
    --dither              shaped dither (sox 'dither -s') when converting to wav
    --no-dither           no dither when converting to wav (default)
    --force-convert       re-run sox conversion even if a matching .wav already exists
    --disc-minutes MIN    target disc capacity in minutes, e.g. 74
                          (default: read from the inserted blank, falling back to 80)
    --overburn            write past the disc's rated capacity (also offered
                          interactively when the tracks don't fit)
    --dry-run             generate + validate the .toc, don't burn
    --verify              after burning, rip track 2 (or track 1 if there's only one
                          track) back and diff against source .wav - track 2 is used
                          to avoid pregap read-offset errors some drives report on
                          track 1 even when the burn is fine
    --verify2             after burning, rip track 2 AND the last track back and diff
                          against source .wav
    --eject               eject the disc after burning (and after --verify/--verify2, if also given)

Examples:
    ./burn_cd.py /path/to/album --speed 32 --dither
    ./burn_cd.py /path/to/album --dry-run
    ./burn_cd.py /path/to/album --verify --eject
    ./burn_cd.py /path/to/album --verify2 --eject
    ./burn_cd.py /path/to/album --device /dev/sr1 --speed 16
    ./burn_cd.py /path/to/album --language de
    ./burn_cd.py /path/to/album --language fr
"""
import argparse
import contextlib
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

PUNCT_MAP = {
    "‘": "'", "’": "'",
    "“": '"', "”": '"',
    "‐": "-", "‑": "-", "–": "-", "—": "-",
    "…": "...",
}


def latin1_safe(s: str) -> str:
    """CD-TEXT only supports ISO-8859-1. Normalize typographic punctuation
    and strip accents (NFKD, e.g. 'ő' -> 'o'). For non-Latin scripts
    (Cyrillic, Greek, CJK, ...) that approach drops nearly everything, so
    fall back to a transliteration of the original string instead of
    silently emitting a blank CD-TEXT field."""
    raw = s
    for k, v in PUNCT_MAP.items():
        s = s.replace(k, v)
    s = unicodedata.normalize("NFC", s)
    chars = []
    for c in s:
        try:
            c.encode("latin-1")
            chars.append(c)
        except UnicodeEncodeError:
            chars.append(unicodedata.normalize("NFKD", c).encode("latin-1", "ignore").decode("latin-1"))
    sanitized = "".join(chars)

    orig_nonspace = sum(1 for c in raw if not c.isspace())
    san_nonspace = sum(1 for c in sanitized if not c.isspace())
    if orig_nonspace and san_nonspace / orig_nonspace < 0.5:
        try:
            from unidecode import unidecode
            return unidecode(raw)
        except ImportError:
            print(f"WARNING: {raw!r} contains a non-Latin script and "
                  f"python-unidecode isn't installed; CD-TEXT field will "
                  f"be lossy ({sanitized!r}). Run: sudo pacman -S python-unidecode",
                  file=sys.stderr)
    return sanitized


def esc(s: str) -> str:
    """Escape a string for embedding in a quoted cdrdao TOC field."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def ffprobe_tag(path: Path, tag: str) -> str:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", f"format_tags={tag}",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    ).stdout.strip()
    return out


SOURCE_EXTS = (".flac", ".m4a")


def check_lossless_m4a(path: Path):
    """Refuse to burn lossy AAC disguised as .m4a; ALAC is the only lossless
    codec normally found in an .m4a container."""
    codec = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    ).stdout.strip()
    if codec and codec != "alac":
        sys.exit(f"{path.name}: codec is '{codec}', not lossless ALAC - refusing to burn a lossy file as audio CD")


def convert_to_wav(src: Path, wav: Path, dither: bool):
    """Decode flac/m4a -> Red Book wav (16-bit/44.1kHz/stereo) via sox."""
    if dither:
        cmd = ["sox", str(src), "-b", "16", "-r", "44100", "-c", "2", str(wav), "dither", "-s"]
    else:
        cmd = ["sox", "-D", str(src), "-b", "16", "-r", "44100", "-c", "2", str(wav)]
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)


def collect_tracks(folder: Path, dither: bool, force_convert: bool):
    sources = sorted(p for ext in SOURCE_EXTS for p in folder.glob(f"*{ext}"))
    if not sources:
        sys.exit(f"no .flac/.m4a files found in {folder}")

    tracks = []
    for src in sources:
        if src.suffix.lower() == ".m4a":
            check_lossless_m4a(src)
        wav = src.with_suffix(".wav")
        if force_convert or not wav.exists():
            convert_to_wav(src, wav, dither)
        else:
            print(f"reusing existing {wav.name}")
        title = ffprobe_tag(src, "title") or src.stem
        artist = ffprobe_tag(src, "artist") or ""
        album = ffprobe_tag(src, "album") or folder.name
        album_artist = ffprobe_tag(src, "album_artist") or artist
        tracks.append({
            "wav": wav,
            "title": title,
            "artist": artist,
            "album": album,
            "album_artist": album_artist,
        })
    return tracks


# cdrdao's TOC grammar only accepts the bare literal "EN" or a numeric
# CD-TEXT language code (EBU Tech 3258 Annex 1); German and French have no
# literal keyword, so they must be given as their numeric codes 8 and 15
# (see cdio/cdtext.h).
LANGUAGE_CODES = {
    "en": "EN",
    "de": "8",
    "fr": "15",
}


def build_toc(tracks, no_cdtext: bool, language: str) -> str:
    lines = ["CD_DA", ""]

    if not no_cdtext:
        album_title = latin1_safe(tracks[0]["album"])
        album_performer = latin1_safe(tracks[0]["album_artist"])
        lines += [
            "CD_TEXT {",
            "  LANGUAGE_MAP {",
            f"    0 : {LANGUAGE_CODES[language]}",
            "  }",
            "  LANGUAGE 0 {",
            f'    TITLE "{esc(album_title)}"',
            f'    PERFORMER "{esc(album_performer)}"',
            "  }",
            "}",
            "",
        ]

    for t in tracks:
        lines.append("TRACK AUDIO")
        if not no_cdtext:
            lines += [
                "CD_TEXT {",
                "  LANGUAGE 0 {",
                f'    TITLE "{esc(latin1_safe(t["title"]))}"',
                f'    PERFORMER "{esc(latin1_safe(t["artist"]))}"',
                "  }",
                "}",
            ]
        lines.append(f'FILE "{esc(str(t["wav"].resolve()))}" 0')
        lines.append("")

    return "\n".join(lines)


def total_runtime_seconds(tracks) -> float:
    """Sum WAV payload size / 4 bytes-per-frame / 44100 Hz across all tracks."""
    total = 0.0
    for t in tracks:
        payload = t["wav"].stat().st_size - 44  # skip the WAV header
        total += payload / 4 / 44100
    return total


def read_disc_capacity_minutes(device: str):
    """Best-effort read of the inserted blank's actual capacity via
    'cdrdao disk-info'. Returns minutes (float) or None if no disc is
    present, the drive can't be opened, or the output can't be parsed -
    callers must fall back to a sane default in that case."""
    result = subprocess.run(["cdrdao", "disk-info", "--device", device],
                             capture_output=True, text=True)
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if "capacity" not in line.lower():
            continue
        m = re.search(r"(\d+):(\d+)", line)
        if m:
            return int(m.group(1)) + int(m.group(2)) / 60
    return None


def check_disc_capacity(tracks, disc_minutes: float) -> bool:
    """Bail out before converting/burning if the tracks won't fit the target
    disc size. cdrdao would otherwise fail mid-burn (or silently truncate).
    Returns True if the user confirmed burning past capacity, in which case
    cdrdao must be invoked with --overburn or it will just refuse to start."""
    capacity_sec = disc_minutes * 60
    runtime_sec = total_runtime_seconds(tracks)
    print(f"total runtime: {runtime_sec / 60:.1f} min (disc capacity: {disc_minutes:.1f} min)")
    if runtime_sec > capacity_sec:
        print(f"WARNING: total runtime {runtime_sec / 60:.1f} min exceeds the {disc_minutes:.1f} min "
              f"disc capacity by {(runtime_sec - capacity_sec) / 60:.1f} min - "
              f"use --disc-minutes to match your media if it's not a standard 80 min disc",
              file=sys.stderr)
        if not confirm_burn("Try to overburn anyway?"):
            sys.exit("aborted: tracks do not fit the disc")
        return True
    return False


def validate_toc(toc_path: Path):
    result = subprocess.run(["cdrdao", "show-toc", str(toc_path)],
                             capture_output=True, text=True)
    errors = [l for l in result.stdout.splitlines() + result.stderr.splitlines()
              if "ERROR" in l]
    if errors:
        print("\n".join(errors), file=sys.stderr)
        sys.exit("cdrdao rejected the generated .toc (see ERROR lines above)")
    print(result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "toc looks valid")


def show_toc_preview(toc_path: Path):
    print(f"--- {toc_path} ---")
    print(toc_path.read_text(encoding="utf-8").rstrip())
    print("---")


def confirm_burn(question: str = "Start burning now?") -> bool:
    """Read the confirmation straight from the controlling terminal rather
    than stdin - under sudo, stdin can have a stray leftover newline from
    the password prompt that input() would otherwise consume immediately,
    cancelling the burn before the user can type anything."""
    print(f"{question} [y/N] ", end="", flush=True)
    try:
        with open("/dev/tty") as tty:
            reply = tty.readline().strip().lower()
    except OSError:
        reply = input().strip().lower()
    return reply in ("y", "yes")


@contextlib.contextmanager
def shutdown_inhibitor():
    """Hold a systemd shutdown/sleep inhibitor for the duration of the block.
    Gracefully skips if systemd-inhibit isn't available (non-systemd systems)."""
    if not shutil.which("systemd-inhibit"):
        print("note: systemd-inhibit not found - shutdown protection disabled", file=sys.stderr)
        yield
        return
    proc = subprocess.Popen(
        ["systemd-inhibit", "--what=shutdown:sleep", "--who=burn_cd.py",
         "--why=Burning a CD - do not shut down until it finishes",
         "--mode=block", "sleep", "infinity"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print("shutdown inhibitor active (systemd-inhibit) - shutdown blocked until the burn completes")
    try:
        yield
    finally:
        proc.terminate()
        proc.wait()
        print("shutdown inhibitor released")


def burn(toc_path: Path, device: str, driver: str, speed: int, overburn: bool = False):
    cmd = ["cdrdao", "write", "--device", device, "--driver", driver,
           "--speed", str(speed), "-n", "-v", "2", "--force"]
    if overburn:
        cmd.append("--overburn")
    cmd.append(str(toc_path))
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)


def eject_disc(device: str):
    print(f"+ eject {device}")
    subprocess.run(["eject", device], check=True)


def verify_track(folder: Path, tracks, device: str, track_no: int):
    """Rip the given track back and confirm it's a byte-identical (offset-corrected) match."""
    import tempfile
    src_wav = tracks[track_no - 1]["wav"]
    with tempfile.TemporaryDirectory() as tmp:
        ripped = Path(tmp) / f"track{track_no:02d}.wav"
        subprocess.run(["cdparanoia", "-d", device, str(track_no), str(ripped)], check=True)

        src = src_wav.read_bytes()[44:]
        rip = ripped.read_bytes()[44:]

        # Anchor in the middle of the track (avoids edge silence) and search
        # within ±24000 bytes (≈ ±10 CD frames, well beyond any real drive
        # offset) to prevent false matches from repetitive/ambient content.
        NEEDLE_LEN = 4096
        MAX_OFFSET = 24000
        anchor = max(NEEDLE_LEN, len(src) // 2)
        needle = src[anchor:anchor + NEEDLE_LEN]
        win_start = max(0, anchor - MAX_OFFSET)
        win_end = min(len(rip), anchor + MAX_OFFSET + NEEDLE_LEN)
        local_idx = rip[win_start:win_end].find(needle)
        if local_idx < 0:
            sys.exit(f"verify: could not find a matching offset between source and ripped audio for track {track_no}")
        shift = (win_start + local_idx) - anchor

        s = abs(shift)
        if shift > 0:
            a, b = src[:-s], rip[s:]
        elif shift < 0:
            a, b = src[s:], rip[:-s]
        else:
            a, b = src, rip
        n = min(len(a), len(b))
        diffs = sum(1 for i in range(n) if a[i] != b[i])

        print(f"verify: track {track_no} sample offset {shift // 4} frames, "
              f"compared {n} bytes, {diffs} differing ({100 * diffs / n:.4f}%)")
        if diffs:
            sys.exit(f"verify: FAILED — ripped track {track_no} does not match source after offset correction")
        print(f"verify: OK — track {track_no} is a bit-exact match")


def verify_track2_or_1(folder: Path, tracks, device: str):
    """Verify track 2 rather than track 1: cdparanoia's read-offset
    compensation can ask for sectors just before the disc's start when
    ripping track 1, which some drives report as I/O errors even on a
    perfectly good burn. Track 2 has no such pregap-adjacent edge case."""
    verify_track(folder, tracks, device, 2 if len(tracks) > 1 else 1)


def verify_first_last(folder: Path, tracks, device: str):
    verify_track2_or_1(folder, tracks, device)
    last = len(tracks)
    if last not in (1, 2):
        verify_track(folder, tracks, device, last)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path, help="folder containing matching .flac/.wav pairs")
    ap.add_argument("--device", default="/dev/sr0")
    ap.add_argument("--driver", default="generic-mmc:0x00000010")
    ap.add_argument("--speed", type=int, default=32)
    ap.add_argument("--toc-out", type=Path, default=None,
                     help="where to write the .toc (default: <folder>/album.toc)")
    ap.add_argument("--no-cdtext", action="store_true", help="skip CD-TEXT block entirely")
    ap.add_argument("--language", choices=["en", "de", "fr"], default="en",
                     help="CD-TEXT language map entry (default: en)")
    dither_group = ap.add_mutually_exclusive_group()
    dither_group.add_argument("--dither", action="store_true",
                               help="apply shaped dither (sox 'dither -s') when converting flac to wav")
    dither_group.add_argument("--no-dither", action="store_true",
                               help="disable dither entirely (sox -D) when converting flac to wav (default)")
    ap.add_argument("--force-convert", action="store_true",
                     help="re-run sox conversion even if a matching .wav already exists")
    ap.add_argument("--disc-minutes", type=int, default=None,
                     help="target disc capacity in minutes, e.g. 74 for older media "
                          "(default: read from the inserted blank, falling back to 80)")
    ap.add_argument("--overburn", action="store_true",
                     help="pass --overburn to cdrdao so it writes past the disc's rated "
                          "capacity (implied by confirming the over-capacity prompt)")
    ap.add_argument("--dry-run", action="store_true", help="generate + validate the .toc, don't burn")
    ap.add_argument("--verify", action="store_true",
                     help="after burning, rip track 2 (or track 1 if there's only one track) "
                          "back and diff against source .wav")
    ap.add_argument("--verify2", action="store_true",
                     help="after burning, rip track 2 AND the last track back and diff against source .wav")
    ap.add_argument("--eject", action="store_true",
                     help="eject the disc after burning (and after --verify, if also given)")
    args = ap.parse_args()

    folder = args.folder.resolve()
    if not folder.is_dir():
        sys.exit(f"not a folder: {folder}")

    tracks = collect_tracks(folder, dither=args.dither, force_convert=args.force_convert)
    print(f"found {len(tracks)} tracks in {folder}")

    disc_minutes = args.disc_minutes
    if disc_minutes is None:
        disc_minutes = read_disc_capacity_minutes(args.device)
        if disc_minutes is None:
            print("note: could not read capacity from the inserted blank "
                  "(no disc / drive busy / unrecognized cdrdao output) - "
                  "assuming a standard 80 min disc; pass --disc-minutes to override",
                  file=sys.stderr)
            disc_minutes = 80
        else:
            print(f"detected disc capacity: {disc_minutes:.1f} min")

    overburn = check_disc_capacity(tracks, disc_minutes) or args.overburn

    toc_path = args.toc_out or (folder / "album.toc")
    toc_path.write_text(build_toc(tracks, args.no_cdtext, args.language), encoding="utf-8")
    print(f"wrote {toc_path}")

    validate_toc(toc_path)

    show_toc_preview(toc_path)

    if args.dry_run:
        print("dry-run: not burning")
        return

    if not confirm_burn():
        sys.exit("burn cancelled")

    with shutdown_inhibitor():
        burn(toc_path, args.device, args.driver, args.speed, overburn=overburn)

        if args.verify:
            verify_track2_or_1(folder, tracks, args.device)

        if args.verify2:
            verify_first_last(folder, tracks, args.device)

        if args.eject:
            eject_disc(args.device)


if __name__ == "__main__":
    main()
