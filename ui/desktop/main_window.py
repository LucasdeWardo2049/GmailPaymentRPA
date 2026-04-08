from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from models.client_record import ClientRecord
from rpa.csv_loader import (
    EXPECTED_COLUMNS,
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
from services.audit_exporter import export_send_audit
from services.message_composer import (
    SUPPORTED_PLACEHOLDERS,
    compose_from_user_templates,
    validate_templates,
)
from services.message_rules import evaluate_record

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

APP_STYLESHEET = """
QWidget {
    font-family: 'Segoe UI', system-ui, sans-serif;
    font-size: 13px;
    background-color: #1e1e24; /* Fundo principal escuro */
    color: #e2e8f0; /* Texto claro */
}

/* Campos de texto e tabelas */
QLineEdit, QPlainTextEdit, QTableWidget {
    background-color: #2b2b36; /* Fundo levemente mais claro que o principal */
    border: 1px solid #3f3f4e;
    border-radius: 6px; /* Bordas mais modernas */
    padding: 6px;
    color: #e2e8f0;
}

/* Estilizacao especifica da Tabela */
QTableWidget {
    gridline-color: #3f3f4e; /* Linhas de grade suaves */
    alternate-background-color: #23232c;
    selection-background-color: #3b82f6; /* Azul moderno para selecao */
    selection-color: #ffffff;
}

QTableWidget::item:selected {
    background-color: #3b82f6;
    color: #ffffff;
}

/* Botoes */
QPushButton {
    background-color: #3b82f6; /* Azul primario */
    color: #ffffff;
    border: none;
    border-radius: 6px;
    min-height: 32px;
    padding: 6px 16px;
    font-weight: 600;
}

QPushButton:hover {
    background-color: #60a5fa; /* Azul mais claro no hover */
}

QPushButton:disabled {
    background-color: #3f3f4e;
    color: #94a3b8;
}

/* Foco nos inputs */
QLineEdit:focus, QPlainTextEdit:focus, QTableWidget:focus {
    border: 2px solid #3b82f6;
    outline: none;
}

/* Labels */
QLabel#statusLabel {
    font-weight: 600;
    color: #60a5fa;
}
"""


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
        per_client: bool,
        cooldown_days: int = 3,
    ) -> None:
        super().__init__()
        self._sender = sender
        self._records = records
        self._subject = subject
        self._body = body
        self._per_client = per_client
        self._cooldown_days = cooldown_days

    def run(self) -> None:
        try:
            if not self._per_client:
                result = self._sender.send_batch(
                    self._records,
                    self._subject,
                    self._body,
                    log_callback=self.log.emit,
                )
                self.summary.emit(result)
                return

            composed_items: list[tuple[ClientRecord, str, str]] = []
            pre_results: list[dict[str, object]] = []
            skip_count = 0
            template_error_count = 0

            for record in self._records:
                decision = evaluate_record(record, cooldown_days=self._cooldown_days)
                email = (record.email or "").strip()

                if not decision.eligible:
                    reason = decision.reason or "registro nao elegivel"
                    skip_count += 1
                    self.log.emit(f"SKIP | {record.id} | {email} | {reason}")
                    pre_results.append(
                        {
                            "id": record.id,
                            "email": email,
                            "ok": False,
                            "error": reason,
                            "skip": True,
                        }
                    )
                    continue

                try:
                    composed = compose_from_user_templates(record, decision, self._subject, self._body)
                except Exception as error:  # noqa: BLE001
                    error_message = str(error)
                    template_error_count += 1
                    self.log.emit(f"ERRO | {record.id} | {email} | {error_message}")
                    pre_results.append(
                        {
                            "id": record.id,
                            "email": email,
                            "ok": False,
                            "error": error_message,
                            "skip": False,
                        }
                    )
                    continue

                composed_items.append((record, composed.subject, composed.body))

            send_result: dict[str, object] = {"ok": 0, "error": 0, "results": []}
            if composed_items:
                send_result = self._sender.send_batch_composed(composed_items, log_callback=self.log.emit)

            summary = {
                "ok": int(send_result.get("ok", 0)),
                "error": int(send_result.get("error", 0)) + template_error_count,
                "skip": skip_count,
                "results": [*pre_results, *list(send_result.get("results", []))],
            }
            result = summary
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
        self._profile_dir = str(Path("playwright-profile").resolve())

        title = QLabel("Tela 1/3 - Login Gmail")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")

        self.profile_settings_button = QPushButton("Configurar diretorio de perfil (opcional)")
        self.profile_settings_button.setToolTip(
            "Abre um modal para configurar a pasta de perfil usada para manter login no Gmail"
        )
        self.headless_checkbox = QCheckBox("Headless")
        self.headless_checkbox.setChecked(False)
        self.headless_checkbox.setToolTip("Use apenas para testes. No modo visivel o login manual e mais confiavel")

        self.open_login_button = QPushButton("Abrir Gmail e fazer login")
        self.validate_button = QPushButton("Validar sessao")
        self.next_button = QPushButton("Proximo")
        self.next_button.setEnabled(False)
        self.open_login_button.setToolTip("Abre o Gmail para login manual")
        self.validate_button.setToolTip("Verifica se a sessao salva ainda esta valida")
        self.next_button.setToolTip("Ir para a etapa de CSV apos sessao valida")

        self.status_label = QLabel("Status: sessao nao validada.")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Playwright (avancado)", self.profile_settings_button)
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
        self.profile_settings_button.clicked.connect(self._open_profile_dir_modal)
        self.next_button.clicked.connect(self.next_clicked.emit)

    def set_busy(self, busy: bool) -> None:
        self.profile_settings_button.setEnabled(not busy)
        self.headless_checkbox.setEnabled(not busy)
        self.open_login_button.setEnabled(not busy)
        self.validate_button.setEnabled(not busy)
        self.next_button.setEnabled((not busy) and self._session_valid)

    def set_status(self, text: str) -> None:
        self.status_label.setText(f"Status: {text}")

    def set_session_valid(self, valid: bool) -> None:
        self._session_valid = valid
        self.next_button.setEnabled(valid)
        if valid:
            self.status_label.setText("Status: sessao valida. Pode avancar.")
        else:
            self.status_label.setText("Status: sessao invalida ou expirada.")

    def get_profile_dir(self) -> str:
        profile_dir = self._profile_dir.strip()
        if profile_dir:
            return profile_dir
        return str(Path("playwright-profile").resolve())

    def _emit_open_login(self) -> None:
        self.open_login_clicked.emit(self.get_profile_dir(), self.headless_checkbox.isChecked())

    def _emit_validate(self) -> None:
        self.validate_clicked.emit(self.get_profile_dir(), self.headless_checkbox.isChecked())

    def _open_profile_dir_modal(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Diretorio do perfil Playwright")
        dialog.setModal(True)

        layout = QVBoxLayout(dialog)
        description = QLabel(
            "Defina a pasta usada para armazenar sessao, cookies e dados do navegador entre execucoes."
        )
        description.setWordWrap(True)

        profile_dir_edit = QLineEdit(self.get_profile_dir())
        profile_dir_edit.setClearButtonEnabled(True)
        profile_dir_edit.setPlaceholderText("Informe ou selecione a pasta de perfil do navegador")

        browse_button = QPushButton("Escolher pasta")
        browse_button.setToolTip("Selecionar pasta de perfil")

        profile_row = QHBoxLayout()
        profile_row.setContentsMargins(0, 0, 0, 0)
        profile_row.addWidget(profile_dir_edit)
        profile_row.addWidget(browse_button)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)

        layout.addWidget(description)
        layout.addLayout(profile_row)
        layout.addWidget(buttons)

        def choose_profile_dir() -> None:
            selected = QFileDialog.getExistingDirectory(
                dialog,
                "Selecionar diretorio de perfil",
                profile_dir_edit.text().strip() or self.get_profile_dir(),
            )
            if selected:
                profile_dir_edit.setText(selected)

        browse_button.clicked.connect(choose_profile_dir)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)

        if dialog.exec() != QDialog.Accepted:
            return

        selected_profile = profile_dir_edit.text().strip()
        self._profile_dir = selected_profile or str(Path("playwright-profile").resolve())


class CsvPage(QWidget):
    select_csv_clicked = Signal()
    save_csv_clicked = Signal()
    select_all_valid_clicked = Signal()
    clear_selection_clicked = Signal()
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
        self.select_all_valid_button = QPushButton("Selecionar validos")
        self.clear_selection_button = QPushButton("Limpar selecao")
        self.next_button = QPushButton("Proximo")
        self.next_button.setEnabled(False)
        self.select_csv_button.setToolTip("Importar arquivo CSV")
        self.save_csv_button.setToolTip("Salvar as alteracoes em um novo CSV")
        self.select_all_valid_button.setToolTip("Marcar todos os registros validos")
        self.clear_selection_button.setToolTip("Desmarcar todos os registros")
        self.next_button.setToolTip("Ir para a tela de envio")

        self.selected_file_label = QLabel("Arquivo: nenhum")
        self.count_label = QLabel("Total: 0 | Validos: 0 | Selecionados: 0")
        self.count_label.setWordWrap(True)

        self.table = QTableWidget(0, len(CSV_HEADERS))
        self.table.setHorizontalHeaderLabels(CSV_HEADERS)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(60)
        header.setSectionResizeMode(COL_SELECT, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_ID, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_VALOR, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_VENCIMENTO, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_ULTIMA, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_CLIENTE, QHeaderView.Interactive)
        header.setSectionResizeMode(COL_EMAIL, QHeaderView.Stretch)
        header.setSectionResizeMode(COL_OBS, QHeaderView.Interactive)
        self.table.setColumnWidth(COL_CLIENTE, 180)
        self.table.setColumnWidth(COL_OBS, 220)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.SelectedClicked
            | QAbstractItemView.EditKeyPressed
        )
        self.table.setToolTip("Duplo clique em cliente_nome, email, status ou valor para editar")
        self.table.setAccessibleName("Tabela de registros do CSV")

        self.rejected_box = QPlainTextEdit()
        self.rejected_box.setReadOnly(True)
        self.rejected_box.setPlaceholderText("Rejeitados/invalidos por linha")
        self.rejected_box.setMaximumHeight(120)
        self.rejected_box.setToolTip("Lista de linhas com problemas de validacao")
        self.rejected_box.setAccessibleName("Lista de linhas invalidas")

        top_buttons = QHBoxLayout()
        top_buttons.addWidget(self.select_csv_button)
        top_buttons.addWidget(self.save_csv_button)
        top_buttons.addWidget(self.select_all_valid_button)
        top_buttons.addWidget(self.clear_selection_button)
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
        self.select_all_valid_button.clicked.connect(self.select_all_valid_clicked.emit)
        self.clear_selection_button.clicked.connect(self.clear_selection_clicked.emit)
        self.next_button.clicked.connect(self.next_clicked.emit)
        self.table.itemChanged.connect(self._on_item_changed)

    def set_selected_file(self, path: str) -> None:
        self.selected_file_label.setText(f"Arquivo: {path}")

    def set_counts(self, total: int, valid: int, selected: int) -> None:
        self.count_label.setText(f"Resumo CSV - Total: {total} | Validos: {valid} | Selecionados: {selected}")

    def set_next_enabled(self, enabled: bool) -> None:
        self.next_button.setEnabled(enabled)

    def set_rejections(self, rejected_rows: list[str]) -> None:
        if not rejected_rows:
            self.rejected_box.setPlainText("Sem Rejeicoes")
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
    back_to_import_clicked = Signal()
    send_clicked = Signal(str, str, bool)
    subject_changed = Signal(str)
    body_changed = Signal(str)
    selection_changed = Signal()
    select_all_eligible_clicked = Signal()
    clear_selection_clicked = Signal()

    SEND_HEADERS = ["Enviar", *EXPECTED_COLUMNS, "motivo"]

    SEND_COL_SELECT = 0
    SEND_COL_ID = 1
    SEND_COL_CLIENTE = 2
    SEND_COL_EMAIL = 3
    SEND_COL_STATUS = 4
    SEND_COL_VALOR = 5
    SEND_COL_VENCIMENTO = 6
    SEND_COL_ULTIMA = 7
    SEND_COL_MOTIVO = 8

    PRESET_MANUAL = "manual"
    PRESET_FRIENDLY = "friendly"
    PRESET_SECOND_NOTICE = "second_notice"
    PRESET_FINAL_NOTICE = "final_notice"
    PRESET_LAST_CALL = "last_call"

    def __init__(self) -> None:
        super().__init__()
        self._records: list[ClientRecord] = []
        self._preview_records: list[ClientRecord] = []
        self._updating_table = False
        self._is_busy = False
        self._logs_tab_visible = False
        self._preset_templates: dict[str, tuple[str, str]] = {
            self.PRESET_FRIENDLY: (
                "Lembrete amigavel - {cliente_nome} (ID {record_id})",
                (
                    "Ola {cliente_nome},\n\n"
                    "Identificamos uma pendencia no valor de R$ {valor}.\n"
                    "Vencimento: {vencimento}.\n"
                    "Dias em atraso: {dias_atraso}.\n\n"
                    "Se ja efetuou o pagamento, desconsidere esta mensagem.\n"
                    "Obrigado."
                ),
            ),
            self.PRESET_SECOND_NOTICE: (
                "2o aviso: pendencia em aberto para {cliente_nome}",
                (
                    "Ola {cliente_nome},\n\n"
                    "Este e nosso 2o aviso sobre a pendencia de R$ {valor}.\n"
                    "Vencimento original: {vencimento}.\n"
                    "Atraso atual: {dias_atraso} dia(s).\n\n"
                    "Se precisar de apoio para regularizacao, responda este contato."
                ),
            ),
            self.PRESET_FINAL_NOTICE: (
                "Aviso final de cobranca - {cliente_nome}",
                (
                    "Prezado(a) {cliente_nome},\n\n"
                    "Ate o momento nao identificamos a regularizacao do valor de R$ {valor}.\n"
                    "Vencimento: {vencimento}.\n"
                    "Atraso: {dias_atraso} dia(s).\n\n"
                    "Solicitamos retorno imediato para evitar escalonamento."
                ),
            ),
            self.PRESET_LAST_CALL: (
                "Ultima tentativa de contato - ID {record_id}",
                (
                    "Ola {cliente_nome},\n\n"
                    "Esta e a ultima tentativa de contato sobre o titulo em aberto de R$ {valor}.\n"
                    "Vencimento: {vencimento}.\n"
                    "Dias em atraso: {dias_atraso}.\n\n"
                    "Caso ja tenha pago, desconsidere e nos informe para atualizacao."
                ),
            ),
        }

        title = QLabel("Tela 3/3 - Personalizacao e Envio")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")

        self.back_button = QPushButton("Voltar")
        self.back_to_import_button = QPushButton("Voltar para Importacao")
        self.back_to_import_button.setVisible(False)
        self.back_to_import_button.setToolTip("Exibe apos finalizar para retornar a Tela 2")
        self.send_button = QPushButton("Enviar")
        self.select_all_button = QPushButton("Selecionar elegiveis")
        self.clear_selection_button = QPushButton("Limpar selecao")
        self.per_client_checkbox = QCheckBox("Personalizar por cliente (placeholders)")
        self.per_client_checkbox.setChecked(False)
        self.per_client_checkbox.setToolTip(
            "Ative para usar placeholders por cliente, como {cliente_nome} e {dias_atraso}"
        )
        self.select_all_button.setToolTip("Marcar todos os destinatarios elegiveis")
        self.clear_selection_button.setToolTip("Desmarcar todos os destinatarios")
        self.send_button.setToolTip("Iniciar envio")

        self.selected_label = QLabel("Destinatarios selecionados: 0")
        self.selected_label.setWordWrap(True)

        self.table = QTableWidget(0, len(self.SEND_HEADERS))
        self.table.setHorizontalHeaderLabels(self.SEND_HEADERS)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(60)
        header.setSectionResizeMode(self.SEND_COL_SELECT, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.SEND_COL_ID, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.SEND_COL_STATUS, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.SEND_COL_VALOR, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.SEND_COL_VENCIMENTO, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.SEND_COL_ULTIMA, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.SEND_COL_CLIENTE, QHeaderView.Interactive)
        header.setSectionResizeMode(self.SEND_COL_EMAIL, QHeaderView.Stretch)
        header.setSectionResizeMode(self.SEND_COL_MOTIVO, QHeaderView.Interactive)
        self.table.setColumnWidth(self.SEND_COL_CLIENTE, 180)
        self.table.setColumnWidth(self.SEND_COL_MOTIVO, 240)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setToolTip("Linhas cinza sao SKIP por regra e nao podem ser enviadas")
        self.table.setAccessibleName("Tabela de envio com motivo de skip")

        self.subject_edit = QLineEdit()
        self.subject_edit.setPlaceholderText("Assunto global do lote")
        self.subject_edit.setToolTip("Assunto fixo para todos os destinatarios no modo global")
        self.subject_edit.setAccessibleName("Campo assunto global")

        self.body_edit = QPlainTextEdit()
        self.body_edit.setPlaceholderText("Corpo global do lote")
        self.body_edit.setToolTip("Mensagem fixa para todos os destinatarios no modo global")
        self.body_edit.setAccessibleName("Campo corpo global da mensagem")

        self.template_preset_combo = QComboBox()
        self.template_preset_combo.addItem("Template manual", self.PRESET_MANUAL)
        self.template_preset_combo.addItem("Cobranca amigavel", self.PRESET_FRIENDLY)
        self.template_preset_combo.addItem("2o aviso", self.PRESET_SECOND_NOTICE)
        self.template_preset_combo.addItem("Aviso final", self.PRESET_FINAL_NOTICE)
        self.template_preset_combo.addItem("Ultima tentativa", self.PRESET_LAST_CALL)
        self.template_preset_combo.setToolTip("Escolha um preset para preencher assunto/corpo template")

        self.template_subject_edit = QLineEdit()
        self.template_subject_edit.setPlaceholderText("Assunto template por cliente")
        self.template_subject_edit.setToolTip("Aceita placeholders como {cliente_nome} e {dias_atraso}")
        self.template_subject_edit.setAccessibleName("Campo assunto template por cliente")

        self.template_body_edit = QPlainTextEdit()
        self.template_body_edit.setPlaceholderText("Corpo template por cliente")
        self.template_body_edit.setToolTip("Aceita placeholders por cliente")
        self.template_body_edit.setAccessibleName("Campo corpo template por cliente")

        self.help_placeholders_button = QPushButton("Ver Variaveis Disponiveis { }")
        self.help_placeholders_button.setToolTip("Abrir lista de placeholders em modal")

        placeholder_buttons_layout = QHBoxLayout()
        placeholder_buttons_layout.addWidget(QLabel("Inserir variavel:"))
        for name in SUPPORTED_PLACEHOLDERS:
            token = f"{{{name}}}"
            button = QPushButton(token)
            button.setToolTip(f"Inserir {token} no cursor do template")
            button.clicked.connect(lambda _, value=token: self._insert_placeholder(value))
            placeholder_buttons_layout.addWidget(button)
        placeholder_buttons_layout.addStretch()
        placeholder_buttons_layout.addWidget(self.help_placeholders_button)

        self.preview_client_combo = QComboBox()
        self.preview_client_combo.setToolTip("Selecione um cliente elegivel para visualizacao")

        self.preview_subject_view = QLineEdit()
        self.preview_subject_view.setReadOnly(True)
        self.preview_subject_view.setPlaceholderText("Assunto renderizado")

        self.preview_body_view = QPlainTextEdit()
        self.preview_body_view.setReadOnly(True)
        self.preview_body_view.setMaximumHeight(130)
        self.preview_body_view.setPlaceholderText("Corpo renderizado")

        self.preview_status_label = QLabel("Preview por cliente elegivel.")
        self.preview_status_label.setWordWrap(True)

        self.logs_box = QPlainTextEdit()
        self.logs_box.setReadOnly(True)
        self.logs_box.setPlaceholderText("Logs de envio")
        self.logs_box.setAccessibleName("Area de logs de envio")

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)

        top_buttons = QHBoxLayout()
        top_buttons.addWidget(self.back_button)
        top_buttons.addWidget(self.back_to_import_button)
        top_buttons.addWidget(self.select_all_button)
        top_buttons.addWidget(self.clear_selection_button)
        top_buttons.addStretch()
        top_buttons.addWidget(self.per_client_checkbox)
        top_buttons.addWidget(self.send_button)

        top_section = QWidget()
        top_section_layout = QVBoxLayout(top_section)
        top_section_layout.setContentsMargins(0, 0, 0, 0)
        top_section_layout.setSpacing(8)
        top_section_layout.addLayout(top_buttons)
        top_section_layout.addWidget(self.selected_label)
        top_section_layout.addWidget(self.table, 1)

        self.global_section = QWidget()
        global_layout = QVBoxLayout(self.global_section)
        global_layout.setContentsMargins(0, 0, 0, 0)
        global_layout.setSpacing(8)
        global_title = QLabel("Modo Global (lote unico)")
        global_title.setStyleSheet("font-weight: 600;")
        global_form = QFormLayout()
        global_form.addRow("Assunto", self.subject_edit)
        global_form.addRow("Corpo", self.body_edit)
        global_layout.addWidget(global_title)
        global_layout.addLayout(global_form)

        self.per_client_section = QWidget()
        per_client_layout = QVBoxLayout(self.per_client_section)
        per_client_layout.setContentsMargins(0, 0, 0, 0)
        per_client_layout.setSpacing(8)
        per_client_title = QLabel("Modo por Cliente (templates)")
        per_client_title.setStyleSheet("font-weight: 600;")

        template_form = QFormLayout()
        template_form.addRow("Preset", self.template_preset_combo)
        template_form.addRow("Assunto template", self.template_subject_edit)
        template_form.addRow("Corpo template", self.template_body_edit)

        per_client_layout.addWidget(per_client_title)
        per_client_layout.addLayout(template_form)
        per_client_layout.addLayout(placeholder_buttons_layout)
        per_client_layout.addWidget(QLabel("Use o botao de ajuda para ver placeholders e exemplos."))
        per_client_layout.addStretch()

        self.editor_tab = QWidget()
        editor_tab_layout = QVBoxLayout(self.editor_tab)
        editor_tab_layout.setContentsMargins(8, 8, 8, 8)
        editor_tab_layout.setSpacing(10)
        editor_tab_layout.addWidget(self.global_section)
        editor_tab_layout.addWidget(self.per_client_section)
        editor_tab_layout.addStretch()

        self.preview_tab = QWidget()
        preview_tab_layout = QVBoxLayout(self.preview_tab)
        preview_tab_layout.setContentsMargins(8, 8, 8, 8)
        preview_tab_layout.setSpacing(10)
        preview_form = QFormLayout()
        preview_form.addRow("Cliente de preview", self.preview_client_combo)
        preview_form.addRow("Assunto renderizado", self.preview_subject_view)
        preview_form.addRow("Corpo renderizado", self.preview_body_view)
        preview_tab_layout.addLayout(preview_form)
        preview_tab_layout.addWidget(self.preview_status_label)
        preview_tab_layout.addStretch()

        self.logs_tab = QWidget()
        logs_tab_layout = QVBoxLayout(self.logs_tab)
        logs_tab_layout.setContentsMargins(8, 8, 8, 8)
        logs_tab_layout.setSpacing(10)
        logs_tab_layout.addWidget(self.logs_box)
        logs_tab_layout.addWidget(self.progress_bar)

        self.bottom_tabs = QTabWidget()
        self.bottom_tabs.addTab(self.editor_tab, "Editor de Mensagem")
        self.bottom_tabs.addTab(self.preview_tab, "Preview por Cliente")

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(top_section, 1)
        layout.addWidget(self.bottom_tabs, 1)

        self.back_button.clicked.connect(self.back_clicked.emit)
        self.back_to_import_button.clicked.connect(self.back_to_import_clicked.emit)
        self.send_button.clicked.connect(self._emit_send)
        self.select_all_button.clicked.connect(self.select_all_eligible_clicked.emit)
        self.clear_selection_button.clicked.connect(self.clear_selection_clicked.emit)
        self.help_placeholders_button.clicked.connect(self._show_placeholders_help)
        self.subject_edit.textChanged.connect(lambda _: self._emit_active_message_changed())
        self.body_edit.textChanged.connect(lambda: self._emit_active_message_changed())
        self.template_subject_edit.textChanged.connect(lambda _: self._on_template_changed())
        self.template_body_edit.textChanged.connect(self._on_template_changed)
        self.template_preset_combo.currentIndexChanged.connect(lambda _: self._on_preset_changed())
        self.preview_client_combo.currentIndexChanged.connect(lambda _: self._update_preview())
        self.table.itemChanged.connect(self._on_item_changed)
        self.per_client_checkbox.toggled.connect(self._on_mode_toggled)

        self._update_mode_ui()
        self._refresh_preview_candidates()
        self._update_preview()
        self._emit_active_message_changed()

    def set_selected_recipients(self, records: list[ClientRecord]) -> None:
        self._records = list(records)
        self._updating_table = True
        try:
            self.table.setRowCount(len(self._records))
            for row_index, record in enumerate(self._records):
                self._render_row(row_index, record)
        finally:
            self._updating_table = False

        self._update_selected_label()
        self._refresh_preview_candidates()
        self._update_preview()
        self.selection_changed.emit()

    def selected_records(self) -> list[ClientRecord]:
        selected: list[ClientRecord] = []
        for row_index, record in enumerate(self._records):
            checkbox = self.table.item(row_index, self.SEND_COL_SELECT)
            if checkbox is None:
                continue

            if not (checkbox.flags() & Qt.ItemIsEnabled):
                continue

            if checkbox.checkState() == Qt.Checked:
                selected.append(record)

        return selected

    def selected_count(self) -> int:
        return len(self.selected_records())

    def is_per_client_enabled(self) -> bool:
        return self.per_client_checkbox.isChecked()

    def select_all_eligible(self) -> None:
        self._updating_table = True
        try:
            for row_index in range(self.table.rowCount()):
                checkbox = self.table.item(row_index, self.SEND_COL_SELECT)
                if checkbox is None:
                    continue
                if not (checkbox.flags() & Qt.ItemIsEnabled):
                    continue
                checkbox.setCheckState(Qt.Checked)
        finally:
            self._updating_table = False

        self._update_selected_label()
        self._refresh_preview_candidates()
        self._update_preview()
        self.selection_changed.emit()

    def clear_selection(self) -> None:
        self._updating_table = True
        try:
            for row_index in range(self.table.rowCount()):
                checkbox = self.table.item(row_index, self.SEND_COL_SELECT)
                if checkbox is None:
                    continue
                checkbox.setCheckState(Qt.Unchecked)
        finally:
            self._updating_table = False

        self._update_selected_label()
        self._refresh_preview_candidates()
        self._update_preview()
        self.selection_changed.emit()

    def set_subject(self, subject: str) -> None:
        if self.subject_edit.text() != subject:
            blocked = self.subject_edit.blockSignals(True)
            self.subject_edit.setText(subject)
            self.subject_edit.blockSignals(blocked)

        if self.template_subject_edit.text() != subject:
            blocked = self.template_subject_edit.blockSignals(True)
            self.template_subject_edit.setText(subject)
            self.template_subject_edit.blockSignals(blocked)

        self._emit_active_message_changed()
        self._update_preview()

    def set_body(self, body: str) -> None:
        if self.body_edit.toPlainText() != body:
            blocked = self.body_edit.blockSignals(True)
            self.body_edit.setPlainText(body)
            self.body_edit.blockSignals(blocked)

        if self.template_body_edit.toPlainText() != body:
            blocked = self.template_body_edit.blockSignals(True)
            self.template_body_edit.setPlainText(body)
            self.template_body_edit.blockSignals(blocked)

        self._emit_active_message_changed()
        self._update_preview()

    def append_log(self, text: str) -> None:
        self._ensure_logs_tab_visible(select_tab=False)
        self.logs_box.appendPlainText(text)

    def clear_logs(self) -> None:
        self.logs_box.clear()
        self._hide_logs_tab()
        self.back_to_import_button.setVisible(False)

    def set_post_send_navigation_visible(self, visible: bool) -> None:
        self.back_to_import_button.setVisible(bool(visible))

    def set_send_enabled(self, enabled: bool) -> None:
        self.send_button.setEnabled(enabled and not self._is_busy)

    def set_busy(self, busy: bool) -> None:
        self._is_busy = busy
        self.send_button.setEnabled(not busy)
        self.back_button.setEnabled(not busy)
        self.back_to_import_button.setEnabled(not busy)
        self.select_all_button.setEnabled(not busy)
        self.clear_selection_button.setEnabled(not busy)
        self.per_client_checkbox.setEnabled(not busy)
        self.table.setEnabled(not busy)
        self.global_section.setEnabled(not busy)
        self.per_client_section.setEnabled(not busy)
        self.help_placeholders_button.setEnabled(not busy)

        if busy:
            self._ensure_logs_tab_visible(select_tab=True)

        if busy:
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(0)

    def _emit_send(self) -> None:
        subject = self._active_subject_text().strip()
        body = self._active_body_text().strip()

        if not subject:
            QMessageBox.warning(self, "Assunto vazio", "Preencha o assunto antes de enviar.")
            return

        if not body:
            QMessageBox.warning(self, "Corpo vazio", "Preencha o corpo antes de enviar.")
            return

        if self.selected_count() == 0:
            QMessageBox.warning(self, "Sem selecao", "Selecione ao menos um destinatario elegivel.")
            return

        if self.is_per_client_enabled():
            try:
                validate_templates(subject, body)
            except ValueError as error:
                QMessageBox.warning(self, "Template invalido", str(error))
                return

        self.send_clicked.emit(subject, body, self.is_per_client_enabled())

    def _render_row(self, row_index: int, record: ClientRecord) -> None:
        decision = evaluate_record(record, cooldown_days=3)
        eligible = bool(record.is_valid and decision.eligible and (record.email or "").strip())

        checkbox = QTableWidgetItem("")
        flags = Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
        if eligible:
            flags |= Qt.ItemIsEnabled
        checkbox.setFlags(flags)
        checkbox.setCheckState(Qt.Checked if eligible else Qt.Unchecked)
        self.table.setItem(row_index, self.SEND_COL_SELECT, checkbox)

        row_values = [
            record.id,
            record.cliente_nome or "",
            record.email or "",
            record.status or "",
            format_valor(record.valor),
            record.vencimento or "",
            record.ultima_cobranca or "",
            decision.reason if not decision.eligible else "",
        ]

        for column_index, value in enumerate(row_values, start=1):
            item = QTableWidgetItem(value)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(row_index, column_index, item)

        if eligible:
            self._apply_eligible_style(row_index)
        else:
            self._apply_skip_style(row_index)

    def _apply_eligible_style(self, row_index: int) -> None:
        background = QColor("#2b2f38")
        foreground = QColor("#e2e8f0")

        for column_index in range(self.table.columnCount()):
            item = self.table.item(row_index, column_index)
            if item is None:
                continue
            item.setBackground(background)
            item.setForeground(foreground)

    def _apply_skip_style(self, row_index: int) -> None:
        background = QColor("#3a3f4a")
        foreground = QColor("#cbd5e1")

        for column_index in range(self.table.columnCount()):
            item = self.table.item(row_index, column_index)
            if item is None:
                continue
            item.setBackground(background)
            item.setForeground(foreground)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_table:
            return

        if item.column() != self.SEND_COL_SELECT:
            return

        self._update_selected_label()
        self._refresh_preview_candidates()
        self._update_preview()
        self.selection_changed.emit()

    def _update_selected_label(self) -> None:
        self.selected_label.setText(f"Destinatarios selecionados: {self.selected_count()}")

    def _on_mode_toggled(self, _: bool) -> None:
        self._update_mode_ui()
        self.bottom_tabs.setCurrentWidget(self.editor_tab)
        self._emit_active_message_changed()
        self._refresh_preview_candidates()
        self._update_preview()
        self.selection_changed.emit()

    def _on_template_changed(self) -> None:
        self._emit_active_message_changed()
        self._update_preview()

    def _update_mode_ui(self) -> None:
        per_client = self.is_per_client_enabled()
        self.global_section.setVisible(not per_client)
        self.per_client_section.setVisible(per_client)
        self.send_button.setText("Enviar por cliente" if per_client else "Enviar")

    def _active_subject_text(self) -> str:
        if self.is_per_client_enabled():
            return self.template_subject_edit.text()
        return self.subject_edit.text()

    def _active_body_text(self) -> str:
        if self.is_per_client_enabled():
            return self.template_body_edit.toPlainText()
        return self.body_edit.toPlainText()

    def _emit_active_message_changed(self) -> None:
        self.subject_changed.emit(self._active_subject_text())
        self.body_changed.emit(self._active_body_text())

    def _on_preset_changed(self) -> None:
        preset_key = str(self.template_preset_combo.currentData() or self.PRESET_MANUAL)
        if preset_key == self.PRESET_MANUAL:
            self._on_template_changed()
            return

        preset = self._preset_templates.get(preset_key)
        if preset is None:
            return

        subject_template, body_template = preset
        block_subject = self.template_subject_edit.blockSignals(True)
        block_body = self.template_body_edit.blockSignals(True)
        try:
            self.template_subject_edit.setText(subject_template)
            self.template_body_edit.setPlainText(body_template)
        finally:
            self.template_subject_edit.blockSignals(block_subject)
            self.template_body_edit.blockSignals(block_body)

        self._on_template_changed()

    def _insert_placeholder(self, placeholder: str) -> None:
        if self.template_subject_edit.hasFocus():
            self.template_subject_edit.insert(placeholder)
            return

        cursor = self.template_body_edit.textCursor()
        cursor.insertText(placeholder)
        self.template_body_edit.setTextCursor(cursor)
        self.template_body_edit.setFocus()

    def _show_placeholders_help(self) -> None:
        placeholders = "\n".join(f"- {{{name}}}" for name in SUPPORTED_PLACEHOLDERS)
        QMessageBox.information(
            self,
            "Variaveis Disponiveis",
            (
                "Use placeholders no modo por cliente para personalizar assunto e corpo.\n\n"
                f"Disponiveis:\n{placeholders}\n\n"
                "Exemplo de assunto:\n"
                "Aviso para {cliente_nome} - atraso de {dias_atraso} dia(s)\n\n"
                "Exemplo de corpo:\n"
                "Ola {cliente_nome}, titulo de R$ {valor} com vencimento em {vencimento}."
            ),
        )

    def _ensure_logs_tab_visible(self, select_tab: bool) -> None:
        if not self._logs_tab_visible:
            self.bottom_tabs.addTab(self.logs_tab, "Logs de Envio")
            self._logs_tab_visible = True

        if select_tab:
            self.bottom_tabs.setCurrentWidget(self.logs_tab)

    def _hide_logs_tab(self) -> None:
        if not self._logs_tab_visible:
            return

        logs_tab_index = self.bottom_tabs.indexOf(self.logs_tab)
        if logs_tab_index < 0:
            self._logs_tab_visible = False
            return

        if self.bottom_tabs.currentIndex() == logs_tab_index:
            self.bottom_tabs.setCurrentWidget(self.editor_tab)

        self.bottom_tabs.removeTab(logs_tab_index)
        self._logs_tab_visible = False

    def _refresh_preview_candidates(self) -> None:
        current_key: tuple[str, str] | None = None
        current_index = self.preview_client_combo.currentIndex()
        if 0 <= current_index < len(self._preview_records):
            current_record = self._preview_records[current_index]
            current_key = (
                current_record.id,
                (current_record.email or "").strip().lower(),
            )

        blocked = self.preview_client_combo.blockSignals(True)
        try:
            self.preview_client_combo.clear()
            self._preview_records = []

            restored_index = -1
            for row_index, record in enumerate(self._records):
                checkbox = self.table.item(row_index, self.SEND_COL_SELECT)
                if checkbox is None:
                    continue
                if not (checkbox.flags() & Qt.ItemIsEnabled):
                    continue
                if checkbox.checkState() != Qt.Checked:
                    continue

                decision = evaluate_record(record, cooldown_days=3)
                email = (record.email or "").strip()
                if not (record.is_valid and decision.eligible and email):
                    continue

                self._preview_records.append(record)
                display_name = record.cliente_nome or "(sem nome)"
                self.preview_client_combo.addItem(f"{display_name} <{email}>")

                key = (record.id, email.lower())
                if current_key is not None and key == current_key:
                    restored_index = len(self._preview_records) - 1

            if self._preview_records:
                if restored_index >= 0:
                    self.preview_client_combo.setCurrentIndex(restored_index)
                else:
                    self.preview_client_combo.setCurrentIndex(0)
        finally:
            self.preview_client_combo.blockSignals(blocked)

    def _set_preview_message(self, text: str, color: str) -> None:
        self.preview_status_label.setStyleSheet(f"font-weight: 600; color: {color};")
        self.preview_status_label.setText(text)

    def _update_preview(self) -> None:
        if not self.is_per_client_enabled():
            self.preview_subject_view.clear()
            self.preview_body_view.clear()
            self._set_preview_message(
                "Ative o modo por cliente para visualizar o preview do template.",
                "#93c5fd",
            )
            return

        if not self._preview_records:
            self.preview_subject_view.clear()
            self.preview_body_view.clear()
            self._set_preview_message(
                "Nenhum cliente elegivel selecionado para preview.",
                "#fca5a5",
            )
            return

        preview_index = self.preview_client_combo.currentIndex()
        if preview_index < 0 or preview_index >= len(self._preview_records):
            preview_index = 0
            blocked = self.preview_client_combo.blockSignals(True)
            try:
                self.preview_client_combo.setCurrentIndex(0)
            finally:
                self.preview_client_combo.blockSignals(blocked)

        record = self._preview_records[preview_index]
        decision = evaluate_record(record, cooldown_days=3)

        subject_template = self.template_subject_edit.text().strip()
        body_template = self.template_body_edit.toPlainText().strip()
        if not subject_template and not body_template:
            self.preview_subject_view.clear()
            self.preview_body_view.clear()
            self._set_preview_message(
                "Preencha assunto e corpo template para gerar o preview.",
                "#93c5fd",
            )
            return

        try:
            composed = compose_from_user_templates(
                record,
                decision,
                subject_template,
                body_template,
            )
        except Exception as error:  # noqa: BLE001
            self.preview_subject_view.clear()
            self.preview_body_view.clear()
            self._set_preview_message(f"Template invalido: {error}", "#fca5a5")
            return

        self.preview_subject_view.setText(composed.subject)
        self.preview_body_view.setPlainText(composed.body)
        self._set_preview_message("Template valido para o cliente selecionado.", "#86efac")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MVP Gmail RPA")
        self.resize(1260, 820)
        self.setMinimumSize(1024, 700)
        self.setStyleSheet(APP_STYLESHEET)
        self.statusBar().showMessage("Pronto")

        self.logged_in = False
        self.records_loaded = False
        self.records: list[ClientRecord] = []
        self.selected_ids: set[str] = set()
        self.current_csv_path: str | None = None
        self.subject: str = ""
        self.body: str = ""
        self._send_completed = False

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
        self._setup_shortcuts()

    def _setup_shortcuts(self) -> None:
        self.shortcut_load_csv = QShortcut(QKeySequence("Ctrl+L"), self)
        self.shortcut_load_csv.activated.connect(self._select_csv)

        self.shortcut_save_csv = QShortcut(QKeySequence("Ctrl+S"), self)
        self.shortcut_save_csv.activated.connect(self._save_csv)

        self.shortcut_send = QShortcut(QKeySequence("Ctrl+Return"), self)
        self.shortcut_send.activated.connect(self._trigger_send_shortcut)

    def _trigger_send_shortcut(self) -> None:
        if self.stack.currentWidget() is not self.send_page:
            return
        if not self.send_page.send_button.isEnabled():
            return
        self.send_page.send_button.click()

    def _connect_signals(self) -> None:
        self.login_page.open_login_clicked.connect(self._open_gmail_for_login)
        self.login_page.validate_clicked.connect(self._validate_session)
        self.login_page.next_clicked.connect(self._go_to_csv)

        self.csv_page.select_csv_clicked.connect(self._select_csv)
        self.csv_page.save_csv_clicked.connect(self._save_csv)
        self.csv_page.select_all_valid_clicked.connect(self._select_all_valid_csv)
        self.csv_page.clear_selection_clicked.connect(self._clear_csv_selection)
        self.csv_page.next_clicked.connect(self._go_to_send)
        self.csv_page.cell_edited.connect(self._on_csv_cell_edited)
        self.csv_page.selection_toggled.connect(self._on_csv_selection_toggled)

        self.send_page.back_clicked.connect(self._back_to_csv)
        self.send_page.back_to_import_clicked.connect(self._back_to_csv_after_send)
        self.send_page.subject_changed.connect(self._on_subject_changed)
        self.send_page.body_changed.connect(self._on_body_changed)
        self.send_page.selection_changed.connect(self._refresh_send_state)
        self.send_page.select_all_eligible_clicked.connect(self._select_all_send)
        self.send_page.clear_selection_clicked.connect(self._clear_send_selection)
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
        self.statusBar().showMessage("Tela 2 aberta: carregue e revise o CSV")
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
        self.statusBar().showMessage(f"CSV carregado: {len(records)} registro(s)")

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
        self.statusBar().showMessage("CSV editado salvo com sucesso", 5000)

    def _select_all_valid_csv(self) -> None:
        if not self.records:
            return

        for record in self.records:
            record.selected = bool(record.is_valid)

        self.csv_page.populate_records(self.records)
        self._refresh_csv_state()

    def _clear_csv_selection(self) -> None:
        if not self.records:
            return

        for record in self.records:
            record.selected = False

        self.csv_page.populate_records(self.records)
        self._refresh_csv_state()

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

        self._send_completed = False
        self.send_page.set_post_send_navigation_visible(False)
        self.send_page.set_selected_recipients(selected_records)
        self.send_page.set_subject(self.subject)
        self.send_page.set_body(self.body)
        self._refresh_send_state()

        self.statusBar().showMessage("Tela 3 aberta: configure mensagem e envie", 5000)
        self.stack.setCurrentWidget(self.send_page)

    def _back_to_csv(self) -> None:
        self.statusBar().showMessage("Retornou para Tela 2")
        self.stack.setCurrentWidget(self.csv_page)

    def _back_to_csv_after_send(self) -> None:
        self.statusBar().showMessage("Retornou para Tela 2 apos envio", 5000)
        self.stack.setCurrentWidget(self.csv_page)

    def _select_all_send(self) -> None:
        self.send_page.select_all_eligible()
        self._refresh_send_state()

    def _clear_send_selection(self) -> None:
        self.send_page.clear_selection()
        self._refresh_send_state()

    def _on_subject_changed(self, text: str) -> None:
        self.subject = text.strip()
        self._refresh_send_state()

    def _on_body_changed(self, text: str) -> None:
        self.body = text.strip()
        self._refresh_send_state()

    def _send_emails(self, subject: str, body: str, per_client: bool) -> None:
        records = self.send_page.selected_records()
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
        self._send_completed = False
        self.send_page.set_post_send_navigation_visible(False)

        if not self.subject or not self.body:
            QMessageBox.warning(self, "Mensagem incompleta", "Assunto e corpo sao obrigatorios.")
            return

        if per_client:
            try:
                validate_templates(self.subject, self.body)
            except ValueError as error:
                QMessageBox.warning(self, "Template invalido", str(error))
                return

        sender = GmailPlaywrightSender(
            user_data_dir=self.login_page.get_profile_dir(),
            headless=self.login_page.headless_checkbox.isChecked(),
        )

        self.send_page.clear_logs()
        mode_label = "per-client" if per_client else "lote unico"
        self.send_page.append_log(f"Iniciando envio para {len(records)} destinatario(s) [{mode_label}]...")
        self.send_page.set_busy(True)
        self.statusBar().showMessage("Envio em andamento...")

        worker = SendEmailsThread(sender, records, self.subject, self.body, per_client=per_client)
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
        skip_count = int(summary.get("skip", 0))

        self._apply_send_results(summary.get("results", []))

        self.send_page.append_log("---")
        self.send_page.append_log(f"Resumo: OK={ok_count} | ERRO={error_count} | SKIP={skip_count}")

        try:
            audit_dir = export_send_audit(
                summary=summary,
                records=self.records,
                raw_logs=self.send_page.logs_box.toPlainText(),
            )
            self.send_page.append_log(f"AUDITORIA | Exportada em: {audit_dir}")
        except Exception as error:  # noqa: BLE001
            self.send_page.append_log(f"AVISO | Falha ao exportar auditoria: {error}")

        self._send_completed = True
        self.send_page.set_post_send_navigation_visible(True)

        QMessageBox.information(
            self,
            "Envio finalizado",
            f"Resumo do envio:\nOK: {ok_count}\nERRO: {error_count}\nSKIP: {skip_count}",
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
            is_skip = bool(result.get("skip", False))

            if is_skip:
                record.mark_send_skip(error_message or "Registro inelegivel")
            elif ok:
                record.mark_send_success()
            else:
                record.mark_send_error(error_message or "Falha no envio")

        if self.records:
            self.csv_page.populate_records(self.records)
            self._refresh_csv_state()

    def _on_send_failed(self, error_message: str) -> None:
        self._send_completed = False
        self.send_page.append_log(f"Erro fatal no envio: {error_message}")

        try:
            audit_dir = export_send_audit(
                summary={"ok": 0, "error": 1, "skip": 0, "results": []},
                records=self.records,
                raw_logs=self.send_page.logs_box.toPlainText(),
            )
            self.send_page.append_log(f"AUDITORIA | Exportada em: {audit_dir}")
        except Exception as error:  # noqa: BLE001
            self.send_page.append_log(f"AVISO | Falha ao exportar auditoria: {error}")

        QMessageBox.critical(self, "Erro no envio", error_message)

    def _on_send_finished_cleanup(self) -> None:
        self.send_page.set_busy(False)
        self._send_worker = None
        self.send_page.set_post_send_navigation_visible(self._send_completed)
        self._refresh_send_state()
        self.statusBar().showMessage("Envio finalizado", 5000)

    def _selected_records(self) -> list[ClientRecord]:
        return [record for record in self.records if record.is_valid and record.selected]

    def _refresh_send_state(self) -> None:
        reason = "Pronto para enviar"
        can_send = True

        if not self.logged_in:
            can_send = False
            reason = "Valide a sessao do Gmail na Tela 1"
        elif self.send_page.selected_count() == 0:
            can_send = False
            reason = "Selecione ao menos um destinatario elegivel"
        elif not self.subject.strip():
            can_send = False
            reason = "Preencha o assunto"
        elif not self.body.strip():
            can_send = False
            reason = "Preencha o corpo da mensagem"
        elif self._send_worker is not None and self._send_worker.isRunning():
            can_send = False
            reason = "Envio em andamento"

        self.send_page.set_send_enabled(can_send)
        self.send_page.send_button.setToolTip(reason)

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
