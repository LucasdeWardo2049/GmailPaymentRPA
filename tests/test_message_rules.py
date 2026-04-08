from datetime import date

from models.client_record import ClientRecord
from services.message_rules import evaluate_record

FIXED_TODAY = date(2026, 4, 8)


def _build_record(**overrides: object) -> ClientRecord:
    payload: dict[str, object | None] = {
        "id": "CLI-001",
        "cliente_nome": "Cliente Teste",
        "email": "cliente@example.com",
        "status": "ABERTO",
        "valor": 120.0,
        "vencimento": "2026-04-01",
        "ultima_cobranca": None,
    }
    payload.update(overrides)
    return ClientRecord(**payload)  # type: ignore[arg-type]


def test_evaluate_record_skips_when_status_not_aberto() -> None:
    record = _build_record(status="PAGO")

    decision = evaluate_record(record, today=FIXED_TODAY)

    assert decision.eligible is False
    assert "status nao elegivel" in decision.reason


def test_evaluate_record_skips_when_vencimento_invalid() -> None:
    record = _build_record(vencimento="08/04/2026")

    decision = evaluate_record(record, today=FIXED_TODAY)

    assert decision.eligible is False
    assert "vencimento invalido" in decision.reason


def test_evaluate_record_skips_when_not_overdue_yet() -> None:
    record = _build_record(vencimento="10-04-2026")

    decision = evaluate_record(record, today=FIXED_TODAY)

    assert decision.eligible is False
    assert decision.dias_atraso == -2
    assert "ainda nao venceu" in decision.reason


def test_evaluate_record_skips_when_inside_cooldown() -> None:
    record = _build_record(ultima_cobranca="07-04-2026")

    decision = evaluate_record(record, today=FIXED_TODAY, cooldown_days=3)

    assert decision.eligible is False
    assert "cooldown ativo" in decision.reason
    assert "2 dia(s)" in decision.reason


def test_evaluate_record_ok_when_aberto_overdue_and_outside_cooldown() -> None:
    record = _build_record(vencimento="01-04-2026", ultima_cobranca="01-04-2026")

    decision = evaluate_record(record, today=FIXED_TODAY, cooldown_days=3)

    assert decision.eligible is True
    assert decision.reason == ""
    assert decision.dias_atraso == 7


def test_evaluate_record_accepts_iso_date_for_backward_compatibility() -> None:
    record = _build_record(vencimento="2026-04-01", ultima_cobranca="2026-04-01")

    decision = evaluate_record(record, today=FIXED_TODAY, cooldown_days=3)

    assert decision.eligible is True
    assert decision.reason == ""
    assert decision.dias_atraso == 7
