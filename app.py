import sys

from PyQt6.QtWidgets import QApplication

from whatsapp_recovery.ui import RecoveryMainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("WhatsApp Backup Reader & Recovery Assistant")
    theme_path = __file__.replace("app.py", "whatsapp_recovery/theme.qss")
    with open(theme_path, "r", encoding="utf-8") as handle:
        app.setStyleSheet(handle.read())

    window = RecoveryMainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
