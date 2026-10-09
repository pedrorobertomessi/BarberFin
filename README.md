# Etapa (a): adaptador do provedor + Pix recebido

Fluxo: webhook Celcoin → `ingest` (autentica, grava cifrado, responde 200) → `worker` (confirma na API,
registra no livro, enfileira aviso) → `dispatch_outbox` (envia pelo canal de mensagens).

## Testes (rodam em qualquer máquina com Python 3.12 + `cryptography`)
    pip install -r requirements.txt
    python -m unittest discover -s tests -t .          # ou: pytest
Testes do Postgres (banco DESCARTÁVEL, nunca produção):
    docker compose up -d db
    set DATABASE_URL=postgresql://app:app@localhost:5432/barbearia      (PowerShell: $env:DATABASE_URL="...")
    python -m unittest tests.test_repo_contract -v

## Rodar localmente
1. `cp .env.example .env` e preencha (chaves: comando no próprio arquivo). Certificado mTLS em `certs/`.
2. `docker compose up -d db` (aplica `schema.sql`) e cadastre um cliente de teste:
   `INSERT INTO customers(provider_account) VALUES ('<número da conta BaaS de teste>');`
3. Web: `uvicorn app.web:app --port 8000` · Worker: `python -m app.worker_main`
4. Exponha o webhook por HTTPS e cadastre-o na Celcoin (Basic) em `/webhooks/celcoin`.

## Pendências de verificação no sandbox (NÃO confirmadas; o código segue a documentação, não a prática)
1. Que a Celcoin envia `Authorization: Basic ...` nos webhooks cadastrados como Basic (JWT: não implementado).
2. Grafia e comportamento de `GET /pix/v2/receivement/v2/status?endtoEnd=` e o campo `status` (CONFIRMED/PENDING).
3. `amount` do webhook em REAIS (deduzido do exemplo da doc: diferença de saldo = amount). Confirmar com Pix real de R$ 0,01.
4. Formato/fuso de `createTimestamp` (consulta parece MM/dd/aaaa). O sistema usa o relógio próprio para o dia comercial.
5. Se o evento pode reaparecer com o mesmo `id` e `status` diferentes (a chave inclui o status) e se `body.id` é único.
6. Frequência aceitável da consulta de recebimento: a doc recomenda usá-la só em exceção/conciliação e priorizar o
   reenvio de webhook. Confirmar com a Celcoin por escrito que ~1 consulta por Pix recebido é aceitável.

## Riscos e limites conhecidos desta etapa
- `web.py`, `postgres.py`, `http_httpx.py` e `worker_main.py` NÃO foram executados por quem escreveu (sem rede/FastAPI/Postgres
  no ambiente). A lógica de domínio é testada; essas camadas só têm o teste de contrato do Postgres.
- Sem limite de taxa nem alerta em rajadas de 401 no endpoint (entra na etapa (i)); proteja com WAF/rate limit do provedor de nuvem.
- Chaves de cifra vêm do ambiente (MVP). Evoluir para KMS com envelope encryption antes de clientes reais.
- Livro e auditoria são imutáveis por trigger: exclusão LGPD x retenção legal precisa de decisão do advogado (etapa g).
- `ConsoleChannel` imprime valores: bloqueado em `APP_ENV=production`. O WhatsApp real entra na etapa (b).
