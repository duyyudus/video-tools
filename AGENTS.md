# Repository Guidelines

## Project Structure & Module Organization
- `main.py` hosts the PySide6 desktop UI that wraps the CLI helpers and lets artists queue folders for img2vid / merge tasks.
- The CLI helpers now live under the `tools/` package (`tools/img2vid.py`, `tools/merge_vid.py`) so the GUI can import them directly; add new utilities in this package until we have enough shared logic for a dedicated library.
- `img2vid` stitches sequential images into a single MP4 by symlinking numbered files into a temporary workspace before invoking `ffmpeg`.
- `merge_vid` concatenates numbered video clips via an `ffmpeg` concat list and optional CUDA scaling.
- Store sample assets under `assets/` or a sibling folder outside the repo to keep version control lean.

## Build, Test, and Development Commands
- Use `uv` package manager, always work in venv.
- Use any recent CPython 3.10+ runtime. A lightweight virtual environment keeps dependencies isolated: `python -m venv .venv && source .venv/bin/activate`.
- Install runtime deps with `uv pip install -r requirements.txt` (PySide6 for the GUI) and ensure tooling availability before running: `ffmpeg -version` should succeed; install system packages if missing.
- Launch the GUI with `python main.py` to drive both pipelines interactively.
- Render image sequences via CLI: `python -m tools.img2vid frames/ output/ -f 24 -s 3840x2160 --cuda`.
- Merge clips via CLI: `python -m tools.merge_vid clips/ render/ --codec h264_nvenc --preset p4 --resolution 1920x1080`.

## Coding Style & Naming Conventions
- Follow existing scripts: 4-space indentation, explicit type hints, descriptive snake_case names, f-strings, and early validation errors with actionable messages.
- Group imports as stdlib only; avoid implicit relative imports.
- Prefer pure functions that accept `Path` objects and return values rather than mutating globals. Keep CLI logic inside `main()` for easy testing.

## Testing Guidelines
- No automated suite exists yet. Exercise scripts with small mock folders before submitting; verify boundary cases (empty folders, mixed extensions, incorrect numbering) and confirm `ffmpeg` exits 0.
- When adding helper functions, cover them with focused docstring examples or propose lightweight `pytest` modules under `tests/` following the filename pattern `test_<feature>.py`.

## Commit & Pull Request Guidelines
- Match the existing Conventional Commit style seen in history (e.g., `feat(video): add batch merge helper`). Keep subject lines under 72 characters and describe user value.
- Squash work into logical commits, reference related issues in the body, and mention required system dependencies or sample data updates.
- Pull requests should summarize the change, list validation commands, note any new CLI flags, and attach short logs or screenshots when ffmpeg output is relevant.
