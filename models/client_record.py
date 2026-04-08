from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class ClientRecord:
    id: str
    cliente_nome: str | None
    email: str | None
    status: str | None
    valor: float | None
    vencimento: str | None
    ultima_cobranca: str | None
    observacao_erro: str = ""
    is_valid: bool = True
    selected: bool = False
    enviado_em: str | None = None
    envio_status: str | None = None
    envio_erro: str | None = None

    def mark_send_success(self) -> None:
        self.envio_status = "OK"
        self.envio_erro = None
        self.enviado_em = datetime.now().isoformat(timespec="seconds")

    def mark_send_error(self, error_message: str) -> None:
        self.envio_status = "ERRO"
        self.envio_erro = error_message
        self.enviado_em = datetime.now().isoformat(timespec="seconds")

    def mark_send_skip(self, reason: str) -> None:
        self.envio_status = "SKIP"
        self.envio_erro = reason
        self.enviado_em = datetime.now().isoformat(timespec="seconds")
