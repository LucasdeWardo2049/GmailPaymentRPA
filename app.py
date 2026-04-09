from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from ui.desktop.main_window import MainWindow


def main() -> None:
    app = QApplication([])
    QCoreApplication.setOrganizationName("MVPGmailRPA")
    QCoreApplication.setApplicationName("MVP Gmail RPA")
    window = MainWindow()
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
