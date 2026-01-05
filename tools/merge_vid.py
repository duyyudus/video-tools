#!/usr/bin/env python3
"""Merge sequentially numbered videos into a single MP4 using ffmpeg + CUDA."""

from __future__ import annotations

import argparse
import re
import shlex
import shutil
import subprocess
import sys
from collections import deque
from pathlib import Path
from tempfile import NamedTemporaryFile
from textwrap import dedent
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
FFMPEG_LOG_TAIL_LINES = 20


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Combine sequentially numbered video files (0001, 0002, …) into one MP4."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=dedent(
            """Examples:
  python -m tools.merge_vid clips/ render/
  python -m tools.merge_vid clips/ render/ --resolution 3840x2160\\
    --codec h264_nvenc --preset p4 --fallback-codec libx264
"""
        ),
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
        default="1920x1080",
        help=(
            "Output resolution WIDTHxHEIGHT used when clips differ in size (default: "
            "1920x1080). Scaling preserves aspect ratio and adds padding when required."
        ),
    )
    parser.add_argument(
        "--codec",
        default="h264_nvenc",
        help="NVENC codec to use (default: h264_nvenc).",
    )
    parser.add_argument(
        "--preset",
        default=None,
        help=(
            "Preset value passed to ffmpeg. Defaults to 'p4' for NVENC codecs and "
            "falls back to the codec's built-in default otherwise."
        ),
    )
    parser.add_argument(
        "--fallback-codec",
        default="libx264",
        help=(
            "Codec to try if the primary encode fails (default: libx264). "
            "Use 'none' to disable automatic fallback."
        ),
    )
    return parser.parse_args(argv)


def ensure_ffmpeg_available() -> None:
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        raise RuntimeError(
            f"{', '.join(missing)} executable(s) not found in PATH; install ffmpeg bundle."
        )


def resolve_preset(codec: str, explicit: Optional[str]) -> Optional[str]:
    if explicit:
        return explicit
    if codec.lower().endswith("_nvenc"):
        return "p4"
    return None


def normalize_fallback_codec(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in {"", "none", "false", "off"}:
        return None
    return value


def parse_resolution(resolution: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)x(\d+)", resolution)
    if not match:
        raise ValueError("Resolution must be formatted as WIDTHxHEIGHT, e.g. 1920x1080.")
    width, height = map(int, match.groups())
    if width <= 0 or height <= 0:
        raise ValueError("Resolution values must be positive.")
    return width, height


def probe_video_resolution(video: Path) -> tuple[int, int]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=s=x:p=0",
        str(video),
    ]
    proc = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "Unable to detect source video resolution via ffprobe; "
            f"ffprobe output:\n{proc.stderr.strip() or proc.stdout.strip()}"
        )
    value = proc.stdout.strip()
    try:
        return parse_resolution(value)
    except ValueError as exc:
        raise RuntimeError(
            f"ffprobe returned unexpected resolution '{value}' for {video}"
        ) from exc


def detect_uniform_resolution(videos: Sequence[Path]) -> Optional[tuple[int, int]]:
    """Return the common resolution shared by all videos or None when mixed."""

    reference: Optional[tuple[int, int]] = None
    for video in videos:
        current = probe_video_resolution(video)
        if reference is None:
            reference = current
            continue
        if current != reference:
            return None
    return reference


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
    matches.sort(key=lambda item: (item[0], item[1].name))
    return [path for _, path in matches]


def build_concat_file(videos: Sequence[Path]) -> str:
    tmp = NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    with tmp:
        for video in videos:
            escaped = str(video).replace("'", "'\\''")
            tmp.write(f"file '{escaped}'\n")
    return tmp.name


def build_resize_filter(
    resolution: Optional[tuple[int, int]], use_cuda_scale: bool
) -> tuple[Optional[str], bool]:
    """Construct the filter graph to scale and letterbox clips for mixed AR content."""

    if resolution is None:
        return None, False

    width, height = resolution
    pad_filter = f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
    if use_cuda_scale:
        # Resize on the GPU, download to system memory for padding, then convert.
        aspect_ratio = width / height
        scale_filter = (
            "scale_cuda="
            f"w='if(gt(iw/ih,{aspect_ratio:.6f}),{width},-2)':"
            f"h='if(gt(iw/ih,{aspect_ratio:.6f}),-2,{height})'"
        )
        filters = [
            scale_filter,
            "hwdownload",
            "format=nv12",
            pad_filter,
            "format=yuv420p",
            "setsar=1",
        ]
        return ",".join(filters), True

    scale_filter = f"scale={width}:{height}:force_original_aspect_ratio=decrease"
    filters = [scale_filter, pad_filter, "setsar=1"]
    return ",".join(filters), False


def build_ffmpeg_command(
    concat_file: str,
    output_path: Path,
    codec: str,
    preset: Optional[str],
    resolution: Optional[tuple[int, int]],
    use_cuda_scale: bool,
) -> list[str]:
    use_cuda_codec = codec.lower().endswith("_nvenc")
    filter_graph, filter_needs_hw_frames = build_resize_filter(resolution, use_cuda_scale)
    cmd: list[str] = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "info",
    ]
    needs_hw_frames = use_cuda_codec and filter_needs_hw_frames
    if use_cuda_codec:
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
        ]
    )
    if preset:
        cmd.extend(["-preset", preset])
    if filter_graph:
        cmd.extend(["-vf", filter_graph])
    if not use_cuda_codec:
        cmd.extend(["-pix_fmt", "yuv420p"])
    cmd.extend(
        [
            "-c:a",
            "copy",
            str(output_path),
        ]
    )
    return cmd


def run_ffmpeg(cmd: Sequence[str]) -> tuple[int, List[str]]:
    """Run ffmpeg, streaming logs to stdout and keeping a short tail."""

    print("Running ffmpeg command:")
    print(f"  {shlex.join(str(part) for part in cmd)}")
    print("")

    log_tail: deque[str] = deque(maxlen=FFMPEG_LOG_TAIL_LINES)
    last_char = "\n"
    printed = False
    with subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        universal_newlines=True,
    ) as proc:
        stdout = proc.stdout
        if stdout is None:
            raise RuntimeError("Could not capture ffmpeg output stream.")
        try:
            for raw_line in stdout:
                printed = True
                if raw_line:
                    last_char = raw_line[-1]
                sys.stdout.write(raw_line)
                sys.stdout.flush()
                log_tail.append(raw_line.rstrip("\r\n"))
        except KeyboardInterrupt:
            proc.terminate()
            proc.wait()
            raise
        return_code = proc.wait()

    if printed and last_char not in ("\n", "\r"):
        sys.stdout.write("\n")
        sys.stdout.flush()

    return return_code, list(log_tail)


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
    if not videos:
        print(f"Info: No video files matching the #### pattern were found in {input_dir}. Skipping.")
        return 0

    uniform_resolution = detect_uniform_resolution(videos)
    if uniform_resolution is not None:
        target_resolution = uniform_resolution
    else:
        target_resolution = parse_resolution(args.resolution)

    concat_file = build_concat_file(videos)
    output_path = derive_output_path(input_dir, output_dir)
    fallback_codec = normalize_fallback_codec(args.fallback_codec)
    codecs_to_try: List[str] = [args.codec]
    if fallback_codec and fallback_codec not in codecs_to_try:
        codecs_to_try.append(fallback_codec)
    last_error: Optional[tuple[int, List[str]]] = None

    try:
        for codec_index, codec_name in enumerate(codecs_to_try):
            use_cuda_codec = codec_name.lower().endswith("_nvenc")
            preset = resolve_preset(codec_name, args.preset)
            prefer_cuda_scale = bool(target_resolution) and use_cuda_codec
            scale_modes = [True, False] if prefer_cuda_scale else [False]

            for use_cuda_scale in scale_modes:
                cmd = build_ffmpeg_command(
                    concat_file,
                    output_path,
                    codec_name,
                    preset,
                    target_resolution,
                    use_cuda_scale,
                )
                return_code, log_tail = run_ffmpeg(cmd)
                if return_code == 0:
                    if codec_index > 0:
                        print(
                            f"Fallback codec '{codec_name}' succeeded after primary codec failure."
                        )
                    print(f"Merged video written to {output_path}")
                    return 0

                last_error = (return_code, log_tail)
                if use_cuda_scale and len(scale_modes) > 1:
                    sys.stderr.write(
                        "ffmpeg failed while using CUDA scaling; retrying with CPU scaling...\n"
                    )
                    sys.stderr.flush()

            if codec_index < len(codecs_to_try) - 1:
                sys.stderr.write(
                    "ffmpeg failed while using codec "
                    f"'{codec_name}'; retrying with fallback codec "
                    f"'{codecs_to_try[codec_index + 1]}'...\n"
                )
                sys.stderr.flush()
    finally:
        Path(concat_file).unlink(missing_ok=True)

    if last_error is None:
        raise RuntimeError("ffmpeg exited unexpectedly without reporting an error.")

    return_code, log_tail = last_error
    tail_excerpt = "\n".join(log_tail) if log_tail else "(ffmpeg produced no output)"
    raise RuntimeError(
        "ffmpeg failed with exit code "
        f"{return_code}. Last log lines:\n{tail_excerpt}"
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Exception as exc:  # pragma: no cover - CLI error surface
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
