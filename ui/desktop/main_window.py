from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QColor
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
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from models.client_record import ClientRecord
from rpa.csv_loader import (
    format_valor,
    is_valid_email,
    load_client_records,
    normalize_status,
    normalize_text,
    parse_valor,
    save_client_records,
    validate_record_fields,
)
from services.gmail_playwright_sender import GmailPlaywrightSender

CSV_HEADERS = [
    "Selecionar",
    "id",
    "cliente_nome",
    "email",
    "status",
    "valor",
    "vencimento",
    "ultima_cobranca",
    "observacao",
]

COL_SELECT = 0
COL_ID = 1
COL_CLIENTE = 2
COL_EMAIL = 3
COL_STATUS = 4
COL_VALOR = 5
COL_VENCIMENTO = 6
COL_ULTIMA = 7
COL_OBS = 8

EDITABLE_COLUMNS = {COL_CLIENTE, COL_EMAIL, COL_STATUS, COL_VALOR}


class WorkerThread(QThread):
    success = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[], object]) -> None:
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            self.success.emit(self._fn())
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))


class SendEmailsThread(QThread):
    log = Signal(str)
    summary = Signal(object)
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
            self.summary.emit(result)
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
        self.next_button = QPushButton("Proximo")
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

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def set_session_valid(self, valid: bool) -> None:
        self._session_valid = valid
        self.next_button.setEnabled(valid)
        if valid:
            self.status_label.setText("Sessao valida. Pode avancar.")
        else:
            self.status_label.setText("Sessao invalida ou expirada.")

    def get_profile_dir(self) -> str:
        profile_dir = self.profile_dir_edit.text().strip()
        if profile_dir:
            return profile_dir
        return str(Path("playwright-profile").resolve())

    def _emit_open_login(self) -> None:
        self.open_login_clicked.emit(self.get_profile_dir(), self.headless_checkbox.isChecked())

    def _emit_validate(self) -> None:
        self.validate_clicked.emit(self.get_profile_dir(), self.headless_checkbox.isChecked())


class CsvPage(QWidget):
    select_csv_clicked = Signal()
    save_csv_clicked = Signal()
    next_clicked = Signal()
    cell_edited = Signal(int, int, str, str)
    selection_toggled = Signal(int, bool)

    def __init__(self) -> None:
        super().__init__()
        self._updating_table = False

        title = QLabel("Tela 2/3 - CSV Management")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")

        self.select_csv_button = QPushButton("Carregar CSV")
        self.save_csv_button = QPushButton("Salvar CSV editado")
        self.next_button = QPushButton("Proximo")
        self.next_button.setEnabled(False)

        self.selected_file_label = QLabel("Arquivo: nenhum")
        self.count_label = QLabel("Total: 0 | Validos: 0 | Selecionados: 0")

        self.table = QTableWidget(0, len(CSV_HEADERS))
        self.table.setHorizontalHeaderLabels(CSV_HEADERS)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.SelectedClicked
            | QAbstractItemView.EditKeyPressed
        )

        self.rejected_box = QPlainTextEdit()
        self.rejected_box.setReadOnly(True)
        self.rejected_box.setPlaceholderText("Rejeitados/invalidos por linha")
        self.rejected_box.setMaximumHeight(120)

        top_buttons = QHBoxLayout()
        top_buttons.addWidget(self.select_csv_button)
        top_buttons.addWidget(self.save_csv_button)
        top_buttons.addStretch()
        top_buttons.addWidget(self.next_button)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addLayout(top_buttons)
        layout.addWidget(self.selected_file_label)
        layout.addWidget(self.count_label)
        layout.addWidget(self.table)
        layout.addWidget(QLabel("Linhas invalidas"))
        layout.addWidget(self.rejected_box)

        self.select_csv_button.clicked.connect(self.select_csv_clicked.emit)
        self.save_csv_button.clicked.connect(self.save_csv_clicked.emit)
        self.next_button.clicked.connect(self.next_clicked.emit)
        self.table.itemChanged.connect(self._on_item_changed)

    def set_selected_file(self, path: str) -> None:
        self.selected_file_label.setText(f"Arquivo: {path}")

    def set_counts(self, total: int, valid: int, selected: int) -> None:
        self.count_label.setText(f"Total: {total} | Validos: {valid} | Selecionados: {selected}")

    def set_next_enabled(self, enabled: bool) -> None:
        self.next_button.setEnabled(enabled)

    def set_rejections(self, rejected_rows: list[str]) -> None:
        if not rejected_rows:
            self.rejected_box.setPlainText("Sem rejeicoes.")
            return
        self.rejected_box.setPlainText("\n".join(rejected_rows))

    def populate_records(self, records: list[ClientRecord]) -> None:
        self._updating_table = True
        try:
            self.table.setRowCount(len(records))
            for row_index, record in enumerate(records):
                self._render_row(row_index, record)
        finally:
            self._updating_table = False

    def refresh_row(self, row_index: int, record: ClientRecord) -> None:
        self._updating_table = True
        try:
            self._render_row(row_index, record)
        finally:
            self._updating_table = False

    def revert_cell(self, row_index: int, column_index: int, old_text: str) -> None:
        self._updating_table = True
        try:
            item = self.table.item(row_index, column_index)
            if item is None:
                return
            item.setText(old_text)
            item.setData(Qt.UserRole, old_text)
        finally:
            self._updating_table = False

    def commit_cell(self, row_index: int, column_index: int, canonical_text: str) -> None:
        self._updating_table = True
        try:
            item = self.table.item(row_index, column_index)
            if item is None:
                return
            item.setText(canonical_text)
            item.setData(Qt.UserRole, canonical_text)
        finally:
            self._updating_table = False

    def _render_row(self, row_index: int, record: ClientRecord) -> None:
        checkbox_item = QTableWidgetItem("")
        checkbox_flags = Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
        if record.is_valid:
            checkbox_flags |= Qt.ItemIsEnabled
        checkbox_item.setFlags(checkbox_flags)
        checkbox_item.setCheckState(Qt.Checked if (record.is_valid and record.selected) else Qt.Unchecked)
        checkbox_item.setData(Qt.UserRole, checkbox_item.checkState())
        self.table.setItem(row_index, COL_SELECT, checkbox_item)

        row_values = [
            record.id,
            record.cliente_nome or "",
            record.email or "",
            record.status or "",
            format_valor(record.valor),
            record.vencimento or "",
            record.ultima_cobranca or "",
            record.observacao_erro,
        ]

        for column_index, value in enumerate(row_values, start=1):
            item = QTableWidgetItem(value)
            item.setData(Qt.UserRole, value)

            if column_index in EDITABLE_COLUMNS:
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
            else:
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)

            self.table.setItem(row_index, column_index, item)

        self._apply_row_colors(row_index, record)

    def _apply_row_colors(self, row_index: int, record: ClientRecord) -> None:
        if not record.is_valid:
            background = QColor("#b33535")
            foreground = QColor("#ffffff")
        elif record.status == "ABERTO":
            background = QColor("#ffe3b2")
            foreground = QColor("#1f1f1f")
        elif record.status in {"PAGO", "CANCELADO"}:
            background = QColor("#efefef")
            foreground = QColor("#1f1f1f")
        else:
            background = QColor("#ffffff")
            foreground = QColor("#1f1f1f")

        for column_index in range(self.table.columnCount()):
            item = self.table.item(row_index, column_index)
            if item is None:
                continue
            item.setBackground(background)
            item.setForeground(foreground)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_table:
            return

        row_index = item.row()
        column_index = item.column()

        if column_index == COL_SELECT:
            checked = item.checkState() == Qt.Checked
            item.setData(Qt.UserRole, item.checkState())
            self.selection_toggled.emit(row_index, checked)
            return

        if column_index not in EDITABLE_COLUMNS:
            return

        old_text = str(item.data(Qt.UserRole) or "")
        new_text = item.text()
        if new_text == old_text:
            return

        self.cell_edited.emit(row_index, column_index, new_text, old_text)


class SendPage(QWidget):
    back_clicked = Signal()
    send_clicked = Signal(str, str)
    subject_changed = Signal(str)
    body_changed = Signal(str)

    def __init__(self) -> None:
        super().__init__()

        title = QLabel("Tela 3/3 - Personalizacao e Envio")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")

        self.back_button = QPushButton("Voltar")
        self.send_button = QPushButton("Enviar")

        self.selected_label = QLabel("Destinatarios selecionados: 0")

        self.selected_emails_box = QPlainTextEdit()
        self.selected_emails_box.setReadOnly(True)
        self.selected_emails_box.setMaximumHeight(120)
        self.selected_emails_box.setPlaceholderText("Lista de emails selecionados")

        self.subject_edit = QLineEdit()
        self.subject_edit.setPlaceholderText("Assunto global do lote")

        self.body_edit = QPlainTextEdit()
        self.body_edit.setPlaceholderText("Corpo global do lote")

        self.logs_box = QPlainTextEdit()
        self.logs_box.setReadOnly(True)
        self.logs_box.setMaximumHeight(190)
        self.logs_box.setPlaceholderText("Logs de envio")

        top_buttons = QHBoxLayout()
        top_buttons.addWidget(self.back_button)
        top_buttons.addStretch()
        top_buttons.addWidget(self.send_button)

        form = QFormLayout()
        form.addRow("Assunto", self.subject_edit)
        form.addRow("Corpo", self.body_edit)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addLayout(top_buttons)
        layout.addWidget(self.selected_label)
        layout.addWidget(self.selected_emails_box)
        layout.addLayout(form)
        layout.addWidget(QLabel("Logs"))
        layout.addWidget(self.logs_box)

        self.back_button.clicked.connect(self.back_clicked.emit)
        self.send_button.clicked.connect(self._emit_send)
        self.subject_edit.textChanged.connect(self.subject_changed.emit)
        self.body_edit.textChanged.connect(lambda: self.body_changed.emit(self.body_edit.toPlainText()))

    def set_selected_recipients(self, records: list[ClientRecord]) -> None:
        emails = [record.email or "" for record in records]
        self.selected_label.setText(f"Destinatarios selecionados: {len(records)}")
        self.selected_emails_box.setPlainText("\n".join(email for email in emails if email))

    def set_subject(self, subject: str) -> None:
        if self.subject_edit.text() == subject:
            return
        blocked = self.subject_edit.blockSignals(True)
        self.subject_edit.setText(subject)
        self.subject_edit.blockSignals(blocked)

    def set_body(self, body: str) -> None:
        if self.body_edit.toPlainText() == body:
            return
        blocked = self.body_edit.blockSignals(True)
        self.body_edit.setPlainText(body)
        self.body_edit.blockSignals(blocked)

    def append_log(self, text: str) -> None:
        self.logs_box.appendPlainText(text)

    def clear_logs(self) -> None:
        self.logs_box.clear()

    def set_send_enabled(self, enabled: bool) -> None:
        self.send_button.setEnabled(enabled)

    def set_busy(self, busy: bool) -> None:
        self.send_button.setEnabled(not busy)
        self.back_button.setEnabled(not busy)
        self.subject_edit.setEnabled(not busy)
        self.body_edit.setEnabled(not busy)

    def _emit_send(self) -> None:
        subject = self.subject_edit.text().strip()
        body = self.body_edit.toPlainText().strip()

        if not subject:
            QMessageBox.warning(self, "Assunto vazio", "Preencha o assunto antes de enviar.")
            return

        if not body:
            QMessageBox.warning(self, "Corpo vazio", "Preencha o corpo antes de enviar.")
            return

        self.send_clicked.emit(subject, body)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MVP Gmail RPA")
        self.resize(1260, 820)

        self.logged_in = False
        self.records_loaded = False
        self.records: list[ClientRecord] = []
        self.selected_ids: set[str] = set()
        self.current_csv_path: str | None = None
        self.subject: str = ""
        self.body: str = ""

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
        self.csv_page.save_csv_clicked.connect(self._save_csv)
        self.csv_page.next_clicked.connect(self._go_to_send)
        self.csv_page.cell_edited.connect(self._on_csv_cell_edited)
        self.csv_page.selection_toggled.connect(self._on_csv_selection_toggled)

        self.send_page.back_clicked.connect(self._back_to_csv)
        self.send_page.subject_changed.connect(self._on_subject_changed)
        self.send_page.body_changed.connect(self._on_body_changed)
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
        self.logged_in = bool(valid)
        self.login_page.set_session_valid(self.logged_in)

        if not self.logged_in:
            self.login_page.set_status("Nao foi possivel validar. Verifique o login no Gmail.")

        self._refresh_send_state()

    def _on_login_check_error(self, error_message: str) -> None:
        self.logged_in = False
        self.login_page.set_session_valid(False)
        self.login_page.set_status("Erro ao validar sessao Gmail.")
        QMessageBox.critical(self, "Erro", f"Falha no login/validacao: {error_message}")
        self._refresh_send_state()

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
        self.current_csv_path = file_path
        self.records_loaded = len(records) > 0

        self.csv_page.set_selected_file(file_path)
        self.csv_page.populate_records(records)
        self.csv_page.set_rejections(rejected_rows)

        if not records:
            QMessageBox.warning(self, "CSV vazio", "Nao foi encontrado nenhum registro no CSV.")

        if rejected_rows:
            QMessageBox.information(
                self,
                "Linhas invalidas",
                f"{len(rejected_rows)} linha(s) com erro foram destacadas para correcao.",
            )

        self._refresh_csv_state()

    def _save_csv(self) -> None:
        if not self.records:
            QMessageBox.warning(self, "Sem dados", "Carregue um CSV antes de salvar.")
            return

        default_name = "clientes_editado.csv"
        if self.current_csv_path:
            default_name = f"{Path(self.current_csv_path).stem}_editado.csv"

        output_path, _ = QFileDialog.getSaveFileName(
            self,
            "Salvar CSV editado",
            str(Path.home() / default_name),
            "CSV (*.csv)",
        )
        if not output_path:
            return

        try:
            save_client_records(output_path, self.records, include_send_columns=True)
        except Exception as error:  # noqa: BLE001
            QMessageBox.critical(self, "Erro ao salvar", f"Nao foi possivel salvar CSV: {error}")
            return

        QMessageBox.information(self, "CSV salvo", f"Arquivo salvo em:\n{output_path}")

    def _on_csv_selection_toggled(self, row_index: int, checked: bool) -> None:
        if row_index < 0 or row_index >= len(self.records):
            return

        record = self.records[row_index]
        if not record.is_valid:
            record.selected = False
            self.csv_page.refresh_row(row_index, record)
            return

        record.selected = checked
        self._refresh_csv_state()

    def _on_csv_cell_edited(self, row_index: int, column_index: int, new_text: str, old_text: str) -> None:
        if row_index < 0 or row_index >= len(self.records):
            return

        record = self.records[row_index]
        candidate = new_text.strip()

        if column_index == COL_CLIENTE:
            record.cliente_nome = normalize_text(candidate)
            canonical = record.cliente_nome or ""
            self.csv_page.commit_cell(row_index, column_index, canonical)

        elif column_index == COL_EMAIL:
            email_value = normalize_text(candidate)
            if not is_valid_email(email_value):
                QMessageBox.warning(self, "Email invalido", "Informe um email valido para continuar.")
                self.csv_page.revert_cell(row_index, column_index, old_text)
                return
            record.email = email_value
            self.csv_page.commit_cell(row_index, column_index, record.email or "")

        elif column_index == COL_STATUS:
            status_value = normalize_status(candidate)
            if status_value not in {"ABERTO", "PAGO", "CANCELADO"}:
                QMessageBox.warning(
                    self,
                    "Status invalido",
                    "Use apenas ABERTO, PAGO ou CANCELADO.",
                )
                self.csv_page.revert_cell(row_index, column_index, old_text)
                return
            record.status = status_value
            self.csv_page.commit_cell(row_index, column_index, record.status)

        elif column_index == COL_VALOR:
            valor_value, valor_error = parse_valor(candidate)
            if valor_error is not None:
                QMessageBox.warning(self, "Valor invalido", valor_error)
                self.csv_page.revert_cell(row_index, column_index, old_text)
                return
            record.valor = valor_value
            self.csv_page.commit_cell(row_index, column_index, format_valor(record.valor))

        else:
            return

        was_valid = record.is_valid
        reasons = validate_record_fields(record.email, record.status, record.valor)
        record.is_valid = len(reasons) == 0
        record.observacao_erro = "; ".join(reasons)

        if not record.is_valid:
            record.selected = False
        elif not was_valid:
            record.selected = record.status == "ABERTO"

        self.csv_page.refresh_row(row_index, record)
        self._refresh_csv_state()

    def _refresh_csv_state(self) -> None:
        total = len(self.records)
        valid = sum(1 for record in self.records if record.is_valid)
        selected = sum(1 for record in self.records if record.is_valid and record.selected)

        self.selected_ids = {record.id for record in self.records if record.is_valid and record.selected}

        self.csv_page.set_counts(total, valid, selected)
        self.csv_page.set_next_enabled(selected > 0)
        self._refresh_send_state()

    def _go_to_send(self) -> None:
        selected_records = self._selected_records()
        if not selected_records:
            QMessageBox.warning(self, "Sem destinatarios", "Selecione ao menos um destinatario valido na Tela 2.")
            return

        self.send_page.set_selected_recipients(selected_records)
        self.send_page.set_subject(self.subject)
        self.send_page.set_body(self.body)
        self._refresh_send_state()

        self.stack.setCurrentWidget(self.send_page)

    def _back_to_csv(self) -> None:
        self.stack.setCurrentWidget(self.csv_page)

    def _on_subject_changed(self, text: str) -> None:
        self.subject = text.strip()
        self._refresh_send_state()

    def _on_body_changed(self, text: str) -> None:
        self.body = text.strip()
        self._refresh_send_state()

    def _send_emails(self, subject: str, body: str) -> None:
        records = self._selected_records()
        if not records:
            QMessageBox.warning(self, "Sem destinatarios", "Selecione destinatarios validos antes de enviar.")
            return

        if not self.logged_in:
            QMessageBox.warning(self, "Sessao invalida", "Retorne para Tela 1 e valide a sessao Gmail.")
            return

        if self._send_worker is not None and self._send_worker.isRunning():
            QMessageBox.information(self, "Envio em andamento", "Aguarde o envio atual finalizar.")
            return

        self.subject = subject.strip()
        self.body = body.strip()

        if not self.subject or not self.body:
            QMessageBox.warning(self, "Mensagem incompleta", "Assunto e corpo sao obrigatorios.")
            return

        sender = GmailPlaywrightSender(
            user_data_dir=self.login_page.get_profile_dir(),
            headless=self.login_page.headless_checkbox.isChecked(),
        )

        self.send_page.clear_logs()
        self.send_page.append_log(f"Iniciando envio para {len(records)} destinatario(s)...")
        self.send_page.set_busy(True)

        worker = SendEmailsThread(sender, records, self.subject, self.body)
        self._send_worker = worker

        worker.log.connect(self.send_page.append_log)
        worker.summary.connect(self._on_send_summary)
        worker.failed.connect(self._on_send_failed)
        worker.finished.connect(self._on_send_finished_cleanup)

        worker.start()

    def _on_send_summary(self, summary_obj: object) -> None:
        summary = dict(summary_obj) if isinstance(summary_obj, dict) else {}
        ok_count = int(summary.get("ok", 0))
        error_count = int(summary.get("error", 0))

        self._apply_send_results(summary.get("results", []))

        self.send_page.append_log("---")
        self.send_page.append_log(f"Resumo: OK={ok_count} | ERRO={error_count}")

        QMessageBox.information(
            self,
            "Envio finalizado",
            f"Resumo do envio:\nOK: {ok_count}\nERRO: {error_count}",
        )

    def _apply_send_results(self, results: object) -> None:
        if not isinstance(results, list):
            return

        by_key: dict[tuple[str, str], list[ClientRecord]] = {}
        for record in self.records:
            email_key = (record.email or "").strip().lower()
            key = (record.id, email_key)
            by_key.setdefault(key, []).append(record)

        for result in results:
            if not isinstance(result, dict):
                continue

            record_id = str(result.get("id", ""))
            email = str(result.get("email", "")).strip().lower()
            ok = bool(result.get("ok", False))
            error_message = str(result.get("error", "")).strip()

            key = (record_id, email)
            bucket = by_key.get(key)
            if not bucket:
                continue

            record = bucket.pop(0)

            if ok:
                record.mark_send_success()
            else:
                record.mark_send_error(error_message or "Falha no envio")

        if self.records:
            self.csv_page.populate_records(self.records)
            self._refresh_csv_state()

    def _on_send_failed(self, error_message: str) -> None:
        self.send_page.append_log(f"Erro fatal no envio: {error_message}")
        QMessageBox.critical(self, "Erro no envio", error_message)

    def _on_send_finished_cleanup(self) -> None:
        self.send_page.set_busy(False)
        self._send_worker = None
        self._refresh_send_state()

    def _selected_records(self) -> list[ClientRecord]:
        return [record for record in self.records if record.is_valid and record.selected]

    def _refresh_send_state(self) -> None:
        can_send = (
            self.logged_in
            and len(self._selected_records()) > 0
            and bool(self.subject.strip())
            and bool(self.body.strip())
            and (self._send_worker is None or not self._send_worker.isRunning())
        )
        self.send_page.set_send_enabled(can_send)

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
