from pathlib import Path
import sys

from PyQt6.QtWidgets import QApplication

from whatsapp_recovery.ui import RecoveryMainWindow


def resource_path(relative_path: str) -> Path:
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base_path / relative_path


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("WhatsApp Backup Reader & Recovery Assistant")
    with resource_path("whatsapp_recovery/theme.qss").open("r", encoding="utf-8") as handle:
        app.setStyleSheet(handle.read())

    window = RecoveryMainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
