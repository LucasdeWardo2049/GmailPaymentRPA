# MVP Desktop Gmail RPA (PySide + Playwright)

MVP com 3 telas:
1. Login Gmail (sessao persistente)
2. CSV Management (import, edicao, cores e selecao)
3. Personalizacao de mensagem e envio (normal ou per-client)

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
1. Na Tela 1, clique em "Abrir Gmail e fazer login" (opcionalmente, abra "Configurar diretorio de perfil" para ajustar o Playwright).
2. Finalize login/2FA manualmente na janela do browser aberta pelo Playwright.
3. Clique em "Validar sessao" e depois em "Avancar".
4. Na Tela 2, carregue o CSV, ajuste email/status/valor quando necessario e selecione os destinatarios.
5. Opcional: clique em "Salvar CSV editado" para exportar os ajustes e status de envio.
6. Na Tela 3, escolha envio normal ou "Personalizar por cliente (placeholders)", preencha Assunto/Corpo e clique em "Enviar".
7. Ao finalizar, use "Voltar para Importacao" para retornar direto a Tela 2.

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

## Auditoria de envio
Ao finalizar cada envio, o app exporta auditoria automaticamente em pasta organizada por data:
- logs/YYYY-MM-DD/HH-MM-SS_resumo.txt
- logs/YYYY-MM-DD/HH-MM-SS_detalhado.csv
- logs/YYYY-MM-DD/HH-MM-SS_log.txt

O resumo contem totais de OK/ERRO/SKIP e o detalhado lista os registros processados.

## Tela 3 - SKIP, Modos e Placeholders
- A tabela da Tela 3 possui coluna `motivo` para exibir motivo de SKIP.
- Registros inelegiveis ficam cinza, com checkbox desabilitado, e nao entram no envio.
- A tela foi reorganizada em duas metades:
	- Metade superior: tabela de destinatarios + botoes principais de acao.
	- Metade inferior: abas para editar mensagem, visualizar preview e acompanhar logs.
- A composicao possui dois modos:
	- Modo Global: assunto/corpo unicos para todo o lote.
	- Modo por Cliente: templates por registro com placeholders.
- No modo por cliente:
	- Presets disponiveis: `Template manual`, `Cobranca amigavel`, `2o aviso`, `Aviso final` e `Ultima tentativa`.
	- Botoes clicaveis para inserir placeholders no cursor.
	- Botao `Ver Variaveis Disponiveis { }` abre um modal de ajuda com placeholders e exemplos.
	- Preview por cliente com apenas registros elegiveis selecionados.
	- Feedback visual quando template estiver invalido.
- A aba de logs de envio fica oculta e aparece automaticamente quando o envio inicia.
- Apos concluir o envio, aparece o botao `Voltar para Importacao` para retornar a Tela 2.

### Regras simples de elegibilidade (SKIP)
Um registro e elegivel apenas quando:
- status == ABERTO
- vencimento valido em DD-MM-YYYY (tambem aceita YYYY-MM-DD para compatibilidade)
- vencimento ja ocorreu (dias_atraso >= 0)
- se ultima_cobranca existir, respeita cooldown minimo de 3 dias

### Placeholders suportados
- {cliente_nome}
- {valor}
- {vencimento}
- {dias_atraso}
- {record_id}

Se o template contiver placeholder desconhecido, o envio e bloqueado com aviso.

### Exemplos de templates
Subject:
- Cobranca do cliente {cliente_nome} (ID {record_id})
- Pagamento em atraso - {dias_atraso} dia(s)

Body:
- Ola {cliente_nome},\n\nIdentificamos pendencia no valor de R$ {valor}.\nVencimento: {vencimento}.\nDias em atraso: {dias_atraso}.\n\nFavor regularizar.

## Observacoes
- O login e persistido no `userDataDir` configurado na opcao avancada da Tela 1.
- O envio e sequencial (um destinatario por vez).
- Falhas de envio sao logadas e o processo continua com o proximo cliente.
