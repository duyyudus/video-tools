#!/usr/bin/env python3
"""PySide6 GUI wrapper around img2vid.py and merge_vid.py."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Iterable

from tools import img2vid, merge_vid
from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
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
MERGE_DEFAULT_RESOLUTION = ""
BUTTON_HEIGHT_SCALE = 1.5

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

        self.input_list = QListWidget()
        self.input_list.setSelectionMode(QAbstractItemView.ExtendedSelection)

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
        layout.addWidget(QLabel("Input folders"))
        layout.addWidget(self.input_list)

        button_row = QHBoxLayout()
        button_row.addWidget(self.add_button)
        button_row.addWidget(self.remove_button)
        layout.addLayout(button_row)

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
        self.resolution_input = QLineEdit(MERGE_DEFAULT_RESOLUTION)
        self.resolution_input.setPlaceholderText("Leave blank to inherit source resolution")

        self.extra_controls_layout.addWidget(QLabel("Resolution (optional WIDTHxHEIGHT)"))
        self.extra_controls_layout.addWidget(self.resolution_input)

        self.run_button.clicked.connect(self.run_processing)

    def additional_disable_widgets(self) -> list[QWidget]:
        return [self.resolution_input]

    def run_processing(self) -> None:
        folders = self.selected_folders()
        if not folders:
            QMessageBox.warning(self, "Missing folders", "Add at least one input folder.")
            return
        output_dir = self.output_directory()
        if output_dir is None:
            QMessageBox.warning(self, "Missing output", "Choose an output folder.")
            return

        resolution = self.resolution_input.text().strip()

        def task(folder: Path) -> None:
            args = [str(folder), str(output_dir)]
            if resolution:
                args.extend(["--resolution", resolution])
            merge_vid.main(args)

        self.start_worker(folders, task)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Video Tools")
        tabs = QTabWidget()
        tabs.addTab(ImagesToVideoTab(), "Images to Video")
        tabs.addTab(MergeVideosTab(), "Merge Videos")
        self.setCentralWidget(tabs)


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.resize(960, 640)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
