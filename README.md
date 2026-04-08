# MVP Desktop Gmail RPA (PySide + Playwright)

MVP com 3 telas:
1. Login Gmail (sessao persistente)
2. CSV Management (import, edicao, cores e selecao)
3. Personalizacao de mensagem e envio

## Requisitos
- Python 3.11+
- Chromium do Playwright

## Instalar
```bash
pip install -r requirements.txt
playwright install chromium
```

## Executar
```bash
python app.py
```

## CSV esperado
Cabecalho:

id,cliente_nome,email,status,valor,vencimento,ultima_cobranca

## Fluxo
1. Na Tela 1, informe o diretorio de perfil e clique em "Abrir Gmail e fazer login".
2. Finalize login/2FA manualmente na janela do browser aberta pelo Playwright.
3. Clique em "Validar sessao" e depois em "Avancar".
4. Na Tela 2, carregue o CSV, ajuste email/status/valor quando necessario e selecione os destinatarios.
5. Opcional: clique em "Salvar CSV editado" para exportar os ajustes e status de envio.
6. Na Tela 3, preencha Assunto/Corpo e clique em "Enviar".

## CSV Management (Tela 2)
- Linhas invalidas ficam na tabela com destaque em vermelho e checkbox desabilitado.
- Linhas ABERTO validas ficam marcadas por padrao.
- Linhas PAGO/CANCELADO ficam desmarcadas por padrao.
- Campos editaveis: cliente_nome, email, status, valor.
- Campos read-only: id, vencimento, ultima_cobranca, observacao.
- Validacao na edicao:
	- email valido (regex simples)
	- status em ABERTO/PAGO/CANCELADO
	- valor numerico >= 0
- Se o valor for invalido, a celula volta para o valor anterior (rollback).

## Exportacao CSV
Ao salvar CSV editado, o arquivo inclui as colunas originais e tambem:
- enviado_em
- envio_status
- envio_erro

## Observacoes
- O login e persistido no `userDataDir` informado na Tela 1.
- O envio e sequencial (um destinatario por vez).
- Falhas de envio sao logadas e o processo continua com o proximo cliente.
