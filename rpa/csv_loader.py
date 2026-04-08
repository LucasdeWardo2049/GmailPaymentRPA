import csv
import re
from pathlib import Path

from models.client_record import ClientRecord

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EXPECTED_COLUMNS = [
    "id",
    "cliente_nome",
    "email",
    "status",
    "valor",
    "vencimento",
    "ultima_cobranca",
]


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

        accepted_records: list[ClientRecord] = []
        rejected_rows: list[str] = []

        for line_number, row in enumerate(reader, start=2):
            row_id = (row.get("id") or "").strip()
            email = (row.get("email") or "").strip()
            if not row_id.isdigit():
                rejected_rows.append(f"Linha {line_number}: id invalido '{row_id}'.")
                continue
            if not email or not EMAIL_PATTERN.match(email):
                rejected_rows.append(f"Linha {line_number}: email invalido '{email}'.")
                continue

            accepted_records.append(
                ClientRecord(
                    id=int(row_id),
                    cliente_nome=(row.get("cliente_nome") or "").strip(),
                    email=email,
                    status=(row.get("status") or "").strip(),
                    valor=(row.get("valor") or "").strip(),
                    vencimento=(row.get("vencimento") or "").strip(),
                    ultima_cobranca=(row.get("ultima_cobranca") or "").strip(),
                )
            )

    return accepted_records, rejected_rows
