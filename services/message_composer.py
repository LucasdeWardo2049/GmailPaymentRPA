from __future__ import annotations

from dataclasses import dataclass
from string import Formatter

from models.client_record import ClientRecord
from services.message_rules import RuleDecision

SUPPORTED_PLACEHOLDERS = ("cliente_nome", "valor", "vencimento", "dias_atraso", "record_id")


@dataclass(slots=True)
class ComposedEmail:
    record: ClientRecord
    subject: str
    body: str


def _format_valor(valor: float | None) -> str:
    if valor is None:
        return ""
    return f"{valor:.2f}"


def _extract_placeholder_names(template: str) -> set[str]:
    formatter = Formatter()
    names: set[str] = set()

    for _, field_name, _, _ in formatter.parse(template):
        if not field_name:
            continue
        root = field_name.split(".")[0].split("[")[0]
        if root:
            names.add(root)

    return names


def validate_templates(subject_template: str, body_template: str) -> None:
    placeholders = _extract_placeholder_names(subject_template) | _extract_placeholder_names(body_template)
    unknown = sorted(name for name in placeholders if name not in SUPPORTED_PLACEHOLDERS)

    if not unknown:
        return

    supported = ", ".join(SUPPORTED_PLACEHOLDERS)
    unknown_text = ", ".join(f"{{{name}}}" for name in unknown)
    raise ValueError(f"Placeholder desconhecido {unknown_text}. Use apenas: {supported}")


def compose_from_user_templates(
    record: ClientRecord,
    decision: RuleDecision,
    subject_template: str,
    body_template: str,
) -> ComposedEmail:
    validate_templates(subject_template, body_template)

    payload = {
        "cliente_nome": record.cliente_nome or "",
        "valor": _format_valor(record.valor),
        "vencimento": record.vencimento or "",
        "dias_atraso": max(0, decision.dias_atraso),
        "record_id": record.id,
    }

    try:
        subject = subject_template.format(**payload)
        body = body_template.format(**payload)
    except KeyError as error:
        key = str(error).strip("'\"")
        supported = ", ".join(SUPPORTED_PLACEHOLDERS)
        raise ValueError(f"Placeholder desconhecido {{{key}}}. Use apenas: {supported}") from error
    except Exception as error:  # noqa: BLE001
        raise ValueError(f"Erro ao compor mensagem: {error}") from error

    return ComposedEmail(record=record, subject=subject, body=body)
