import pytest

from models.client_record import ClientRecord
from services.message_composer import compose_from_user_templates
from services.message_rules import RuleDecision


def _build_record(**overrides: object) -> ClientRecord:
    payload: dict[str, object | None] = {
        "id": "CLI-001",
        "cliente_nome": "Maria Souza",
        "email": "maria@example.com",
        "status": "ABERTO",
        "valor": 125.5,
        "vencimento": "2026-04-01",
        "ultima_cobranca": None,
    }
    payload.update(overrides)
    return ClientRecord(**payload)  # type: ignore[arg-type]


def test_compose_from_user_templates_substitutes_supported_placeholders() -> None:
    record = _build_record()
    decision = RuleDecision(eligible=True, reason="", dias_atraso=7)
    subject_template = "Cobranca {cliente_nome} - {record_id}"
    body_template = (
        "Cliente: {cliente_nome}\n"
        "Valor: {valor}\n"
        "Vencimento: {vencimento}\n"
        "Dias em atraso: {dias_atraso}\n"
        "ID: {record_id}"
    )

    composed = compose_from_user_templates(record, decision, subject_template, body_template)

    assert composed.subject == "Cobranca Maria Souza - CLI-001"
    assert "Cliente: Maria Souza" in composed.body
    assert "Valor: 125.50" in composed.body
    assert "Vencimento: 2026-04-01" in composed.body
    assert "Dias em atraso: 7" in composed.body
    assert "ID: CLI-001" in composed.body


def test_compose_from_user_templates_raises_for_unknown_placeholder() -> None:
    record = _build_record()
    decision = RuleDecision(eligible=True, reason="", dias_atraso=7)

    with pytest.raises(ValueError, match="Placeholder desconhecido"):
        compose_from_user_templates(
            record,
            decision,
            "Assunto {foo}",
            "Corpo",
        )
