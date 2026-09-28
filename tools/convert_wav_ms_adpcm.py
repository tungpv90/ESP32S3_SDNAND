#!/usr/bin/env python3
"""Convert WAV files to mono RIFF/WAVE Microsoft ADPCM at a target rate."""

from __future__ import annotations

import argparse
import csv
import math
import struct
from pathlib import Path

import numpy as np
import soundfile as sf

from anim_pack import ADAPT_COEF1, ADAPT_COEF2, encode_ms_adpcm


def sinc_resample(
    signal: np.ndarray,
    source_rate: int,
    target_rate: int,
    half_taps: int = 32,
    beta: float = 8.6,
) -> np.ndarray:
    """Band-limited resampling with a fractional-delay Kaiser-windowed sinc."""
    if source_rate == target_rate:
        return signal.astype(np.float32, copy=True)

    ratio = target_rate / source_rate
    output_length = round(len(signal) * ratio)
    padded = np.pad(signal.astype(np.float64), (half_taps, half_taps), mode="reflect")
    offsets = np.arange(-half_taps + 1, half_taps + 1, dtype=np.int64)
    window_denominator = np.i0(beta)
    output = np.empty(output_length, dtype=np.float32)

    # Chunking bounds the temporary matrix size for long recordings.
    for first in range(0, output_length, 8192):
        last = min(first + 8192, output_length)
        positions = np.arange(first, last, dtype=np.float64) / ratio
        centers = np.floor(positions).astype(np.int64)
        indices = centers[:, None] + offsets[None, :]
        distances = positions[:, None] - indices

        # ratio is the normalized low-pass bandwidth when downsampling.
        cutoff = min(1.0, ratio) * 0.96
        normalized = distances / half_taps
        window = np.i0(
            beta * np.sqrt(np.maximum(0.0, 1.0 - normalized * normalized))
        ) / window_denominator
        weights = cutoff * np.sinc(cutoff * distances) * window
        weights /= np.sum(weights, axis=1, keepdims=True)

        samples = padded[indices + half_taps]
        output[first:last] = np.sum(samples * weights, axis=1)

    return np.clip(output, -1.0, 1.0)


def wave_format_tag(path: Path) -> int:
    with path.open("rb") as handle:
        if handle.read(4) != b"RIFF":
            raise ValueError(f"{path} is not RIFF")
        handle.seek(8)
        if handle.read(4) != b"WAVE":
            raise ValueError(f"{path} is not WAVE")
        while chunk_header := handle.read(8):
            if len(chunk_header) != 8:
                break
            chunk_id, chunk_size = struct.unpack("<4sI", chunk_header)
            if chunk_id == b"fmt ":
                return struct.unpack("<H", handle.read(2))[0]
            handle.seek(chunk_size + (chunk_size & 1), 1)
    raise ValueError(f"{path} has no fmt chunk")


def build_ms_adpcm_wave(
    signal: np.ndarray, sample_rate: int, block_align: int
) -> bytes:
    pcm = np.clip(np.rint(signal * 32768.0), -32768, 32767).astype("<i2")
    data, samples_per_block, encoded_samples = encode_ms_adpcm(
        pcm.tolist(), block_align, search=True
    )

    coefficients: list[int] = []
    for first, second in zip(ADAPT_COEF1, ADAPT_COEF2):
        coefficients.extend((first & 0xFFFF, second & 0xFFFF))
    fmt = struct.pack(
        "<HHIIHHHHH" + "H" * 14,
        0x0002,
        1,
        sample_rate,
        (sample_rate * block_align) // samples_per_block,
        block_align,
        4,
        32,
        samples_per_block,
        7,
        *coefficients,
    )
    fact = struct.pack("<I", encoded_samples)
    body = (
        b"WAVE"
        + b"fmt "
        + struct.pack("<I", len(fmt))
        + fmt
        + b"fact"
        + struct.pack("<I", len(fact))
        + fact
        + b"data"
        + struct.pack("<I", len(data))
        + data
        + (b"\x00" if len(data) & 1 else b"")
    )
    return b"RIFF" + struct.pack("<I", len(body)) + body


def db(value: float) -> float:
    return 20.0 * math.log10(max(value, 1e-12))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--rate", type=int, default=16000)
    parser.add_argument("--block-align", type=int, default=256)
    parser.add_argument(
        "--preview-dir",
        type=Path,
        help="optional directory for universally playable PCM-16 previews",
    )
    args = parser.parse_args()

    inputs = sorted(args.input_dir.glob("*.wav"))
    if not inputs:
        raise SystemExit(f"No WAV files found in {args.input_dir}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.preview_dir:
        args.preview_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str | int]] = []

    for index, source in enumerate(inputs, 1):
        audio, source_rate = sf.read(source, always_2d=True, dtype="float32")
        mono = np.mean(audio, axis=1, dtype=np.float64).astype(np.float32)
        converted = sinc_resample(mono, source_rate, args.rate)
        destination = args.output_dir / source.name
        destination.write_bytes(
            build_ms_adpcm_wave(converted, args.rate, args.block_align)
        )
        if args.preview_dir:
            sf.write(
                args.preview_dir / source.name,
                converted,
                args.rate,
                format="WAV",
                subtype="PCM_16",
            )

        info = sf.info(destination)
        format_tag = wave_format_tag(destination)
        raw = destination.read_bytes()
        fmt_offset = raw.find(b"fmt ")
        block_align = struct.unpack_from("<H", raw, fmt_offset + 20)[0]
        if (
            info.samplerate != args.rate
            or info.channels != 1
            or info.subtype != "MS_ADPCM"
            or format_tag != 0x0002
            or block_align != args.block_align
        ):
            raise RuntimeError(f"Output validation failed: {destination}\n{info}")

        decoded, _ = sf.read(destination, dtype="float32")
        rows.append(
            {
                "index": index,
                "filename": source.name,
                "duration_sec": f"{info.frames / info.samplerate:.3f}",
                "sample_rate_hz": info.samplerate,
                "channels": info.channels,
                "codec": info.subtype,
                "riff_format_tag": f"0x{format_tag:04X}",
                "block_align": block_align,
                "decoded_peak_dbfs": f"{db(float(np.max(np.abs(decoded)))):.2f}",
                "file_size_bytes": destination.stat().st_size,
            }
        )
        print(f"[{index:03d}/{len(inputs):03d}] {source.name}")

    manifest_path = args.output_dir / "manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    print(f"Created {len(rows)} files in {args.output_dir}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
