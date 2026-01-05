#!/usr/bin/env python3
"""Combine a folder of numbered images into a video using ffmpeg."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterable, List, Sequence


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
SEQUENCE_REGEX = re.compile(r"(\d{4,})")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Combine sequentially numbered images into an MP4 video via ffmpeg.",
    )
    parser.add_argument(
        "input_folder",
        type=Path,
        help="Directory containing the source images (expects 0001, 0002 ... pattern).",
    )
    parser.add_argument(
        "output_folder",
        type=Path,
        help="Directory to write the rendered video into.",
    )
    parser.add_argument(
        "--framerate",
        "-f",
        type=float,
        default=2.0,
        help="Frames per second for the output video (default: 2).",
    )
    parser.add_argument(
        "--resolution",
        "-s",
        default="3840x2160",
        help="Output resolution as WIDTHxHEIGHT (default: 3840x2160).",
    )
    parser.add_argument(
        "--cuda",
        action="store_true",
        help="Encode with NVIDIA NVENC (CUDA) for faster hardware acceleration.",
    )
    return parser.parse_args(argv)


def ensure_ffmpeg_available() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg executable not found in PATH.")


def parse_resolution(res_string: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)x(\d+)", res_string)
    if not match:
        raise ValueError(f"Invalid resolution '{res_string}'. Use WIDTHxHEIGHT format.")
    width, height = map(int, match.groups())
    if width <= 0 or height <= 0:
        raise ValueError("Resolution values must be positive integers.")
    return width, height


def iter_sequence_images(folder: Path) -> Iterable[Path]:
    for path in sorted(folder.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        if not SEQUENCE_REGEX.search(path.stem):
            continue
        yield path


def collect_images(folder: Path) -> List[Path]:
    matches = list(iter_sequence_images(folder))
    def sort_key(path: Path) -> tuple[int, str]:
        nums = [int(group) for group in SEQUENCE_REGEX.findall(path.stem)]
        number = nums[-1] if nums else -1
        return number, path.name

    matches.sort(key=sort_key)
    return matches


def make_linked_sequence(images: Sequence[Path], tmp_dir: Path) -> tuple[str, int]:
    suffixes = {img.suffix.lower() for img in images}
    if len(suffixes) != 1:
        raise ValueError("All images must share the same file extension for ffmpeg input.")

    extension = suffixes.pop()
    padding = max(4, len(str(len(images))))

    for idx, src in enumerate(images, start=1):
        link_name = f"{idx:0{padding}d}{extension}"
        dst = tmp_dir / link_name
        if dst.exists():
            dst.unlink()
        try:
            os.symlink(src, dst)
        except OSError:
            shutil.copy2(src, dst)

    pattern = str(tmp_dir / f"%0{padding}d{extension}")
    return pattern, padding


def build_ffmpeg_command(
    pattern: str,
    framerate: float,
    width: int,
    height: int,
    output_path: Path,
    use_cuda: bool,
) -> list[str]:
    scale_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"  # shrink/grow
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
    )
    codec = "h264_nvenc" if use_cuda else "libx264"

    return [
        "ffmpeg",
        "-y",
        "-framerate",
        str(framerate),
        "-i",
        pattern,
        "-vf",
        scale_filter,
        "-c:v",
        codec,
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]


def derive_output_path(input_folder: Path, output_dir: Path) -> Path:
    video_name = f"{input_folder.resolve().name}.mp4"
    return output_dir / video_name


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    ensure_ffmpeg_available()

    input_dir = args.input_folder.expanduser().resolve()
    output_dir = args.output_folder.expanduser().resolve()

    if not input_dir.exists() or not input_dir.is_dir():
        raise FileNotFoundError(f"Input folder '{input_dir}' does not exist or is not a directory.")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = derive_output_path(input_dir, output_dir)

    images = collect_images(input_dir)
    if not images:
        print(f"Info: No images with a #### numbering pattern found in {input_dir}. Skipping.")
        return 0
    width, height = parse_resolution(args.resolution)

    with TemporaryDirectory() as tmp:
        pattern, _ = make_linked_sequence(images, Path(tmp))
        cmd = build_ffmpeg_command(
            pattern,
            args.framerate,
            width,
            height,
            output_path,
            args.cuda,
        )
        process = subprocess.run(cmd, check=False)

    if process.returncode != 0:
        raise RuntimeError("ffmpeg failed; see its output above for details.")

    print(f"Video written to {output_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Exception as exc:  # pragma: no cover - CLI error surface
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
