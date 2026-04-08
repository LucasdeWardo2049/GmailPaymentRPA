import csv
import re
from pathlib import Path

from models.client_record import ClientRecord

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
VALID_STATUS = {"ABERTO", "PAGO", "CANCELADO"}
EXPECTED_COLUMNS = [
    "id",
    "cliente_nome",
    "email",
    "status",
    "valor",
    "vencimento",
    "ultima_cobranca",
]
EXTRA_EXPORT_COLUMNS = ["enviado_em", "envio_status", "envio_erro"]


def normalize_text(value: str | None) -> str | None:
    if value is None:
        return None

    normalized = value.strip()
    if not normalized:
        return None
    return normalized


def normalize_status(value: str | None) -> str | None:
    normalized = normalize_text(value)
    if normalized is None:
        return None
    return normalized.upper()


def parse_valor(value: str | None) -> tuple[float | None, str | None]:
    normalized = normalize_text(value)
    if normalized is None:
        return None, None

    candidate = normalized.replace(",", ".")
    try:
        parsed = float(candidate)
    except ValueError:
        return None, f"valor invalido '{normalized}'"

    if parsed < 0:
        return None, "valor deve ser maior ou igual a zero"
    return parsed, None


def is_valid_email(email: str | None) -> bool:
    normalized = normalize_text(email)
    if normalized is None:
        return False
    return bool(EMAIL_PATTERN.match(normalized))


def validate_record_fields(email: str | None, status: str | None, valor: float | None) -> list[str]:
    reasons: list[str] = []

    if not is_valid_email(email):
        reasons.append("email invalido")

    if status not in VALID_STATUS:
        reasons.append("status invalido (use ABERTO, PAGO ou CANCELADO)")

    if valor is not None and valor < 0:
        reasons.append("valor deve ser maior ou igual a zero")

    return reasons


def format_valor(valor: float | None) -> str:
    if valor is None:
        return ""
    return f"{valor:.2f}"


def _record_to_csv_row(record: ClientRecord, include_send_columns: bool) -> dict[str, str]:
    row: dict[str, str] = {
        "id": record.id or "",
        "cliente_nome": record.cliente_nome or "",
        "email": record.email or "",
        "status": record.status or "",
        "valor": format_valor(record.valor),
        "vencimento": record.vencimento or "",
        "ultima_cobranca": record.ultima_cobranca or "",
    }

    if include_send_columns:
        row["enviado_em"] = record.enviado_em or ""
        row["envio_status"] = record.envio_status or ""
        row["envio_erro"] = record.envio_erro or ""

    return row


def load_client_records(csv_path: str) -> tuple[list[ClientRecord], list[str]]:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Arquivo nao encontrado: {csv_path}")

    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)

        if reader.fieldnames is None:
            raise ValueError("CSV sem cabecalho.")

        missing_columns = [column for column in EXPECTED_COLUMNS if column not in reader.fieldnames]
        if missing_columns:
            missing = ", ".join(missing_columns)
            raise ValueError(f"CSV sem colunas obrigatorias: {missing}")

        records: list[ClientRecord] = []
        rejected_rows: list[str] = []

        for line_number, row in enumerate(reader, start=2):
            row_id = normalize_text(row.get("id"))
            cliente_nome = normalize_text(row.get("cliente_nome"))
            email = normalize_text(row.get("email"))
            status = normalize_status(row.get("status"))
            vencimento = normalize_text(row.get("vencimento"))
            ultima_cobranca = normalize_text(row.get("ultima_cobranca"))

            valor, valor_error = parse_valor(row.get("valor"))

            reasons: list[str] = []
            if row_id is None:
                row_id = f"linha-{line_number}"

            reasons.extend(validate_record_fields(email, status, valor))
            if valor_error:
                reasons.append(valor_error)

            is_valid = len(reasons) == 0
            observacao_erro = "; ".join(reasons)
            selected = is_valid and status == "ABERTO"

            record = ClientRecord(
                id=row_id,
                cliente_nome=cliente_nome,
                email=email,
                status=status,
                valor=valor,
                vencimento=vencimento,
                ultima_cobranca=ultima_cobranca,
                observacao_erro=observacao_erro,
                is_valid=is_valid,
                selected=selected,
            )
            records.append(record)

            if not is_valid:
                rejected_rows.append(f"Linha {line_number}: {observacao_erro}")

    return records, rejected_rows


def save_client_records(csv_path: str, records: list[ClientRecord], include_send_columns: bool = True) -> None:
    path = Path(csv_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [*EXPECTED_COLUMNS]
    if include_send_columns:
        fieldnames.extend(EXTRA_EXPORT_COLUMNS)

    with path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow(_record_to_csv_row(record, include_send_columns=include_send_columns))
