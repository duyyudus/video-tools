from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

import main


def get_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_gallery_url_list_accepts_ctrl_v_paste() -> None:
    app = get_app()
    tab = main.GalleryDownloadTab()
    app.clipboard().setText(
        "\n".join(
            [
                "https://trendszine.com/one.html",
                "not-a-url",
                "https://xwxse.com/artdetail/two/",
                "https://trendszine.com/one.html",
            ]
        )
    )

    event = QKeyEvent(QEvent.KeyPress, Qt.Key_V, Qt.ControlModifier)
    QApplication.sendEvent(tab.url_list, event)

    assert tab.selected_urls() == [
        "https://trendszine.com/one.html",
        "https://xwxse.com/artdetail/two/",
    ]
