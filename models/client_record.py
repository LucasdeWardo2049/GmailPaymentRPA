from dataclasses import dataclass


@dataclass(slots=True)
class ClientRecord:
    id: int
    cliente_nome: str
    email: str
    status: str
    valor: str
    vencimento: str
    ultima_cobranca: str
