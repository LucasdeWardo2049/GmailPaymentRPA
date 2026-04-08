from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence

from models.client_record import ClientRecord


def _safe_int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _result_rows(summary: Mapping[str, object], records: Sequence[ClientRecord]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []

    results_obj = summary.get("results", [])
    results = results_obj if isinstance(results_obj, list) else []

    by_key: dict[tuple[str, str], list[ClientRecord]] = {}
    for record in records:
        email_key = (record.email or "").strip().lower()
        key = (record.id, email_key)
        by_key.setdefault(key, []).append(record)

    for result in results:
        if not isinstance(result, dict):
            continue

        record_id = str(result.get("id", "")).strip()
        email = str(result.get("email", "")).strip()
        key = (record_id, email.lower())

        status = "ERRO"
        if bool(result.get("skip", False)):
            status = "SKIP"
        elif bool(result.get("ok", False)):
            status = "OK"

        error_text = str(result.get("error", "")).strip()
        enviado_em = ""

        bucket = by_key.get(key)
        if bucket:
            matched_record = bucket.pop(0)
            if matched_record.envio_status:
                status = matched_record.envio_status
            if matched_record.envio_erro and not error_text:
                error_text = matched_record.envio_erro
            enviado_em = matched_record.enviado_em or ""

        rows.append(
            {
                "id": record_id,
                "email": email,
                "resultado": status,
                "erro": error_text,
                "enviado_em": enviado_em,
            }
        )

    return rows


def export_send_audit(
    summary: Mapping[str, object],
    records: Sequence[ClientRecord],
    raw_logs: str,
    base_dir: str | Path = "logs",
) -> Path:
    now = datetime.now()
    date_dir = Path(base_dir) / now.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)

    timestamp = now.strftime("%H-%M-%S")
    summary_path = date_dir / f"{timestamp}_resumo.txt"
    detail_csv_path = date_dir / f"{timestamp}_detalhado.csv"
    detail_log_path = date_dir / f"{timestamp}_log.txt"

    ok_count = _safe_int(summary.get("ok"))
    error_count = _safe_int(summary.get("error"))
    skip_count = _safe_int(summary.get("skip"))
    total_count = ok_count + error_count + skip_count

    summary_text = (
        "Auditoria de envio\n"
        f"Executado em: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"OK: {ok_count}\n"
        f"ERRO: {error_count}\n"
        f"SKIP: {skip_count}\n"
        f"TOTAL: {total_count}\n"
    )
    summary_path.write_text(summary_text, encoding="utf-8")

    rows = _result_rows(summary, records)
    with detail_csv_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=["id", "email", "resultado", "erro", "enviado_em"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    log_text = raw_logs.strip()
    detail_log_path.write_text((log_text + "\n") if log_text else "", encoding="utf-8")

    return date_dir
