# MVP Desktop Gmail RPA (PySide + Playwright)

MVP com 3 telas:
1. Login Gmail (sessao persistente)
2. Upload CSV
3. Selecao e envio

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
4. Na Tela 2, selecione o CSV e avance.
5. Na Tela 3, marque destinatarios, preencha Subject/Body e clique em "Enviar".

## Observacoes
- O login e persistido no `userDataDir` informado na Tela 1.
- O envio e sequencial (um destinatario por vez).
- Falhas de envio sao logadas e o processo continua com o proximo cliente.
