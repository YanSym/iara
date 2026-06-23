# IAra — Runtime de Agente Conversacional Multi-Tenant

IAra é um runtime de agente conversacional seguro e pronto para produção, projetado para implantações SaaS multi-tenant. Ele se conecta ao [Chatwoot](https://www.chatwoot.com/) customizado da Digi2B via MCP, usa LangGraph para orquestrar um agente baseado em LLM e aplica sete invariantes de segurança inegociáveis em toda a pilha de chamadas.

## O que faz

```
Contato envia mensagem no Chatwoot
          │
          ▼
  [API de Webhook IAra]  --- validação de tenant --- normalização de evento --- verificação de eligibilidade
          │ aceito
          ▼
  [Fila RabbitMQ]
          │
          ▼
  [Worker IAra]  --- agente LangGraph --- 21 ferramentas governadas --- enfileiramento no outbox
          │
          ▼
  [Drainer de Outbox]  --- ChatwootMcpAdapter (JSON-RPC 2.0) --- confirmação de readback
          │
          ▼
  Resposta do agente entregue ao contato
```

## Funcionalidades

- **Multi-tenant** — cada operação é isolada e reverificada por tenant antes de qualquer efeito colateral
- **Segurança fail-closed** — 7 invariantes aplicados; qualquer ambiguidade lança `FailClosedError`, nunca um fallback permissivo
- **Orquestração LangGraph** — grafo stateful com 7 nós, roteamento condicional e memória de conversa
- **21 ferramentas de agente governadas** — agendamento, kanban, campanhas, follow-up, KB, voz, lead, histórico
- **Padrão outbox** — todos os efeitos colaterais são enfileirados no Postgres e drenados de forma assíncrona
- **Redação compatível com LGPD** — nenhum dado pessoal, segredo ou payload bruto em logs ou armazenamento durável
- **Dois provedores LLM** — Anthropic Claude ou OpenAI, configurável por implantação
- **Observabilidade Prometheus** — métricas de webhook, ferramentas e outbox em `/metrics`
- **HITL (Human-in-the-Loop)** — aprovação humana via `POST /hitl/{run_id}/approve`
- **Config publicável** — pipeline draft→publish para persona, horário, kanban e ferramentas ativas

## Início Rápido

```bash
# Requisitos: Python 3.13, uv, Docker

# 1. Instalar dependências
uv sync --all-groups

# 2. Configurar o ambiente
cp .env.example .env
# Edite o .env — no mínimo ANTHROPIC_API_KEY (ou OPENAI_API_KEY),
# CHATWOOT_ACCOUNT_ID e CHATWOOT_MCP_SLUG

# 3. Subir tudo de uma vez (infra + migrate + API + worker)
make dev

# Ou manualmente:
make up        # postgres + rabbitmq
make migrate   # alembic upgrade head
make run       # API em http://localhost:8000
make worker    # worker em background
```

A API estará em `http://localhost:8000`. Swagger UI em `http://localhost:8000/docs` (somente dev).

## Comandos de Desenvolvimento

```bash
make format        # black + ruff --fix + flake8
make lint          # ruff + flake8 (somente leitura)
make type          # mypy strict
make test          # 181 testes
make test-unit     # testes unitários (sem infra externa)
make test-security # invariantes INV-01 a INV-05
make check         # format + lint + type + test-unit (gate CI)
make dev           # sobe tudo localmente de uma vez
```

## Invariantes de Segurança

| Num | Nome | Regra |
|-----|------|-------|
| INV-01 | Fail-Closed | Qualquer ambiguidade lança `FailClosedError` antes de qualquer chamada externa |
| INV-02 | Sem Cross-Tenant | Account do provider reverificada antes de cada efeito colateral |
| INV-03 | LLM Isolado do MCP Raw | O agente vê apenas nomes lógicos de ferramentas, nunca nomes brutos da API Chatwoot |
| INV-04 | Efeitos Colaterais Efetivamente Únicos | Todas as escritas passam pelo outbox — sem mutações diretas dentro do grafo |
| INV-05 | Sem PII em Armazenamento Durável | Apenas hashes, refs e contagens em storage e logs |
| INV-06 | Escritas de Alto Risco são Controladas | Campanhas padrão em `draft_only`, kanban em `suggest_only` |
| INV-07 | Produção Bloqueada | `IARA_PRODUCTION_AUTHORIZED=true` obrigatório para qualquer caminho de produção |

## Integração com Chatwoot MCP (Digi2B)

A versão customizada do Chatwoot usada pela Digi2B expõe um MCP por tenant via HTTP com **132 tools** descobertas.

### Endpoint e autenticação

```
URL:  https://app.digi2b.com/mcp/<account_id>/<slug>
Auth: Api-Access-Token: <token>   (não Bearer)
Transport: HTTP — POST com JSON-RPC 2.0
```

### Famílias de tools disponíveis

| Família | Tools principais |
|---------|-----------------|
| Contexto | `account_context` |
| Conversas | `conversations_get`, `conversations_list`, `conversations_toggle_status`, `conversations_set_labels`, `conversations_get_labels` |
| Mensagens | `messages_list`, `conversation_message_send` (helper customizado com suporte a mídia) |
| Contatos | `contacts_get`, `contacts_update`, `contacts_search`, `contacts_filter` |
| Kanban (custom) | `kanban_boards_list`, `kanban_steps_list`, `kanban_tasks_list`, `kanban_tasks_create`, `kanban_tasks_move` |
| Captain/AI | `captain_tasks_reply_suggestion`, `captain_tasks_summarize`, `captain_tasks_label_suggestion` |
| Reports | `reports_account_overview`, `reports_v2_overview`, `audit_logs_list` |

### Duas camadas MCP (INV-03)

O LLM **nunca vê** os nomes brutos das ferramentas MCP do Chatwoot:

| Camada | Usada por | O que expõe |
|--------|-----------|------------|
| `AgentToolRegistry` | LLM / nó agent do LangGraph | Nomes lógicos: `kanban_update_status`, `qualify`, `followup_reengage_conversation`... |
| `ChatwootMcpRegistry` | Somente o Outbox Drainer | Nomes reais: `kanban_tasks_move`, `conversations_set_labels`, `conversation_message_send`... |

### Mapeamento de intents → MCP tools

| Intent (capability_name) | MCP tool | Risco |
|--------------------------|----------|-------|
| `send_message` | `conversation_message_send` | LOW_WRITE |
| `followup_reengage_conversation` | `conversation_message_send` | LOW_WRITE |
| `kanban_comment` | `conversation_message_send` (private=true) | LOW_WRITE |
| `label_conversation` | `conversations_set_labels` ¹ | LOW_WRITE |
| `kanban_update_status` | `kanban_tasks_move` | LOW_WRITE |
| `assign_conversation` | `conversation_assignments_assign` | LOW_WRITE |
| `close_conversation` | `conversations_toggle_status` | HIGH_WRITE |
| `update_contact` | `contacts_update` | HIGH_WRITE |
| `read_conversation` | `conversations_get` | READ |
| `list_kanban_boards` | `kanban_boards_list` | READ |
| `list_kanban_tasks` | `kanban_tasks_list` | READ |

¹ `conversations_set_labels` **substitui** a lista completa de labels. O adapter faz leitura prévia antes de escrever para não apagar labels existentes.

## Configuração de LLM

| Provedor | Chave | Modelo padrão |
|----------|-------|--------------|
| `anthropic` (padrão) | `ANTHROPIC_API_KEY` | `claude-sonnet-4-6` |
| `openai` | `OPENAI_API_KEY` | `gpt-4o` |

## Variáveis de Ambiente

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| `IARA_ENV` | `development` | `development`, `sandbox`, `staging`, `production` |
| `LLM_PROVIDER` | `anthropic` | `anthropic` ou `openai` |
| `ANTHROPIC_API_KEY` | | Chave direta (dev); use `ANTHROPIC_API_KEY_REF` em produção |
| `DATABASE_URL` | `postgresql+asyncpg://iara:iara_dev@localhost:5432/iara_dev` | |
| `RABBITMQ_URL` | `amqp://iara:iara_dev@localhost:5672/iara` | |
| `CHATWOOT_MCP_BASE_URL` | `https://app.digi2b.com` | Base URL do Chatwoot (sem trailing slash) |
| `CHATWOOT_ACCOUNT_ID` | | Account ID numérico (ex: `59` para suporte, `42` para oral-unic) |
| `CHATWOOT_MCP_SLUG` | | Slug do MCP server (ex: `mcp-suporte`, `oral-unic-cuiaba`) |
| `CHATWOOT_MCP_TOKEN` | | Api-Access-Token do MCP (ou use `CHATWOOT_MCP_CREDENTIAL_REF`) |
| `IARA_PRODUCTION_AUTHORIZED` | `false` | Deve ser `true` para produção |
| `IARA_KANBAN_DEFAULT_MODE` | `suggest_only` | `suggest_only`, `write_sandbox`, `write_confirmed` |
| `IARA_CAMPAIGN_DEFAULT_MODE` | `draft_only` | `draft_only`, `dry_run`, `sandbox`, `approved_send` |

## Componentes Principais

| Componente | Descrição |
|------------|-----------|
| **IAra API** (FastAPI) | Webhook receiver, HITL router (`/hitl/*`), config router (`/config/*`), metrics (`/metrics`) |
| **LangGraph Graph** | eligibility → media_understanding → context_builder → agent → tool_executor → guardrails → hitl → command_dispatch → memory_writer |
| **JobConsumerWorker** | Consome jobs do RabbitMQ e executa o grafo LangGraph |
| **OutboxDrainerWorker** | Drena `provider_command_outbox` → ChatwootMcpAdapter → readback |
| **FollowUpSchedulerWorker** | Polls `follow_up_queue` (trigger_at ≤ now) → promove para outbox |
| **ChatwootMcpAdapter** | HTTP JSON-RPC 2.0, `Api-Access-Token`, retry, `POST {base_url}/mcp/{account_id}/{slug}` |
| **GoogleCalendarWriteAdapter** | Integração Google Calendar (opcional) |
| **ClinicorpWriteAdapter** | Integração Clinicorp (opcional) |
| **NullSchedulingWriteAdapter** | Adapter no-op quando agendamento está desabilitado |
| **PostgresTenantRepository** | Resolve tenant por chave; cache TTL |
| **HitlHoldRepository** | Registra / aprova / rejeita holds HITL no Postgres |
| **FollowUpRepository** | Enfileira, fetch_due, marca sent/skipped/failed |
| **PublishService** | Pipeline async draft→publish→rollback de configuração |

## Estrutura do Projeto

```
src/iara/
├── api/                    # FastAPI + routers (webhooks, chat, admin, hitl, config)
├── config/                 # pydantic-settings (todas as variáveis)
├── config_publishing/      # Pipeline draft→publish de configuração de tenant
├── contracts/              # Modelos de domínio Pydantic v2
├── eligibility/            # Normalizador + verificador de eligibilidade (7 regras)
├── graph/                  # Orquestração LangGraph (nós + edges condicionais)
├── llm/                    # factory.py — Anthropic ou OpenAI
├── media/                  # Subgrafo de compreensão de mídia (áudio, imagem, doc)
├── memory/                 # Store de memória de conversa
├── messaging/              # Topologia RabbitMQ, publisher, consumer
├── observability/          # structlog + Prometheus metrics
├── persistence/            # ORM assíncrono SQLAlchemy 2.0 + repositórios + outbox
│   ├── models.py           # Todos os modelos ORM (incluindo hitl_holds, follow_up_queue)
│   ├── repositories/
│   │   ├── follow_up.py    # FollowUpRepository
│   │   ├── hitl_holds.py   # HitlHoldRepository
│   │   └── outbox.py       # OutboxRepository
│   └── seeds/
│       └── seed_pilot.py   # Bootstrap do tenant piloto
├── provider/               # ProviderAdapter protocol + ChatwootMcpAdapter (JSON-RPC)
│   └── chatwoot/
│       ├── mcp_adapter.py  # HTTP JSON-RPC, Api-Access-Token, retry, readback
│       ├── mcp_registry.py # Mapa intent→MCP tool
│       └── fake_mcp.py     # Stub in-memory para testes
├── security/               # redact_dict(), ContentFilter (blocklist PT-BR), guards
├── tenancy/                # TenantResolver + PostgresTenantRepository (cache TTL)
├── tools/                  # 21 ferramentas de agente
│   ├── registry.py         # AgentToolRegistry
│   ├── gateway.py          # AgentToolMcpGateway + métricas Prometheus
│   ├── policy_guard.py     # ToolPolicyGuard (kanban suggest_only, campaign draft_only)
│   ├── executor.py         # Roteamento leitura / rascunho / outbox
│   └── catalog/            # kanban, qualificação, campanhas, follow-up, kb, voz, lead, histórico
└── workers/
    ├── job_consumer.py           # Consumer RabbitMQ → runner LangGraph
    ├── outbox_drainer.py         # Outbox Postgres → ChatwootMcpAdapter → readback
    └── follow_up_scheduler.py   # Scheduler de follow-ups (polls follow_up_queue)
```

## Arquitetura

```
┌─────────────────────────────────────────────────────────────────┐
│                         IAra Runtime                             │
│                                                                  │
│  Webhook  ──► EligibilityChecker ──► RabbitMQ ──► JobConsumer  │
│  (FastAPI)                                         (LangGraph)  │
│                                                        │         │
│           ┌───────────────────────────────────────────┘         │
│           ▼                                                      │
│    ┌─────────────────────────────────────┐                      │
│    │         LangGraph Graph              │                      │
│    │  eligibility → agent → guardrails   │                      │
│    │  → hitl_node → command_dispatch     │                      │
│    └─────────────────────────────────────┘                      │
│           │                                                      │
│           ▼ (outbox writes)                                     │
│    ┌──────────────┐   ┌──────────────────────┐                 │
│    │OutboxDrainer │   │FollowUpScheduler     │                 │
│    │  chatwoot    │   │ (polls follow_up_queue│                 │
│    │  gcal        │   │  → promotes to outbox)│                 │
│    │  clinicorp   │   └──────────────────────┘                 │
│    └──────────────┘                                             │
│           │                                                      │
│           ▼ (Chatwoot MCP HTTP JSON-RPC 2.0)                   │
│    ┌──────────────────────────────────┐                         │
│    │  Chatwoot / GCal / Clinicorp     │                         │
│    └──────────────────────────────────┘                         │
└─────────────────────────────────────────────────────────────────┘
```

```
[Chatwoot] -- webhook --> [POST /webhooks/{tenant_key}]
                                │
                      TenantResolver (fail-closed, cache TTL)
                                │
                      ChatwootEventNormalizer (remove PII)
                                │
                      EligibilityChecker (7 regras)
                                │ aceito
                      [RabbitMQ: iara.jobs.conversation]
                                │
                      [Worker: JobConsumerWorker]
                                │
                      [Grafo LangGraph]
                        eligibility → media_understanding
                        → context_builder → agent
                        agent ↔ tool_executor (loop)
                        → guardrails → hitl_node → command_dispatch
                                │
                      [Postgres: provider_command_outbox]
                                │
                      [Worker: OutboxDrainerWorker]      [Worker: FollowUpSchedulerWorker]
                                │                          polls follow_up_queue → outbox
                      [ChatwootMcpAdapter]
                        POST {base_url}/mcp/{account_id}/{slug}
                        Api-Access-Token: <token>
                        JSON-RPC 2.0 — method "tools/call"
                                │
                      Chatwoot MCP (132 tools / tenant)
```

## Endpoints da API

| Método | Path | Descrição |
|--------|------|-----------|
| POST | `/webhooks/chatwoot/{tenant_key}` | Recebe eventos do Chatwoot |
| POST | `/chat/{tenant_key}` | Invocação síncrona (dev) |
| GET | `/health` | Health check |
| GET | `/ready` | Readiness probe |
| GET | `/live` | Liveness probe |
| GET | `/metrics` | Métricas Prometheus |
| GET | `/hitl/pending` | Lista holds HITL pendentes |
| POST | `/hitl/{run_id}/approve` | Aprova e retoma execução HITL |
| POST | `/config/{tenant_id}/draft` | Cria rascunho de configuração |
| POST | `/config/{tenant_id}/draft/{draft_id}/publish` | Publica configuração |
| GET | `/config/{tenant_id}/active` | Lê configuração ativa |

## Observabilidade

| Interface | URL | Observação |
|-----------|-----|------------|
| FastAPI Swagger | `http://localhost:8000/docs` | Somente em dev |
| Prometheus metrics | `http://localhost:8000/metrics` | |
| RabbitMQ Management | `http://localhost:15672` | `iara` / `iara_dev` |

Todos os logs são JSON estruturado via `structlog` com `RedactionProcessor`. Nenhum dado pessoal, token ou payload bruto aparece nos logs.

## Filtro de Conteúdo

O IAra possui um filtro de palavras proibidas em `src/iara/security/blocklist.txt`:
- Normalização de acentos e maiúsculas (accent-insensitive)
- Fronteiras de palavra (`\b`) para evitar falsos positivos
- Resposta padrão: `"Desculpe, não posso te ajudar com esse assunto."`

## Licença

Proprietário: SymCorp.
Todos os direitos reservados. Veja `LICENSE`.
