from pathlib import Path

import pytest

from rpa.csv_loader import load_client_records


def _write_csv(tmp_path: Path, content: str) -> str:
    csv_path = tmp_path / "clientes.csv"
    csv_path.write_text(content, encoding="utf-8")
    return str(csv_path)


def test_load_client_records_accepts_valid_rows(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path,
        "id,cliente_nome,email,status,valor,vencimento,ultima_cobranca\n"
        "1,Joao,joao@example.com,ABERTO,199.90,2026-04-01,\n",
    )

    records, rejected_rows = load_client_records(csv_path)

    assert len(records) == 1
    assert rejected_rows == []
    assert records[0].id == "1"
    assert records[0].is_valid is True
    assert records[0].selected is True


def test_load_client_records_rejects_invalid_id(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path,
        "id,cliente_nome,email,status,valor,vencimento,ultima_cobranca\n"
        ",Maria,maria@example.com,ABERTO,200.00,2026-04-01,\n",
    )

    records, rejected_rows = load_client_records(csv_path)

    assert len(records) == 1
    assert len(rejected_rows) == 1
    assert "id invalido" in rejected_rows[0]
    assert records[0].id == "linha-2"
    assert records[0].is_valid is False
    assert "id invalido" in records[0].observacao_erro


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
    assert records[0].is_valid is False


def test_load_client_records_fails_when_required_columns_missing(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path,
        "id,cliente_nome,email,status,valor,vencimento\n"
        "1,Ana,ana@example.com,ABERTO,150.00,2026-04-01\n",
    )

    with pytest.raises(ValueError, match="CSV sem colunas obrigatorias") as error:
        load_client_records(csv_path)

    assert "ultima_cobranca" in str(error.value)
