from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from models.client_record import ClientRecord
from rpa.csv_loader import EXPECTED_COLUMNS, load_client_records
from services.gmail_playwright_sender import GmailPlaywrightSender


class WorkerThread(QThread):
    success = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[], object]) -> None:
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn()
            self.success.emit(result)
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))


class SendEmailsThread(QThread):
    log = Signal(str)
    summary = Signal(int, int)
    failed = Signal(str)

    def __init__(
        self,
        sender: GmailPlaywrightSender,
        records: list[ClientRecord],
        subject: str,
        body: str,
    ) -> None:
        super().__init__()
        self._sender = sender
        self._records = records
        self._subject = subject
        self._body = body

    def run(self) -> None:
        try:
            result = self._sender.send_batch(
                self._records,
                self._subject,
                self._body,
                log_callback=self.log.emit,
            )
            self.summary.emit(result["ok"], result["error"])
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))


class LoginPage(QWidget):
    open_login_clicked = Signal(str, bool)
    validate_clicked = Signal(str, bool)
    next_clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._session_valid = False

        title = QLabel("Tela 1/3 - Login Gmail")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")

        self.profile_dir_edit = QLineEdit(str(Path("playwright-profile").resolve()))
        self.headless_checkbox = QCheckBox("Headless")
        self.headless_checkbox.setChecked(False)

        self.open_login_button = QPushButton("Abrir Gmail e fazer login")
        self.validate_button = QPushButton("Validar sessao")
        self.next_button = QPushButton("Avancar")
        self.next_button.setEnabled(False)

        self.status_label = QLabel("Sessao nao validada.")

        form = QFormLayout()
        form.addRow("Diretorio do perfil Playwright", self.profile_dir_edit)
        form.addRow("", self.headless_checkbox)

        buttons = QHBoxLayout()
        buttons.addWidget(self.open_login_button)
        buttons.addWidget(self.validate_button)
        buttons.addStretch()
        buttons.addWidget(self.next_button)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.status_label)
        layout.addStretch()

        self.open_login_button.clicked.connect(self._emit_open_login)
        self.validate_button.clicked.connect(self._emit_validate)
        self.next_button.clicked.connect(self.next_clicked.emit)

    def set_busy(self, busy: bool) -> None:
        self.profile_dir_edit.setEnabled(not busy)
        self.headless_checkbox.setEnabled(not busy)
        self.open_login_button.setEnabled(not busy)
        self.validate_button.setEnabled(not busy)
        self.next_button.setEnabled((not busy) and self._session_valid)

    def set_session_valid(self, valid: bool) -> None:
        self._session_valid = valid
        self.next_button.setEnabled(valid)
        if valid:
            self.status_label.setText("Sessao valida. Pode avancar.")
        else:
            self.status_label.setText("Sessao invalida ou expirada.")

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _emit_open_login(self) -> None:
        self.open_login_clicked.emit(self.get_profile_dir(), self.headless_checkbox.isChecked())

    def _emit_validate(self) -> None:
        self.validate_clicked.emit(self.get_profile_dir(), self.headless_checkbox.isChecked())

    def get_profile_dir(self) -> str:
        profile_dir = self.profile_dir_edit.text().strip()
        if profile_dir:
            return profile_dir
        return str(Path("playwright-profile").resolve())


class CsvPage(QWidget):
    select_csv_clicked = Signal()
    next_clicked = Signal()

    def __init__(self) -> None:
        super().__init__()

        title = QLabel("Tela 2/3 - Upload CSV")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")

        self.select_csv_button = QPushButton("Selecionar CSV")
        self.next_button = QPushButton("Avancar")
        self.next_button.setEnabled(False)

        self.selected_file_label = QLabel("Arquivo: nenhum")
        self.count_label = QLabel("Registros validos: 0")

        self.table = QTableWidget(0, len(EXPECTED_COLUMNS))
        self.table.setHorizontalHeaderLabels(EXPECTED_COLUMNS)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)

        self.rejected_box = QPlainTextEdit()
        self.rejected_box.setReadOnly(True)
        self.rejected_box.setPlaceholderText("Linhas rejeitadas aparecerao aqui.")
        self.rejected_box.setMaximumHeight(120)

        button_row = QHBoxLayout()
        button_row.addWidget(self.select_csv_button)
        button_row.addStretch()
        button_row.addWidget(self.next_button)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addLayout(button_row)
        layout.addWidget(self.selected_file_label)
        layout.addWidget(self.count_label)
        layout.addWidget(self.table)
        layout.addWidget(QLabel("Rejeitados"))
        layout.addWidget(self.rejected_box)

        self.select_csv_button.clicked.connect(self.select_csv_clicked.emit)
        self.next_button.clicked.connect(self.next_clicked.emit)

    def set_selected_file(self, path: str) -> None:
        self.selected_file_label.setText(f"Arquivo: {path}")

    def set_records(self, records: list[ClientRecord]) -> None:
        self.table.setRowCount(len(records))

        for row_index, record in enumerate(records):
            values = [
                str(record.id),
                record.cliente_nome,
                record.email,
                record.status,
                record.valor,
                record.vencimento,
                record.ultima_cobranca,
            ]
            for column_index, value in enumerate(values):
                self.table.setItem(row_index, column_index, QTableWidgetItem(value))

        self.count_label.setText(f"Registros validos: {len(records)}")

    def set_rejections(self, rejected_rows: list[str]) -> None:
        if not rejected_rows:
            self.rejected_box.setPlainText("Sem rejeicoes.")
            return
        self.rejected_box.setPlainText("\n".join(rejected_rows))

    def set_next_enabled(self, enabled: bool) -> None:
        self.next_button.setEnabled(enabled)


class SendPage(QWidget):
    send_clicked = Signal(object, str, str)
    back_clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._records: list[ClientRecord] = []

        title = QLabel("Tela 3/3 - Selecao e Envio")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")

        self.back_button = QPushButton("Voltar")
        self.send_button = QPushButton("Enviar")

        self.table = QTableWidget(0, len(EXPECTED_COLUMNS) + 1)
        self.table.setHorizontalHeaderLabels(["Enviar", *EXPECTED_COLUMNS])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)

        self.subject_edit = QLineEdit()
        self.subject_edit.setPlaceholderText("Subject unico para todos os selecionados")

        self.body_edit = QPlainTextEdit()
        self.body_edit.setPlaceholderText("Body unico para todos os selecionados")

        self.logs_box = QPlainTextEdit()
        self.logs_box.setReadOnly(True)
        self.logs_box.setPlaceholderText("Logs de envio")
        self.logs_box.setMaximumHeight(180)

        top_buttons = QHBoxLayout()
        top_buttons.addWidget(self.back_button)
        top_buttons.addStretch()
        top_buttons.addWidget(self.send_button)

        form = QFormLayout()
        form.addRow("Subject", self.subject_edit)
        form.addRow("Body", self.body_edit)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addLayout(top_buttons)
        layout.addWidget(self.table)
        layout.addLayout(form)
        layout.addWidget(QLabel("Logs"))
        layout.addWidget(self.logs_box)

        self.send_button.clicked.connect(self._emit_send)
        self.back_button.clicked.connect(self.back_clicked.emit)

    def populate_records(self, records: list[ClientRecord]) -> None:
        self._records = records
        self.table.setRowCount(len(records))

        for row_index, record in enumerate(records):
            checkbox_item = QTableWidgetItem()
            checkbox_item.setFlags(
                Qt.ItemIsEnabled
                | Qt.ItemIsSelectable
                | Qt.ItemIsUserCheckable
            )
            checkbox_item.setCheckState(Qt.Checked)
            self.table.setItem(row_index, 0, checkbox_item)

            values = [
                str(record.id),
                record.cliente_nome,
                record.email,
                record.status,
                record.valor,
                record.vencimento,
                record.ultima_cobranca,
            ]
            for column_index, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row_index, column_index, item)

    def append_log(self, text: str) -> None:
        self.logs_box.appendPlainText(text)

    def clear_logs(self) -> None:
        self.logs_box.clear()

    def set_busy(self, busy: bool) -> None:
        self.send_button.setEnabled(not busy)
        self.back_button.setEnabled(not busy)
        self.table.setEnabled(not busy)
        self.subject_edit.setEnabled(not busy)
        self.body_edit.setEnabled(not busy)

    def _emit_send(self) -> None:
        selected = self._selected_records()
        subject = self.subject_edit.text().strip()
        body = self.body_edit.toPlainText().strip()

        if not selected:
            QMessageBox.warning(self, "Sem selecao", "Selecione ao menos um cliente.")
            return

        if not subject:
            QMessageBox.warning(self, "Subject vazio", "Preencha o subject.")
            return

        if not body:
            QMessageBox.warning(self, "Body vazio", "Preencha o body.")
            return

        self.send_clicked.emit(selected, subject, body)

    def _selected_records(self) -> list[ClientRecord]:
        selected: list[ClientRecord] = []

        for row_index, record in enumerate(self._records):
            item = self.table.item(row_index, 0)
            if item is not None and item.checkState() == Qt.Checked:
                selected.append(record)

        return selected


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MVP Gmail RPA")
        self.resize(1200, 760)

        self.logged_in = False
        self.records_loaded = False
        self.selected_ids: set[int] = set()
        self.records: list[ClientRecord] = []

        self._worker: WorkerThread | None = None
        self._send_worker: SendEmailsThread | None = None

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.login_page = LoginPage()
        self.csv_page = CsvPage()
        self.send_page = SendPage()

        self.stack.addWidget(self.login_page)
        self.stack.addWidget(self.csv_page)
        self.stack.addWidget(self.send_page)

        self._connect_signals()

    def _connect_signals(self) -> None:
        self.login_page.open_login_clicked.connect(self._open_gmail_for_login)
        self.login_page.validate_clicked.connect(self._validate_session)
        self.login_page.next_clicked.connect(self._go_to_csv)

        self.csv_page.select_csv_clicked.connect(self._select_csv)
        self.csv_page.next_clicked.connect(self._go_to_send)

        self.send_page.back_clicked.connect(self._back_to_csv)
        self.send_page.send_clicked.connect(self._send_emails)

    def _open_gmail_for_login(self, profile_dir: str, headless: bool) -> None:
        self.login_page.set_status("Abrindo Gmail para login manual...")
        self.login_page.set_busy(True)

        sender = GmailPlaywrightSender(profile_dir, headless)

        self._start_worker(
            fn=lambda: sender.open_gmail_for_manual_login(timeout_ms=300000),
            on_success=self._on_login_check_success,
            on_error=self._on_login_check_error,
            on_finish=lambda: self.login_page.set_busy(False),
        )

    def _validate_session(self, profile_dir: str, headless: bool) -> None:
        self.login_page.set_status("Validando sessao existente...")
        self.login_page.set_busy(True)

        sender = GmailPlaywrightSender(profile_dir, headless)

        self._start_worker(
            fn=lambda: sender.validate_session(timeout_ms=15000),
            on_success=self._on_login_check_success,
            on_error=self._on_login_check_error,
            on_finish=lambda: self.login_page.set_busy(False),
        )

    def _on_login_check_success(self, valid: object) -> None:
        is_valid = bool(valid)
        self.logged_in = is_valid
        self.login_page.set_session_valid(is_valid)

        if not is_valid:
            self.login_page.set_status("Nao foi possivel validar. Verifique o login no Gmail.")

    def _on_login_check_error(self, error_message: str) -> None:
        self.logged_in = False
        self.login_page.set_session_valid(False)
        self.login_page.set_status("Erro ao validar sessao Gmail.")
        QMessageBox.critical(self, "Erro", f"Falha no login/validacao: {error_message}")

    def _go_to_csv(self) -> None:
        if not self.logged_in:
            QMessageBox.warning(self, "Sessao invalida", "Valide a sessao Gmail antes de avancar.")
            return
        self.stack.setCurrentWidget(self.csv_page)

    def _select_csv(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Selecionar CSV",
            str(Path.home()),
            "CSV (*.csv)",
        )
        if not file_path:
            return

        try:
            records, rejected_rows = load_client_records(file_path)
        except Exception as error:  # noqa: BLE001
            QMessageBox.critical(self, "CSV invalido", str(error))
            return

        self.records = records
        self.records_loaded = len(records) > 0

        self.csv_page.set_selected_file(file_path)
        self.csv_page.set_records(records)
        self.csv_page.set_rejections(rejected_rows)
        self.csv_page.set_next_enabled(self.records_loaded)

        if rejected_rows:
            QMessageBox.information(
                self,
                "Linhas rejeitadas",
                f"{len(rejected_rows)} linha(s) foram rejeitadas. Veja detalhes na tela.",
            )

        if not self.records_loaded:
            QMessageBox.warning(self, "Sem linhas validas", "Nenhum registro valido foi carregado.")

    def _go_to_send(self) -> None:
        if not self.records_loaded:
            QMessageBox.warning(self, "CSV vazio", "Carregue ao menos um registro valido.")
            return

        self.send_page.populate_records(self.records)
        self.stack.setCurrentWidget(self.send_page)

    def _back_to_csv(self) -> None:
        self.stack.setCurrentWidget(self.csv_page)

    def _send_emails(self, selected_records: object, subject: str, body: str) -> None:
        records = list(selected_records)

        if not self.logged_in:
            QMessageBox.warning(self, "Sessao invalida", "Retorne para Tela 1 e valide sessao.")
            return

        if self._send_worker is not None and self._send_worker.isRunning():
            QMessageBox.information(self, "Envio em andamento", "Aguarde o envio atual terminar.")
            return

        self.selected_ids = {record.id for record in records}
        profile_dir = self.login_page.get_profile_dir()
        headless = self.login_page.headless_checkbox.isChecked()
        sender = GmailPlaywrightSender(profile_dir, headless)

        self.send_page.clear_logs()
        self.send_page.append_log(f"Iniciando envio para {len(records)} destinatario(s)...")
        self.send_page.set_busy(True)

        worker = SendEmailsThread(sender, records, subject, body)
        self._send_worker = worker

        worker.log.connect(self.send_page.append_log)
        worker.summary.connect(self._on_send_summary)
        worker.failed.connect(self._on_send_failed)
        worker.finished.connect(self._on_send_finished_cleanup)

        worker.start()

    def _on_send_summary(self, ok_count: int, error_count: int) -> None:
        self.send_page.append_log("---")
        self.send_page.append_log(f"Resumo: OK={ok_count} | ERRO={error_count}")
        QMessageBox.information(
            self,
            "Envio finalizado",
            f"Resumo do envio:\nOK: {ok_count}\nERRO: {error_count}",
        )

    def _on_send_failed(self, error_message: str) -> None:
        self.send_page.append_log(f"Erro fatal no envio: {error_message}")
        QMessageBox.critical(self, "Erro no envio", error_message)

    def _on_send_finished_cleanup(self) -> None:
        self.send_page.set_busy(False)
        self._send_worker = None

    def _start_worker(
        self,
        fn: Callable[[], object],
        on_success: Callable[[object], None],
        on_error: Callable[[str], None],
        on_finish: Callable[[], None],
    ) -> None:
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "Aguarde", "Ja existe uma acao em andamento.")
            return

        worker = WorkerThread(fn)
        self._worker = worker

        worker.success.connect(on_success)
        worker.failed.connect(on_error)
        worker.finished.connect(on_finish)
        worker.finished.connect(self._clear_worker)

        worker.start()

    def _clear_worker(self) -> None:
        self._worker = None
