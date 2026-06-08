# IAra — Tutorial: Como Subir e Testar Localmente

## Pré-requisitos

| Ferramenta | Versão mínima | Verificar |
|---|---|---|
| Python | 3.13 | `python3 --version` |
| uv | qualquer | `uv --version` |
| Docker + Compose | 24+ | `docker --version` |

## 1. Instalar dependências

```bash
uv sync --all-groups
```

## 2. Configurar o `.env`

```bash
cp .env.example .env
```

Abra o `.env` e preencha no mínimo:

```dotenv
# Escolha o provedor LLM
LLM_PROVIDER=openai          # ou "anthropic"

# OpenAI (se LLM_PROVIDER=openai)
OPENAI_API_KEY=sk-proj-...
OPENAI_MODEL=gpt-4o-mini

# Anthropic (se LLM_PROVIDER=anthropic)
# ANTHROPIC_API_KEY=sk-ant-...
# ANTHROPIC_MODEL=claude-sonnet-4-6
# MAX_TOKENS=4096
```

Os demais campos (`DATABASE_URL`, `RABBITMQ_URL`, etc.) já estão corretos para o Docker local.

### Tenant de desenvolvimento

Quando `IARA_ENV=development` (padrão no `.env`), o seguinte tenant é registrado automaticamente na memória, sem nenhuma configuração adicional:

| Campo | Valor |
|---|---|
| Tenant key (URL) | `test_tenant_001` |
| Account ID | `11111` |

Para usar uma key diferente, defina `IARA_DEV_TENANT_KEY` e `IARA_DEV_ACCOUNT_ID` no `.env`.

## 3. Subir a infraestrutura

```bash
make up
```

Aguarde os healthchecks (aproximadamente 15 s):

```bash
docker ps --format "table {{.Names}}\t{{.Status}}"
# NAMES            STATUS
# iara_rabbitmq    Up X seconds (healthy)
# iara_postgres    Up X seconds (healthy)
```

## 4. Criar o schema do banco

```bash
make migrate
```

Saída esperada: `INFO  [alembic.runtime.migration] Running upgrade -> 20260605_0001, initial schema`

## 5. Iniciar os serviços

### Terminal 1 — API

```bash
make run
```

Saída esperada:
```
INFO  iara_starting env=development version=0.1.0
INFO  rabbitmq_connected
INFO  Application startup complete.
```

Se o RabbitMQ não estiver no ar, `rabbitmq_unavailable_webhook_jobs_disabled` aparece, mas a API sobe normalmente. Você consegue testar eligibilidade, mas jobs não são enfileirados.

### Terminal 2 — Worker

```bash
make worker
```

Saída esperada:
```
INFO  worker_starting env=development
INFO  job_consumer_ready
INFO  outbox_drainer_ready poll_interval=5
```

### Terminal 3 — UI de teste (opcional)

```bash
make ui
# http://localhost:8501
```

## 6. Testar via UI Streamlit

Acesse `http://localhost:8501`.

A UI possui três páginas acessíveis pela sidebar:

**Home** — descrição geral do projeto e arquitetura.

**Chat** — envia mensagens diretamente ao agente LLM e exibe as respostas em tempo real. O endpoint `/chat` invoca o grafo LangGraph de forma síncrona, sem passar pela fila RabbitMQ. Ideal para iterar sobre o comportamento do agente durante o desenvolvimento.

**Webhooks** — simula payloads de webhook do Chatwoot e verifica as regras de eligibilidade.

Configuração da sidebar:
- API URL: `http://localhost:8000`
- Tenant Key: `test_tenant_001`
- Account ID: `11111`
- Conversation ID: `conv_001` (qualquer string)

Cenários disponíveis na página Webhooks:

| Cenário | Comportamento esperado |
|---|---|
| Cliente incoming | `accepted` — mensagem real de contato |
| Mensagem saindo | `rejected` — OUTGOING_MESSAGE |
| Bot sender | `rejected` — BOT_SENDER |
| Nota privada | `rejected` — PRIVATE_NOTE |
| Cross-tenant | `rejected` — ACCOUNT_MISMATCH |

## 7. Testar via curl

### Mensagem válida — aceita

```bash
curl -s -X POST http://localhost:8000/webhooks/chatwoot/test_tenant_001 \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: req-001" \
  -d '{
    "event": "message_created",
    "account": {"id": "11111", "name": "Dev"},
    "inbox": {"id": "inbox_1", "channel_type": "Channel::Whatsapp"},
    "conversation": {"id": "conv_001", "status": "open"},
    "message": {
      "content": "Ola, gostaria de agendar uma consulta",
      "message_type": "incoming",
      "private": false,
      "sender": {"type": "contact"}
    }
  }' | python3 -m json.tool
```

```json
{
  "status": "accepted",
  "correlation_id": "req-001"
}
```

### Chat síncrono — resposta do agente

```bash
curl -s -X POST http://localhost:8000/chat/test_tenant_001 \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_id": "conv-teste-01",
    "messages": [
      {"role": "user", "content": "Ola, gostaria de agendar uma consulta"}
    ]
  }' | python3 -m json.tool
```

```json
{
  "reply": "Claro! Vou verificar a disponibilidade...",
  "conversation_id": "conv-teste-01",
  "run_id": "..."
}
```

### Mensagem outgoing — rejeitada

```bash
curl -s -X POST http://localhost:8000/webhooks/chatwoot/test_tenant_001 \
  -H "Content-Type: application/json" \
  -d '{
    "event": "message_created",
    "account": {"id": "11111"},
    "inbox": {"id": "inbox_1", "channel_type": "Channel::Whatsapp"},
    "conversation": {"id": "conv_001", "status": "open"},
    "message": {
      "content": "resposta do agente",
      "message_type": "outgoing",
      "private": false,
      "sender": {"type": "agent_bot"}
    }
  }' | python3 -m json.tool
```

```json
{
  "status": "rejected",
  "reason": "OUTGOING_MESSAGE",
  "correlation_id": "..."
}
```

### Cross-tenant — account ID errado

```bash
curl -s -X POST http://localhost:8000/webhooks/chatwoot/test_tenant_001 \
  -H "Content-Type: application/json" \
  -d '{
    "event": "message_created",
    "account": {"id": "99999"},
    "inbox": {"id": "inbox_1", "channel_type": "Channel::Whatsapp"},
    "conversation": {"id": "conv_001", "status": "open"},
    "message": {
      "content": "tentativa de acesso cruzado",
      "message_type": "incoming",
      "private": false,
      "sender": {"type": "contact"}
    }
  }' | python3 -m json.tool
```

```json
{
  "status": "rejected",
  "reason": "ACCOUNT_MISMATCH",
  "correlation_id": "..."
}
```

### Tenant inexistente — HTTP 404

```bash
curl -s -X POST http://localhost:8000/webhooks/chatwoot/tenant_invalido \
  -H "Content-Type: application/json" \
  -d '{"event": "message_created"}' | python3 -m json.tool
```

### Health check

```bash
curl -s http://localhost:8000/health | python3 -m json.tool
# {"status": "ok", "service": "iara-runtime"}
```

## 8. Rodar os testes

```bash
# Testes unitários (sem infra — rápido)
make test-unit

# Invariantes de segurança (INV-01 a INV-05)
make test-security

# Todos
make test

# Com relatório HTML de cobertura
uv run pytest --cov=src/iara --cov-report=html
# Abrir: htmlcov/index.html
```

Resultado esperado: **113 testes, aproximadamente 3 s, 0 falhas**.

## 9. Verificar qualidade de código

```bash
make format    # black + ruff --fix + flake8 (formata e valida)
make lint      # ruff + flake8 (somente leitura)
make type      # mypy strict
make check     # tudo junto — gate de CI
```

## 10. Interfaces de observabilidade

| Interface | URL | Credenciais |
|---|---|---|
| FastAPI Swagger | `http://localhost:8000/docs` | somente em dev |
| RabbitMQ Management | `http://localhost:15672` | `iara` / `iara_dev` |

No RabbitMQ Management você pode:
- Inspecionar as filas `iara.jobs.conversation` e `iara.jobs.dead`
- Ver mensagens enfileiradas e dead-lettered
- Publicar mensagens manualmente para testar o worker de forma isolada

## 11. Parar tudo

```bash
# Ctrl+C nos terminais de API e worker, depois:
make down
```

## Referência de Comandos

| Comando | O que faz |
|---|---|
| `make up` | Sobe Postgres e RabbitMQ |
| `make migrate` | Aplica migrations Alembic (upgrade head) |
| `make migrate-current` | Exibe a migration atual |
| `make run` | Inicia a API (porta 8000) |
| `make worker` | Inicia os workers |
| `make ui` | Inicia a UI Streamlit (porta 8501) |
| `make test-unit` | 110 testes sem serviços externos (unit + security) |
| `make test-security` | 35 testes de invariantes de segurança |
| `make test` | Todos os 113 testes |
| `make check` | Gate completo de CI (format + lint + type + test-unit) |
| `make down` | Para a infra Docker |
| `make clean` | Remove caches e artefatos |
| `make logs` | Tail dos logs Docker |

## Invariantes de Segurança

| Invariante | Como verificar localmente |
|---|---|
| INV-01 Fail-closed | `curl` com tenant inexistente → HTTP 404 |
| INV-02 Sem cross-tenant | Cenário "Cross-tenant" na UI → `rejected` ACCOUNT_MISMATCH |
| INV-03 LLM isolado do MCP | `make test-security` |
| INV-04 Effectively-once | `make test-security` |
| INV-05 Sem PII em storage | `make test-security` |
| INV-06 Writes controlados | `IARA_CAMPAIGN_DEFAULT_MODE=draft_only` no `.env` |
| INV-07 Produção bloqueada | `IARA_PRODUCTION_AUTHORIZED=false` no `.env` |

## Resolução de Problemas

**`Tenant not found` (HTTP 404)**
Confirme que `IARA_ENV=development` está no `.env`. O tenant `test_tenant_001` é auto-registrado nesse modo.

**`rabbitmq_unavailable_webhook_jobs_disabled`**
Execute `make up` e aguarde o status `(healthy)`. Reinicie a API após o RabbitMQ estar pronto.

**Worker não processa mensagens aceitas**
Verifique se a API e o worker estão rodando simultaneamente no mesmo ambiente.
No Management UI, cheque se `iara.jobs.conversation` tem mensagens.

**`relation already exists` no migrate**
O schema já existe. Verifique o estado com `make migrate-current`.

**Modelo OpenAI não encontrado**
Confirme que `OPENAI_API_KEY` está definida no `.env` e que `OPENAI_MODEL` é um modelo válido da sua conta.

**Resposta vazia no chat**
Verifique se o servidor foi reiniciado após alterações no `.env`. As configurações de LLM usam cache e só são recarregadas com um novo processo.
