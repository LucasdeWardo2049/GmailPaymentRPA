from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook
import pytest

from rpa.csv_loader import (
    load_client_records,
    load_client_records_from_file,
    load_client_records_from_xlsx,
)


def _write_csv(tmp_path: Path, content: str) -> str:
    csv_path = tmp_path / "clientes.csv"
    csv_path.write_text(content, encoding="utf-8")
    return str(csv_path)


def _write_xlsx(tmp_path: Path, headers: list[str], rows: list[list[object]]) -> str:
    xlsx_path = tmp_path / "clientes.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(headers)
    for row in rows:
        worksheet.append(row)
    workbook.save(xlsx_path)
    workbook.close()
    return str(xlsx_path)


def test_load_client_records_accepts_valid_rows(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path,
        "id,cliente_nome,email,status,valor,vencimento,ultima_cobranca\n"
        "1,Joao,joao@example.com,ABERTO,199.90,01-04-2026,\n",
    )

    records, rejected_rows = load_client_records(csv_path)

    assert len(records) == 1
    assert rejected_rows == []
    assert records[0].id == "1"
    assert records[0].vencimento == "2026-04-01"
    assert records[0].is_valid is True
    assert records[0].selected is True


def test_load_client_records_normalizes_mixed_date_formats(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path,
        "id,cliente_nome,email,status,valor,vencimento,ultima_cobranca\n"
        "10,Ana,ana@example.com,ABERTO,100.00,2026-04-08,07-04-2026\n",
    )

    records, rejected_rows = load_client_records(csv_path)

    assert len(records) == 1
    assert rejected_rows == []
    assert records[0].vencimento == "2026-04-08"
    assert records[0].ultima_cobranca == "2026-04-07"


def test_load_client_records_autoincrements_missing_id(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path,
        "id,cliente_nome,email,status,valor,vencimento,ultima_cobranca\n"
        "1,Joao,joao@example.com,ABERTO,200.00,2026-04-01,\n"
        "2,Ana,ana@example.com,ABERTO,200.00,2026-04-01,\n"
        ",Maria,maria@example.com,ABERTO,200.00,2026-04-01,\n",
    )

    records, rejected_rows = load_client_records(csv_path)

    assert len(records) == 3
    assert rejected_rows == []
    assert records[2].id == "3"
    assert records[2].is_valid is True
    assert records[2].observacao_erro == ""


def test_load_client_records_rejects_invalid_email(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path,
        "id,cliente_nome,email,status,valor,vencimento,ultima_cobranca\n"
        "2,Carlos,carlos-email.com,ABERTO,90.00,2026-04-01,\n",
    )

    records, rejected_rows = load_client_records(csv_path)

    assert len(records) == 1
    assert len(rejected_rows) == 1
    assert "email invalido" in rejected_rows[0]
    assert rejected_rows[0].startswith("Linha 1:")
    assert records[0].is_valid is False


def test_load_client_records_reports_data_row_number_not_header_line(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path,
        "id,cliente_nome,email,status,valor,vencimento,ultima_cobranca\n"
        "1,Cliente Um,2022006229@ifam.edu.br,ABERTO,120.50,2026-04-01,\n"
        "2,Cliente Dois,2026500481@ifam.edu.br,ABERTO,120.50,2026-04-06,\n"
        ",Cliente Sem ID,semid@example.com,ABERTO,60.00,08-04-2026,\n"
        "9002,Cliente Email Invalido,email-invalido,ABERTO,95.00,08-04-2026,\n",
    )

    _, rejected_rows = load_client_records(csv_path)

    assert rejected_rows == ["Linha 4: email invalido"]


def test_load_client_records_fails_when_required_columns_missing(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path,
        "id,cliente_nome,email,status,valor,vencimento\n"
        "1,Ana,ana@example.com,ABERTO,150.00,2026-04-01\n",
    )

    with pytest.raises(ValueError, match="CSV sem colunas obrigatorias") as error:
        load_client_records(csv_path)

    assert "ultima_cobranca" in str(error.value)


def test_load_client_records_sets_aberto_when_status_column_is_missing(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path,
        "id,cliente_nome,email,valor,vencimento,ultima_cobranca\n"
        "1,Joao,joao@example.com,199.90,2026-02-01,\n",
    )

    records, rejected_rows = load_client_records(csv_path)

    assert len(records) == 1
    assert rejected_rows == []
    assert records[0].status == "ABERTO"
    assert records[0].is_valid is True


def test_load_client_records_sets_aberto_when_status_is_blank(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path,
        "id,cliente_nome,email,status,valor,vencimento,ultima_cobranca\n"
        "1,Joao,joao@example.com,,199.90,2026-02-01,\n",
    )

    records, rejected_rows = load_client_records(csv_path)

    assert len(records) == 1
    assert rejected_rows == []
    assert records[0].status == "ABERTO"
    assert records[0].is_valid is True


def test_load_client_records_from_xlsx_normalizes_excel_types(tmp_path: Path) -> None:
    xlsx_path = _write_xlsx(
        tmp_path,
        ["id", "cliente_nome", "email", "status", "valor", "vencimento", "ultima_cobranca"],
        [["7", "Bianca", "bianca@example.com", "ABERTO", 123.45, date(2026, 4, 9), datetime(2026, 4, 8, 14, 30)]],
    )

    records, rejected_rows = load_client_records_from_xlsx(xlsx_path)

    assert len(records) == 1
    assert rejected_rows == []
    assert records[0].valor == pytest.approx(123.45)
    assert records[0].vencimento == "2026-04-09"
    assert records[0].ultima_cobranca == "2026-04-08"
    assert records[0].is_valid is True
    assert records[0].selected is True


def test_load_client_records_from_xlsx_requires_expected_columns(tmp_path: Path) -> None:
    xlsx_path = _write_xlsx(
        tmp_path,
        ["id", "cliente_nome", "email", "status", "valor", "vencimento"],
        [["1", "Ana", "ana@example.com", "ABERTO", 100.0, date(2026, 4, 10)]],
    )

    with pytest.raises(ValueError, match="XLSX sem colunas obrigatorias") as error:
        load_client_records_from_xlsx(xlsx_path)

    assert "ultima_cobranca" in str(error.value)


def test_load_client_records_from_xlsx_sets_aberto_when_status_column_is_missing(tmp_path: Path) -> None:
    xlsx_path = _write_xlsx(
        tmp_path,
        ["id", "cliente_nome", "email", "valor", "vencimento", "ultima_cobranca"],
        [["1", "Ana", "ana@example.com", 100.0, date(2026, 4, 10), None]],
    )

    records, rejected_rows = load_client_records_from_xlsx(xlsx_path)

    assert len(records) == 1
    assert rejected_rows == []
    assert records[0].status == "ABERTO"


def test_load_client_records_from_file_dispatches_csv_and_xlsx(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path,
        "id,cliente_nome,email,status,valor,vencimento,ultima_cobranca\n"
        "1,Joao,joao@example.com,ABERTO,199.90,01-04-2026,\n",
    )
    xlsx_path = _write_xlsx(
        tmp_path,
        ["id", "cliente_nome", "email", "status", "valor", "vencimento", "ultima_cobranca"],
        [["2", "Marta", "marta@example.com", "ABERTO", 220.0, date(2026, 4, 11), None]],
    )

    csv_records, csv_rejected = load_client_records_from_file(csv_path)
    xlsx_records, xlsx_rejected = load_client_records_from_file(xlsx_path)

    assert len(csv_records) == 1
    assert csv_rejected == []
    assert len(xlsx_records) == 1
    assert xlsx_rejected == []
    assert xlsx_records[0].vencimento == "2026-04-11"


def test_load_client_records_from_file_rejects_unsupported_extension(tmp_path: Path) -> None:
    txt_path = tmp_path / "clientes.txt"
    txt_path.write_text("conteudo", encoding="utf-8")

    with pytest.raises(ValueError, match="Formato de arquivo nao suportado"):
        load_client_records_from_file(str(txt_path))


def test_load_client_records_from_xlsx_rejects_corrupted_zip(tmp_path: Path) -> None:
    xlsx_path = tmp_path / "corrompido.xlsx"
    xlsx_path.write_bytes(b"PK\x03\x04arquivo-incompleto")

    with pytest.raises(ValueError, match="XLSX invalido: arquivo corrompido ou formato incorreto"):
        load_client_records_from_xlsx(str(xlsx_path))


def test_load_client_records_from_xlsx_rejects_non_zip_content(tmp_path: Path) -> None:
    xlsx_path = tmp_path / "falso.xlsx"
    xlsx_path.write_text("id,cliente_nome,email", encoding="utf-8")

    with pytest.raises(ValueError, match="XLSX invalido"):
        load_client_records_from_xlsx(str(xlsx_path))


def test_load_client_records_from_file_fallbacks_to_csv_when_xlsx_is_text(tmp_path: Path) -> None:
    fake_xlsx = tmp_path / "clientes.xlsx"
    fake_xlsx.write_text(
        "id,cliente_nome,email,status,valor,vencimento,ultima_cobranca\n"
        "1,Joao,joao@example.com,ABERTO,199.90,2026-04-01,\n",
        encoding="utf-8",
    )

    records, rejected = load_client_records_from_file(str(fake_xlsx))

    assert len(records) == 1
    assert rejected == []
    assert records[0].id == "1"
    assert records[0].status == "ABERTO"


def test_load_client_records_from_file_keeps_xlsx_error_for_non_csv_garbage(tmp_path: Path) -> None:
    fake_xlsx = tmp_path / "clientes.xlsx"
    fake_xlsx.write_bytes(b"\x00\x01\x02\x03")

    with pytest.raises(ValueError, match="XLSX invalido"):
        load_client_records_from_file(str(fake_xlsx))
