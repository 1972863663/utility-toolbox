"""Native Acrylic edition of the toolbox; the original entry point is retained."""

import sys
import traceback

from PySide6.QtWidgets import QApplication, QMessageBox

from glass_toolbox.window import GlassWindow
from glass_toolbox.widgets import ui_font


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("UtilityToolboxGlass")
    app.setFont(ui_font())
    app.setQuitOnLastWindowClosed(False)
    window = GlassWindow()
    def report_error(kind, value, trace):
        detail = "".join(traceback.format_exception(kind, value, trace))
        from utility_toolbox import log_line
        log_line(detail)
        QMessageBox.critical(window, "操作遇到问题", str(value))
    sys.excepthook = report_error
    if "--minimized-to-tray" not in sys.argv or not window.tray:
        window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
