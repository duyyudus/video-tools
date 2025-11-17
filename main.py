#!/usr/bin/env python3
"""PySide6 GUI wrapper around img2vid.py and merge_vid.py."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Iterable

from tools import img2vid, merge_vid, rotate_vid
from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStatusBar,
    QTabWidget,
    QTreeView,
    QVBoxLayout,
    QWidget,
)


IMG2VID_DEFAULT_FRAMERATE = 2.0
IMG2VID_DEFAULT_RESOLUTION = "3840x2160"
MERGE_DEFAULT_CODEC = "h264_nvenc"
MERGE_DEFAULT_PRESET = ""
MERGE_DEFAULT_FALLBACK_CODEC = "libx264"
MERGE_DEFAULT_RESOLUTION = "1920x1080"
BUTTON_HEIGHT_SCALE = 1.5
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

def scale_button_height(button: QPushButton, factor: float = BUTTON_HEIGHT_SCALE) -> None:
    """Increase the button height by the provided scale factor."""

    hint_height = button.sizeHint().height()
    button.setFixedHeight(int(hint_height * factor))


def select_directories(parent: QWidget) -> list[Path]:
    """Show a dialog that allows selecting multiple directories."""

    dialog = QFileDialog(parent, "Select Folders")
    dialog.setFileMode(QFileDialog.Directory)
    dialog.setOption(QFileDialog.DontUseNativeDialog, True)
    dialog.setOption(QFileDialog.ShowDirsOnly, True)

    for view in dialog.findChildren(QListView):
        view.setSelectionMode(QAbstractItemView.ExtendedSelection)
    for view in dialog.findChildren(QTreeView):
        view.setSelectionMode(QAbstractItemView.ExtendedSelection)

    if dialog.exec() == QFileDialog.Accepted:
        return [Path(path) for path in dialog.selectedFiles()]
    return []


class PathDropListWidget(QListWidget):
    """List widget that emits dropped filesystem paths."""

    paths_dropped = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        self.dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        paths: list[Path] = []
        for url in event.mimeData().urls():
            if url.isLocalFile():
                paths.append(Path(url.toLocalFile()))
        if paths:
            self.paths_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()


class FolderProcessingWorker(QObject):
    """Run a processing task for each folder on a background thread."""

    progress = Signal(int, int)
    status = Signal(str)
    error = Signal(str)
    finished = Signal()

    def __init__(
        self,
        folders: Iterable[Path],
        task: Callable[[Path], None],
    ) -> None:
        super().__init__()
        self._folders = list(folders)
        self._task = task

    @Slot()
    def run(self) -> None:
        total = len(self._folders)
        for index, folder in enumerate(self._folders, start=1):
            self.status.emit(f"Processing {folder.name}")
            try:
                self._task(folder)
            except Exception as exc:  # pragma: no cover - GUI thread surface
                self.error.emit(str(exc))
                self.finished.emit()
                return
            self.progress.emit(index, total)
        self.status.emit("All folders processed")
        self.finished.emit()


class BaseProcessingTab(QWidget):
    """Common UI for picking folders, outputs, and running tasks."""

    def __init__(self, run_label: str) -> None:
        super().__init__()
        self.worker_thread: QThread | None = None
        self.worker: FolderProcessingWorker | None = None

        self.input_list = PathDropListWidget()
        self.input_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.input_list.paths_dropped.connect(self.on_paths_dropped)

        self.add_button = QPushButton("Add Folders…")
        self.remove_button = QPushButton("Remove Selected")
        self.output_line = QLineEdit()
        self.output_line.setReadOnly(True)
        self.output_button = QPushButton("Choose Output Folder…")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.status_bar = QStatusBar()
        self.status_bar.showMessage("Idle")
        self.run_button = QPushButton(run_label)

        for button in (self.add_button, self.remove_button, self.output_button, self.run_button):
            scale_button_height(button)

        self.add_button.clicked.connect(self.add_folders)
        self.remove_button.clicked.connect(self.remove_selected)
        self.output_button.clicked.connect(self.choose_output)

        layout = QVBoxLayout(self)
        self.input_label = QLabel("Input folders")
        layout.addWidget(self.input_label)
        layout.addWidget(self.input_list)

        self.button_row = QHBoxLayout()
        self.button_row.addWidget(self.add_button)
        self.button_row.addWidget(self.remove_button)
        layout.addLayout(self.button_row)

        layout.addWidget(QLabel("Output folder"))
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_line)
        output_row.addWidget(self.output_button)
        layout.addLayout(output_row)

        self.extra_controls_layout = QVBoxLayout()
        layout.addLayout(self.extra_controls_layout)

        layout.addWidget(self.progress_bar)
        layout.addWidget(self.status_bar)
        layout.addWidget(self.run_button)

    def additional_disable_widgets(self) -> list[QWidget]:
        return []

    def add_folders(self) -> None:
        for folder in select_directories(self):
            self._add_folder(folder)

    def on_paths_dropped(self, paths: list[Path]) -> None:
        for path in paths:
            if self.accepts_dropped_path(path):
                self._add_folder(path)

    def accepts_dropped_path(self, path: Path) -> bool:
        return path.is_dir()

    def _add_folder(self, folder: Path) -> None:
        existing = {self.input_list.item(i).data(Qt.UserRole) for i in range(self.input_list.count())}
        if str(folder) in existing:
            return
        item = QListWidgetItem(str(folder))
        item.setData(Qt.UserRole, str(folder))
        self.input_list.addItem(item)

    def remove_selected(self) -> None:
        for item in self.input_list.selectedItems():
            row = self.input_list.row(item)
            self.input_list.takeItem(row)

    def choose_output(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if directory:
            self.output_line.setText(directory)

    def selected_folders(self) -> list[Path]:
        folders: list[Path] = []
        for idx in range(self.input_list.count()):
            data = self.input_list.item(idx).data(Qt.UserRole)
            if isinstance(data, str):
                folders.append(Path(data))
        return folders

    def output_directory(self) -> Path | None:
        text = self.output_line.text().strip()
        return Path(text) if text else None

    def set_running(self, running: bool) -> None:
        widgets = [
            self.add_button,
            self.remove_button,
            self.output_button,
            self.input_list,
            self.run_button,
            *self.additional_disable_widgets(),
        ]
        for widget in widgets:
            widget.setEnabled(not running)

    def start_worker(self, folders: list[Path], task: Callable[[Path], None]) -> None:
        self.progress_bar.setRange(0, len(folders))
        self.progress_bar.setValue(0)
        self.status_bar.showMessage("Starting…")

        worker = FolderProcessingWorker(folders, task)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self.on_worker_progress)
        worker.status.connect(self.status_bar.showMessage)
        worker.error.connect(self.on_worker_error)
        worker.finished.connect(self.on_worker_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self.worker_thread = thread
        self.worker = worker
        self.set_running(True)
        thread.start()

    @Slot(int, int)
    def on_worker_progress(self, current: int, total: int) -> None:
        self.progress_bar.setMaximum(max(total, 1))
        self.progress_bar.setValue(current)

    @Slot(str)
    def on_worker_error(self, message: str) -> None:
        self.status_bar.showMessage(f"Error: {message}")
        QMessageBox.critical(self, "Processing failed", message)

    @Slot()
    def on_worker_finished(self) -> None:
        self.set_running(False)
        self.worker_thread = None
        self.worker = None


class ImagesToVideoTab(BaseProcessingTab):
    def __init__(self) -> None:
        super().__init__("Run")
        self.framerate_input = QLineEdit(str(IMG2VID_DEFAULT_FRAMERATE))
        self.resolution_input = QLineEdit(IMG2VID_DEFAULT_RESOLUTION)

        self.extra_controls_layout.addWidget(QLabel("Framerate (fps)"))
        self.extra_controls_layout.addWidget(self.framerate_input)
        self.extra_controls_layout.addWidget(QLabel("Resolution (WIDTHxHEIGHT)"))
        self.extra_controls_layout.addWidget(self.resolution_input)

        self.run_button.clicked.connect(self.run_processing)

    def additional_disable_widgets(self) -> list[QWidget]:
        return [self.framerate_input, self.resolution_input]

    def run_processing(self) -> None:
        folders = self.selected_folders()
        if not folders:
            QMessageBox.warning(self, "Missing folders", "Add at least one input folder.")
            return
        output_dir = self.output_directory()
        if output_dir is None:
            QMessageBox.warning(self, "Missing output", "Choose an output folder.")
            return

        framerate_text = self.framerate_input.text().strip() or str(IMG2VID_DEFAULT_FRAMERATE)
        try:
            framerate = float(framerate_text)
        except ValueError:
            QMessageBox.warning(self, "Invalid framerate", "Enter a numeric framerate value.")
            return
        resolution = self.resolution_input.text().strip() or IMG2VID_DEFAULT_RESOLUTION

        def task(folder: Path) -> None:
            args = [
                str(folder),
                str(output_dir),
                "--framerate",
                str(framerate),
                "--resolution",
                resolution,
            ]
            img2vid.main(args)

        self.start_worker(folders, task)


class MergeVideosTab(BaseProcessingTab):
    def __init__(self) -> None:
        super().__init__("Run")
        self.codec_input = QLineEdit(MERGE_DEFAULT_CODEC)
        self.preset_input = QLineEdit(MERGE_DEFAULT_PRESET)
        self.preset_input.setPlaceholderText("Blank uses codec default (p4 for NVENC).")
        self.fallback_codec_input = QLineEdit(MERGE_DEFAULT_FALLBACK_CODEC)
        self.fallback_codec_input.setPlaceholderText("Enter 'none' to disable fallback.")
        self.resolution_input = QLineEdit(MERGE_DEFAULT_RESOLUTION)
        self.resolution_input.setPlaceholderText("WIDTHxHEIGHT, applied when clips differ.")

        codec_row = QHBoxLayout()
        codec_column = QVBoxLayout()
        codec_column.addWidget(QLabel("Video codec"))
        codec_column.addWidget(self.codec_input)
        fallback_column = QVBoxLayout()
        fallback_column.addWidget(QLabel("Fallback codec"))
        fallback_column.addWidget(self.fallback_codec_input)
        preset_column = QVBoxLayout()
        preset_column.addWidget(QLabel("Preset (optional)"))
        preset_column.addWidget(self.preset_input)
        resolution_column = QVBoxLayout()
        resolution_column.addWidget(QLabel("Resolution"))
        resolution_column.addWidget(self.resolution_input)
        for column in (codec_column, fallback_column, preset_column, resolution_column):
            codec_row.addLayout(column)
        self.extra_controls_layout.addLayout(codec_row)

        self.run_button.clicked.connect(self.run_processing)

    def additional_disable_widgets(self) -> list[QWidget]:
        return [
            self.codec_input,
            self.preset_input,
            self.fallback_codec_input,
            self.resolution_input,
        ]

    def run_processing(self) -> None:
        folders = self.selected_folders()
        if not folders:
            QMessageBox.warning(self, "Missing folders", "Add at least one input folder.")
            return
        output_dir = self.output_directory()
        if output_dir is None:
            QMessageBox.warning(self, "Missing output", "Choose an output folder.")
            return

        codec = self.codec_input.text().strip() or MERGE_DEFAULT_CODEC
        preset = self.preset_input.text().strip()
        fallback_codec = self.fallback_codec_input.text().strip()
        resolution = self.resolution_input.text().strip()

        def task(folder: Path) -> None:
            args: list[str] = [str(folder), str(output_dir), "--codec", codec]
            if resolution:
                args.extend(["--resolution", resolution])
            if preset:
                args.extend(["--preset", preset])
            if fallback_codec:
                args.extend(["--fallback-codec", fallback_codec])
            merge_vid.main(args)

        self.start_worker(folders, task)


class RotateVideosTab(BaseProcessingTab):
    def __init__(self) -> None:
        super().__init__("Run")
        self.input_label.setText("Input folders or videos")
        self.rotation_selector = QComboBox()
        self.rotation_selector.addItem("Clockwise", "clockwise")
        self.rotation_selector.addItem("Counter Clockwise", "counter-clockwise")
        self.add_videos_button = QPushButton("Add Videos…")
        scale_button_height(self.add_videos_button)
        self.add_videos_button.clicked.connect(self.add_videos)
        self.button_row.insertWidget(1, self.add_videos_button)
        self.output_line.setPlaceholderText(
            "Optional output folder (blank overwrites originals)"
        )

        output_hint = QLabel(
            "Output folder applies to both folders and added videos; "
            "leave blank to rotate files in place."
        )
        output_hint.setWordWrap(True)
        self.extra_controls_layout.addWidget(output_hint)

        self.extra_controls_layout.addWidget(QLabel("Rotation direction"))
        self.extra_controls_layout.addWidget(self.rotation_selector)

        self.run_button.clicked.connect(self.run_processing)

    def additional_disable_widgets(self) -> list[QWidget]:
        return [self.rotation_selector, self.add_videos_button]

    def add_videos(self) -> None:
        filter_exts = " ".join(f"*{ext}" for ext in sorted(VIDEO_EXTENSIONS))
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Video Files",
            "",
            f"Video Files ({filter_exts});;All Files (*)",
        )
        for file_path in files:
            self._add_folder(Path(file_path))

    def accepts_dropped_path(self, path: Path) -> bool:
        if path.is_dir():
            return True
        return path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS

    def run_processing(self) -> None:
        sources = self.selected_folders()
        if not sources:
            QMessageBox.warning(
                self,
                "Missing sources",
                "Add at least one input folder or video file.",
            )
            return

        rotation = self.rotation_selector.currentData()
        if not isinstance(rotation, str):
            QMessageBox.warning(self, "Missing rotation", "Choose a rotation direction.")
            return

        output_dir = self.output_directory()

        missing = [path for path in sources if not path.exists()]
        if missing:
            missing_list = "\n".join(str(path) for path in missing)
            QMessageBox.warning(
                self,
                "Missing paths",
                f"The following paths were not found:\n{missing_list}",
            )
            return

        def task(path: Path) -> None:
            args: list[str] = []
            if path.is_dir():
                args.append(str(path))
            else:
                args.extend(["--video-file", str(path)])
            if output_dir is not None:
                args.extend(["--output-folder", str(output_dir)])
            args.extend(["--rotation", rotation])
            rotate_vid.main(args)

        self.start_worker(sources, task)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Video Tools")
        tabs = QTabWidget()
        tabs.addTab(ImagesToVideoTab(), "Images to Video")
        tabs.addTab(MergeVideosTab(), "Merge Videos")
        tabs.addTab(RotateVideosTab(), "Rotate Videos")
        self.setCentralWidget(tabs)


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.resize(960, 640)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
