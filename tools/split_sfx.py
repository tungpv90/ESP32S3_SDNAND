#!/usr/bin/env python3
"""Split a long WAV recording into individual sound-effect clips.

The detector estimates the recording's noise floor, finds meaningful sound
regions, joins short pauses inside one effect, and preserves a little ambience
at both ends so reverb tails are not cut off.
"""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

import numpy as np
import soundfile as sf


def contiguous_regions(mask: np.ndarray) -> list[tuple[int, int]]:
    changes = np.diff(np.r_[False, mask, False].astype(np.int8))
    return list(zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)))


def merge_nearby(
    regions: list[tuple[int, int]], max_gap_frames: int
) -> list[tuple[int, int]]:
    if not regions:
        return []

    merged: list[tuple[int, int]] = []
    start, end = regions[0]
    for next_start, next_end in regions[1:]:
        if next_start - end <= max_gap_frames:
            end = next_end
        else:
            merged.append((start, end))
            start, end = next_start, next_end
    merged.append((start, end))
    return merged


def db(value: float) -> float:
    return 20.0 * math.log10(max(value, 1e-12))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument(
        "--split-gap",
        type=float,
        default=0.75,
        help="quiet gap in seconds that separates effects (default: 0.75)",
    )
    parser.add_argument("--frame-ms", type=float, default=10.0)
    parser.add_argument("--inside-gap", type=float, default=0.18)
    parser.add_argument("--pre-roll", type=float, default=0.06)
    parser.add_argument("--post-roll", type=float, default=0.18)
    args = parser.parse_args()

    audio, sample_rate = sf.read(args.input, always_2d=True, dtype="float32")
    if not len(audio):
        raise SystemExit("Input WAV is empty")

    frame_size = max(1, round(sample_rate * args.frame_ms / 1000.0))
    usable_samples = len(audio) // frame_size * frame_size
    envelope = np.max(np.abs(audio[:usable_samples]), axis=1)
    framed = envelope.reshape(-1, frame_size)
    rms = np.sqrt(np.mean(framed * framed, axis=1) + 1e-15)
    rms_db = 20.0 * np.log10(rms)

    # The low third represents room/compression noise in this recording. The
    # release threshold follows it, but is capped to avoid retaining hiss.
    measurable = rms_db[rms_db > -120.0]
    noise_floor_db = float(np.percentile(measurable, 30))
    release_db = min(-58.0, max(-66.0, noise_floor_db + 10.0))
    meaningful_peak_db = min(-38.0, max(-46.0, noise_floor_db + 30.0))

    audible = rms_db >= release_db
    regions = contiguous_regions(audible)
    regions = merge_nearby(
        regions, round(args.inside_gap * sample_rate / frame_size)
    )

    # Very low isolated bumps are recording noise. Retain quiet, short effects
    # when their frame peak clearly rises above the estimated floor.
    meaningful = [
        (start, end)
        for start, end in regions
        if float(np.max(rms_db[start:end])) >= meaningful_peak_db
    ]

    split_gap_frames = round(args.split_gap * sample_rate / frame_size)
    effects = merge_nearby(meaningful, split_gap_frames)
    if not effects:
        raise SystemExit("No sound effects detected")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "manifest.csv"
    rows: list[dict[str, str | int]] = []
    fade_samples = max(1, round(sample_rate * 0.008))

    for index, (start_frame, end_frame) in enumerate(effects, 1):
        start_sample = max(
            0, start_frame * frame_size - round(args.pre_roll * sample_rate)
        )
        end_sample = min(
            len(audio), end_frame * frame_size + round(args.post_roll * sample_rate)
        )
        clip = audio[start_sample:end_sample].copy()

        # Short fades prevent clicks while remaining inaudible on normal SFX.
        fade_len = min(fade_samples, len(clip) // 2)
        if fade_len:
            clip[:fade_len] *= np.linspace(0.0, 1.0, fade_len, dtype=np.float32)[:, None]
            clip[-fade_len:] *= np.linspace(1.0, 0.0, fade_len, dtype=np.float32)[:, None]

        filename = f"sfx_{index:03d}.wav"
        sf.write(args.output_dir / filename, clip, sample_rate, subtype="PCM_16")

        peak = float(np.max(np.abs(clip)))
        clip_rms = float(np.sqrt(np.mean(clip * clip) + 1e-15))
        rows.append(
            {
                "index": index,
                "filename": filename,
                "source_start_sec": f"{start_sample / sample_rate:.3f}",
                "source_end_sec": f"{end_sample / sample_rate:.3f}",
                "duration_sec": f"{len(clip) / sample_rate:.3f}",
                "peak_dbfs": f"{db(peak):.2f}",
                "rms_dbfs": f"{db(clip_rms):.2f}",
            }
        )

    with manifest_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    print(f"Noise floor: {noise_floor_db:.2f} dBFS")
    print(f"Release threshold: {release_db:.2f} dBFS")
    print(f"Meaningful peak threshold: {meaningful_peak_db:.2f} dBFS")
    print(f"Created {len(rows)} clips in {args.output_dir}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
