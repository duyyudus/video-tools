#!/usr/bin/env python3
"""Rotate all video files in a folder using ffmpeg with CUDA acceleration."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Sequence


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

ROTATION_MAP = {
    "clockwise": "1",
    "counter-clockwise": "2",
}


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rotate every video in a folder 90 degrees via ffmpeg + CUDA.",
    )
    parser.add_argument(
        "input_folder",
        type=Path,
        nargs="?",
        help="Directory that stores the source video files.",
    )
    parser.add_argument(
        "output_folder_pos",
        type=Path,
        nargs="?",
        metavar="output_folder",
        help=(
            "Optional directory to write rotated videos into; "
            "defaults to overwriting the input files."
        ),
    )
    parser.add_argument(
        "--rotation",
        "-r",
        choices=sorted(ROTATION_MAP.keys()),
        required=True,
        help="Rotation direction (clockwise or counter-clockwise).",
    )
    parser.add_argument(
        "--preset",
        default="p4",
        help="NVENC preset to pass through to ffmpeg (default: p4).",
    )
    parser.add_argument(
        "--video-file",
        "-v",
        action="append",
        type=Path,
        dest="video_files",
        help=(
            "Specific video file to rotate; may be provided multiple times. "
            "Processed in addition to any input folder videos."
        ),
    )
    parser.add_argument(
        "--output-folder",
        "-o",
        type=Path,
        dest="output_folder",
        help=(
            "Explicit output directory to write rotated videos into. Overrides "
            "the optional positional argument when provided."
        ),
    )
    return parser.parse_args(argv)


def ensure_ffmpeg_available() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg executable not found in PATH.")


def iter_video_files(folder: Path) -> Iterable[Path]:
    for path in sorted(folder.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        yield path


def build_ffmpeg_command(
    input_path: Path,
    output_path: Path,
    rotation: str,
    preset: str,
) -> list[str]:
    transpose_value = ROTATION_MAP[rotation]
    return [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "info",
        "-i",
        str(input_path),
        "-vf",
        f"transpose={transpose_value}",
        "-c:v",
        "h264_nvenc",
        "-preset",
        preset,
        "-c:a",
        "copy",
        str(output_path),
    ]


def rotate_video(src: Path, dst: Path, rotation: str, preset: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = build_ffmpeg_command(src, dst, rotation, preset)
    process = subprocess.run(cmd, check=False)
    if process.returncode != 0:
        dst.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg failed while rotating '{src.name}'.")


def derive_target_path(src: Path, output_dir: Path | None) -> Path:
    if output_dir is None:
        return src
    return output_dir / src.name


def derive_temp_path(original: Path) -> Path:
    return original.with_name(f"{original.stem}.rotating{original.suffix}")


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    ensure_ffmpeg_available()

    if args.input_folder is None and not args.video_files:
        raise FileNotFoundError(
            "Provide an input folder, at least one --video-file, or both."
        )

    folder_videos: list[Path] = []
    input_dir: Path | None = None
    if args.input_folder is not None:
        input_dir = args.input_folder.expanduser().resolve()
        if not input_dir.exists() or not input_dir.is_dir():
            raise FileNotFoundError(
                f"Input folder '{input_dir}' does not exist or is not a directory."
            )
        folder_videos = list(iter_video_files(input_dir))

    output_dir = None
    chosen_output = args.output_folder or args.output_folder_pos
    if chosen_output:
        output_dir = chosen_output.expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

    direct_videos: list[Path] = []
    for video_path in args.video_files or []:
        resolved = video_path.expanduser().resolve()
        if not resolved.exists() or not resolved.is_file():
            raise FileNotFoundError(
                f"Video file '{resolved}' does not exist or is not a file."
            )
        if resolved.suffix.lower() not in VIDEO_EXTENSIONS:
            raise ValueError(
                f"Video file '{resolved.name}' does not have a supported extension."
            )
        direct_videos.append(resolved)

    videos: list[Path] = []
    seen: set[Path] = set()
    for video in folder_videos + direct_videos:
        if video in seen:
            continue
        seen.add(video)
        videos.append(video)

    if not videos:
        if input_dir is not None:
            raise FileNotFoundError(
                f"No video files with supported extensions were found in {input_dir}."
            )
        raise FileNotFoundError("No valid video files were provided via --video-file.")

    processed = 0
    for video in videos:
        final_target = derive_target_path(video, output_dir)
        temp_target = final_target
        if final_target == video:
            temp_target = derive_temp_path(video)
        rotate_video(video, temp_target, args.rotation, args.preset)
        if temp_target != final_target:
            temp_target.replace(final_target)
        processed += 1
        print(f"Rotated {video.name} -> {final_target}")

    print(f"Successfully rotated {processed} file(s).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Exception as exc:  # pragma: no cover - CLI error surface
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
