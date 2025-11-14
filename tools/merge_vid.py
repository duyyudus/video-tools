#!/usr/bin/env python3
"""Merge sequentially numbered videos into a single MP4 using ffmpeg + CUDA."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import List, Optional, Sequence

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".m4v",
    ".webm",
    ".mpg",
    ".mpeg",
    ".mts",
    ".m2ts",
    ".ts",
}
SEQUENCE_REGEX = re.compile(r"(0\d{3,})")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Combine sequentially numbered video files (0001, 0002, …) into one MP4."
        )
    )
    parser.add_argument(
        "input_folder",
        type=Path,
        help="Folder that stores numbered video clips to merge.",
    )
    parser.add_argument(
        "output_folder",
        type=Path,
        help="Folder where the merged video should be written.",
    )
    parser.add_argument(
        "--resolution",
        "-s",
        help="Optional output resolution WIDTHxHEIGHT; defaults to inheriting input resolution.",
    )
    parser.add_argument(
        "--codec",
        default="h264_nvenc",
        help="NVENC codec to use (default: h264_nvenc).",
    )
    parser.add_argument(
        "--preset",
        default="p4",
        help="NVENC preset to pass to ffmpeg (default: p4).",
    )
    return parser.parse_args(argv)


def ensure_ffmpeg_available() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg executable not found in PATH.")


def parse_resolution(resolution: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)x(\d+)", resolution)
    if not match:
        raise ValueError("Resolution must be formatted as WIDTHxHEIGHT, e.g. 1920x1080.")
    width, height = map(int, match.groups())
    if width <= 0 or height <= 0:
        raise ValueError("Resolution values must be positive.")
    return width, height


def extract_sequence_number(stem: str) -> Optional[int]:
    found = SEQUENCE_REGEX.findall(stem)
    if not found:
        return None
    try:
        return int(found[-1])
    except ValueError:
        return None


def find_numbered_videos(folder: Path) -> List[Path]:
    matches: List[tuple[int, Path]] = []
    for entry in sorted(folder.iterdir()):
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        number = extract_sequence_number(entry.stem)
        if number is None:
            continue
        matches.append((number, entry))
    if not matches:
        raise FileNotFoundError(
            f"No video files matching the #### pattern were found in {folder}."
        )
    matches.sort(key=lambda item: (item[0], item[1].name))
    return [path for _, path in matches]


def build_concat_file(videos: Sequence[Path]) -> str:
    tmp = NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    with tmp:
        for video in videos:
            escaped = str(video).replace("'", "'\\''")
            tmp.write(f"file '{escaped}'\n")
    return tmp.name


def build_ffmpeg_command(
    concat_file: str,
    output_path: Path,
    codec: str,
    preset: str,
    resolution: Optional[tuple[int, int]],
) -> list[str]:
    use_cuda = codec.lower().endswith("_nvenc")
    cmd: list[str] = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "info",
    ]
    needs_hw_frames = use_cuda and resolution is not None
    if use_cuda:
        # Must precede the input specification so ffmpeg treats it as an input option.
        cmd.extend(["-hwaccel", "cuda"])
    if needs_hw_frames:
        cmd.extend(["-hwaccel_output_format", "cuda"])
    cmd.extend(
        [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_file,
            "-c:v",
            codec,
            "-preset",
            preset,
        ]
    )
    if resolution:
        width, height = resolution
        if use_cuda:
            cmd.extend(["-vf", f"scale_cuda={width}:{height}"])
        else:
            cmd.extend(["-vf", f"scale={width}:{height}"])
    if not use_cuda:
        cmd.extend(["-pix_fmt", "yuv420p"])
    cmd.extend(
        [
            "-c:a",
            "copy",
            str(output_path),
        ]
    )
    return cmd


def derive_output_path(input_folder: Path, output_folder: Path) -> Path:
    return output_folder / f"{input_folder.resolve().name}.mp4"


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    ensure_ffmpeg_available()

    input_dir = args.input_folder.expanduser().resolve()
    output_dir = args.output_folder.expanduser().resolve()
    if not input_dir.exists() or not input_dir.is_dir():
        raise FileNotFoundError(
            f"Input folder '{input_dir}' does not exist or is not a directory."
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    videos = find_numbered_videos(input_dir)

    target_resolution = parse_resolution(args.resolution) if args.resolution else None

    concat_file = build_concat_file(videos)
    output_path = derive_output_path(input_dir, output_dir)

    try:
        cmd = build_ffmpeg_command(
            concat_file,
            output_path,
            args.codec,
            args.preset,
            target_resolution,
        )
        process = subprocess.run(cmd, check=False)
    finally:
        Path(concat_file).unlink(missing_ok=True)

    if process.returncode != 0:
        raise RuntimeError("ffmpeg failed; review the log above for details.")

    print(f"Merged video written to {output_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Exception as exc:  # pragma: no cover - CLI error surface
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
