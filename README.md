# Video Tools

Desktop + CLI helpers for stitching still frames into MP4s, merging numbered clips, and rotating footage with NVIDIA NVENC acceleration. The PySide6 app in `main.py` wraps the CLI utilities under `tools/` so artists can drag folders, set defaults, and queue renders without touching the terminal.

## Prerequisites
- Python 3.10 or newer.
- [uv](https://github.com/astral-sh/uv) for dependency installs (falls back to `pip` if unavailable).
- `ffmpeg` accessible on your `PATH` (`ffmpeg -version` should print build info).
- Optional: CUDA-capable GPU + NVIDIA drivers for the NVENC paths used by `img2vid`, `merge_vid`, and `rotate_vid`.

Sample input assets should live under `assets/` or outside the repo root to avoid bloating version control.

## Virtual Environment Setup
1. Create the environment: `uv venv`
2. Activate it:
   - macOS/Linux: `source .venv/bin/activate`
   - Windows (PowerShell): `./.venv/Scripts/activate.ps1`
3. Install dependencies with uv inside the venv: `uv pip install -r requirements.txt`
4. Verify tooling: `ffmpeg -version`

Keep the venv active whenever you run the GUI or CLI helpers so PySide6 and the shared utilities resolve correctly.

## Usage

### GUI workflow
Launch the desktop wrapper to batch queue folders for any pipeline:

```bash
python main.py
```

- Drag folders into the "Input folders" list or click **Add Folders…**.
- Set an output directory and tweak per-tab options (frame rate, resolution, codec, etc.).
- Hit **Run** on the relevant tab to process each folder sequentially; progress and errors surface in the status bar.

### CLI helpers
Run the modules directly for scripted workflows. All commands assume the venv is active.

#### Convert image sequences to MP4
```bash
python -m tools.img2vid frames/ output/ -f 24 -s 3840x2160 --cuda
```
- `frames/` must contain sequentially numbered stills (0001, 0002, …).
- `output/` is created if missing; the resulting file adopts the source folder name.
- Use `--cuda` to encode with `h264_nvenc`; omit it for CPU `libx264`.

#### Merge numbered clips
```bash
python -m tools.merge_vid clips/ render/ --codec h264_nvenc --preset p4 --resolution 1920x1080
```
- Accepts `.mp4`, `.mov`, `.mkv`, and other common extensions as long as filenames include a zero-padded index.
- The command builds an ffmpeg concat list, keeps the audio stream intact, and optionally resizes with CUDA-aware scaling.

#### Rotate videos in bulk
```bash
python -m tools.rotate_vid footage/ rotated/ --rotation clockwise --preset p4
```
- Provide a folder, explicit `--video-file` paths, or both. Missing `rotated/` is created automatically.
- Use `--rotation counter-clockwise` for the opposite direction; videos overwrite in-place when no output folder is supplied.

## Validation tips
- Test new folders with a handful of numbered assets before committing larger renders.
- Watch for non-uniform padding or mixed extensions; the helpers fail fast with actionable error messages so issues can be fixed before ffmpeg runs long jobs.
- Keep ffmpeg logs from the terminal handy when filing bug reports or PRs.

