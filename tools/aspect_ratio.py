#!/usr/bin/env python3
"""Adjust video aspect ratio by stretching or squashing horizontally using ffmpeg with CUDA acceleration."""

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

ASPECT_RATIOS = {
    "16:9": "16/9",
    "4:3": "4/3",
    "1:1": "1/1",
    "9:16": "9/16",
}


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Adjust video aspect ratio by stretching/squashing via ffmpeg + CUDA.",
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
            "Optional directory to write modified videos into; "
            "defaults to overwriting the input files."
        ),
    )
    parser.add_argument(
        "--ratio",
        "-r",
        choices=sorted(ASPECT_RATIOS.keys()),
        required=True,
        help="Target aspect ratio (e.g., 16:9, 4:3).",
    )
    parser.add_argument(
        "--preset",
        default="p7",
        help="NVENC preset to pass through to ffmpeg (default: p7).",
    )
    parser.add_argument(
        "--video-file",
        "-v",
        action="append",
        type=Path,
        dest="video_files",
        help=(
            "Specific video file to process; may be provided multiple times. "
            "Processed in addition to any input folder videos."
        ),
    )
    parser.add_argument(
        "--output-folder",
        "-o",
        type=Path,
        dest="output_folder",
        help=(
            "Explicit output directory to write modified videos into. Overrides "
            "the optional positional argument when provided."
        ),
    )
    return parser.parse_args(argv)


def ensure_ffmpeg_available() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg executable not found in PATH.")


def get_video_bitrate(path: Path) -> str | None:
    if shutil.which("ffprobe") is None:
        return None

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=bit_rate",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        process = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if process.returncode != 0:
            return None
        val = process.stdout.strip()
        return val if val and val != "N/A" else None
    except Exception:
        return None


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
    ratio: str,
    preset: str,
    bitrate: str | None = None,
) -> list[str]:
    ratio_val = ASPECT_RATIOS[ratio]
    # scale filter to stretch/squash: width = height * ratio
    # we use setdar to ensure the display aspect ratio is also correct
    # The scale filter here uses CUDA-accelerated scale_npp if possible, 
    # but for simplicity and compatibility with various ffmpeg builds, 
    # we'll use the standard scale filter or just let nvenc handle it if we can.
    # To keep it simple and consistent with rotate_vid.py, we'll use:
    # -vf "scale=iw*sar:ih,setsar=1,scale=ih*RATIO:ih" (this is a bit complex)
    # Simpler: -vf "scale=ih*(RATIO):ih,setdar=RATIO"
    
    vf = f"scale=ih*({ratio_val}):ih,setdar={ratio_val}"
    
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "info",
        "-i",
        str(input_path),
        "-vf",
        vf,
        "-c:v",
        "h264_nvenc",
        "-preset",
        preset,
    ]
    if bitrate:
        cmd.extend(["-b:v", bitrate])

    cmd.extend([
        "-c:a",
        "copy",
        str(output_path),
    ])
    return cmd


def adjust_aspect_ratio(src: Path, dst: Path, ratio: str, preset: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    bitrate = get_video_bitrate(src)
    cmd = build_ffmpeg_command(src, dst, ratio, preset, bitrate)
    process = subprocess.run(cmd, check=False)
    if process.returncode != 0:
        dst.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg failed while processing '{src.name}'.")


def derive_target_path(src: Path, output_dir: Path | None) -> Path:
    if output_dir is None:
        return src
    return output_dir / src.name


def derive_temp_path(original: Path) -> Path:
    return original.with_name(f"{original.stem}.processing{original.suffix}")


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
        adjust_aspect_ratio(video, temp_target, args.ratio, args.preset)
        if temp_target != final_target:
            temp_target.replace(final_target)
        processed += 1
        print(f"Processed {video.name} -> {final_target}")

    print(f"Successfully processed {processed} file(s).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Exception as exc:  # pragma: no cover - CLI error surface
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
