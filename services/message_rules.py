from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from models.client_record import ClientRecord


@dataclass(slots=True)
class RuleDecision:
    eligible: bool
    reason: str
    dias_atraso: int


def _parse_iso_date(raw_value: str | None) -> date | None:
    if raw_value is None:
        return None

    candidate = raw_value.strip()
    if not candidate:
        return None

    for date_format in ("%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(candidate, date_format).date()
        except ValueError:
            continue

    return None


def evaluate_record(record: ClientRecord, today: date | None = None, cooldown_days: int = 3) -> RuleDecision:
    today_value = today or date.today()
    status = (record.status or "").strip().upper()

    if status != "ABERTO":
        return RuleDecision(False, "status nao elegivel (esperado ABERTO)", 0)

    vencimento = _parse_iso_date(record.vencimento)
    if vencimento is None:
        return RuleDecision(False, "vencimento invalido (use DD-MM-YYYY ou YYYY-MM-DD)", 0)

    dias_atraso = (today_value - vencimento).days
    if dias_atraso < 0:
        return RuleDecision(False, "titulo ainda nao venceu", dias_atraso)

    ultima_cobranca_raw = (record.ultima_cobranca or "").strip()
    if not ultima_cobranca_raw:
        return RuleDecision(True, "", dias_atraso)

    ultima_cobranca = _parse_iso_date(ultima_cobranca_raw)
    if ultima_cobranca is None:
        return RuleDecision(False, "ultima_cobranca invalida (use DD-MM-YYYY ou YYYY-MM-DD)", dias_atraso)

    dias_desde_ultima = (today_value - ultima_cobranca).days
    if dias_desde_ultima < cooldown_days:
        dias_restantes = cooldown_days - dias_desde_ultima
        return RuleDecision(False, f"cooldown ativo: aguarde {dias_restantes} dia(s)", dias_atraso)

    return RuleDecision(True, "", dias_atraso)
