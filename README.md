# IAra — Runtime de Agente Conversacional Multi-Tenant

IAra é um runtime de agente conversacional seguro e pronto para produção, projetado para implantações SaaS multi-tenant. Conecta-se ao [Chatwoot](https://www.chatwoot.com/) customizado da Digi2B via MCP, usa LangGraph para orquestrar um agente baseado em LLM e aplica sete invariantes de segurança inegociáveis em toda a pilha.

---

## Arquitetura

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                             IAra Runtime — Visão Geral                           │
│                                                                                  │
│  ┌─────────────┐  POST /webhooks/chatwoot/{tenant_key}                          │
│  │   Chatwoot  │ ──────────────────────────────────────────────────────────┐    │
│  └─────────────┘                                                           │    │
│                                                                            ▼    │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │                           FastAPI (IAra API)                             │    │
│  │  TenantResolver ──► ChatwootEventNormalizer ──► EligibilityChecker      │    │
│  │  (Postgres-backed                              (8 regras: outgoing,     │    │
│  │   em staging/prod)                              bot, private, dedup,    │    │
│  │                                                 debounce, etc.)         │    │
│  │  Routes also: /hitl/* · /config/* · /metrics · /health · /ready        │    │
│  └───────────────────────────────────────┬─────────────────────────────────┘    │
│                                          │ aceito → RabbitMQ                    │
│                                          ▼                                       │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │                 RabbitMQ  (iara.jobs.conversation)                        │   │
│  │   DLX + retry + backoff por mensagem • fencing token por conversa        │   │
│  └───────────────────────────────────┬──────────────────────────────────────┘   │
│                                      │                                           │
│                                      ▼                                           │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │                     JobConsumerWorker                                     │   │
│  │   LeaseRepository (fencing token) → evita concorrência por conversa      │   │
│  │                        │                                                  │   │
│  │                        ▼                                                  │   │
│  │   ┌─────────────────────────────────────────────────────────────────┐    │   │
│  │   │                  LangGraph — Grafo Conversacional                │    │   │
│  │   │                                                                   │    │   │
│  │   │  START                                                            │    │   │
│  │   │    │                                                              │    │   │
│  │   │    ▼                                                              │    │   │
│  │   │  eligibility ──► [admin?] ──► command_assistant ──► END          │    │   │
│  │   │    │                                                              │    │   │
│  │   │    ▼                                                              │    │   │
│  │   │  media_understanding  (Whisper·GPT-4o vision·pypdf)              │    │   │
│  │   │    │                                                              │    │   │
│  │   │    ▼                                                              │    │   │
│  │   │  context_builder  (histórico · memória semântica · KB · config)  │    │   │
│  │   │    │                                                              │    │   │
│  │   │    ▼                                                              │    │   │
│  │   │  agent  ◄──────────────────────────────────────────────┐         │    │   │
│  │   │    │ tool calls?                                        │ loop    │    │   │
│  │   │    ▼                                                    │         │    │   │
│  │   │  tool_executor ────────────────────────────────────────┘         │    │   │
│  │   │    │ done                                                          │    │   │
│  │   │    ▼                                                              │    │   │
│  │   │  guardrails  (blocklist · anti-loop · baixa confiança)           │    │   │
│  │   │    │ hitl_requested?                                              │    │   │
│  │   │    ▼                                                              │    │   │
│  │   │  hitl_node ──► END  (persiste HitlHoldRecord no Postgres)        │    │   │
│  │   │    │ ok                                                           │    │   │
│  │   │    ▼                                                              │    │   │
│  │   │  command_dispatch ──► memory_writer ──► END                      │    │   │
│  │   │                                                                   │    │   │
│  │   └──────────────────────────┬────────────────────────────────────────┘   │   │
│  └─────────────────────────────┼─────────────────────────────────────────────┘   │
│                                │ (ProviderCommands → Postgres outbox)            │
│                                ▼                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │           Postgres  (fonte de verdade para tudo)                          │   │
│  │                                                                            │   │
│  │  provider_command_outbox  ←─ OutboxDrainerWorker ──► Chatwoot MCP        │   │
│  │  follow_up_queue          ←─ FollowUpSchedulerWorker ──► outbox          │   │
│  │  hitl_holds               ←─ hitl_node / GET /hitl/pending               │   │
│  │  agent_config_versions    ←─ PublishService (draft→publish→rollback)     │   │
│  │  agent_memory_items       ←─ PostgresMemoryStore / memory_writer         │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                │                                                  │
│                                ▼                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────┐   │
│  │  OutboxDrainerWorker  (rota por command.provider)                         │   │
│  │                                                                            │   │
│  │  "chatwoot"        ──► ChatwootMcpAdapter                                 │   │
│  │                         POST {base_url}/mcp/{account_id}/{slug}           │   │
│  │                         Api-Access-Token: <token>                         │   │
│  │                         JSON-RPC 2.0 — method "tools/call"               │   │
│  │                         ↳ readback confirm → mark done                   │   │
│  │                         ↳ 3x retry exponential backoff                   │   │
│  │                                                                            │   │
│  │  "google_calendar" ──► GoogleCalendarWriteAdapter                         │   │
│  │                         JWT service account · iCalUID=sha256(command_id) │   │
│  │                                                                            │   │
│  │  "clinicorp"       ──► ClinicorpWriteAdapter                              │   │
│  │                         API key · external_id=command_id                 │   │
│  │                                                                            │   │
│  │  T-1h reminder ──► FollowUpRepository.enqueue_raw() automático           │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### Duas camadas MCP (INV-03)

O LLM **nunca vê** nomes brutos das ferramentas MCP do Chatwoot:

| Camada | Quem usa | O que expõe |
|--------|----------|-------------|
| `AgentToolRegistry` | LLM / nó `agent` | Nomes lógicos: `kanban_update_status`, `qualify`, `followup_reengage_conversation`… |
| `ChatwootMcpRegistry` | Só o `OutboxDrainerWorker` | Nomes reais: `kanban_tasks_move`, `conversations_set_labels`, `conversation_message_send`… |

---

## Estrutura do Projeto

```
iara/
├── src/iara/
│   ├── api/                        # FastAPI app + routers
│   │   ├── app.py                  # lifespan: conecta DB/RabbitMQ
│   │   └── routers/
│   │       ├── webhooks.py         # POST /webhooks/chatwoot/{tenant_key}
│   │       ├── chat.py             # POST /chat/{tenant_key}  (dev/sync)
│   │       ├── hitl.py             # GET/POST /hitl/*
│   │       ├── config.py           # GET/POST /config/*
│   │       └── metrics.py          # GET /metrics  (Prometheus)
│   │
│   ├── config/
│   │   └── settings.py             # pydantic-settings — todas as env vars
│   │
│   ├── config_publishing/
│   │   ├── publisher.py            # PublishService: draft→publish→rollback (async, DB-backed)
│   │   ├── publisher_compat.py     # ConfigDraft shim de compatibilidade
│   │   └── registry.py            # get_service(tenant_id, session_factory)
│   │
│   ├── contracts/                  # Modelos de domínio Pydantic v2
│   │   ├── chatwoot.py             # NormalizedChatwootEvent, RawEventRef, CanonicalAttachment
│   │   ├── eligibility.py          # EligibilityDecision
│   │   ├── errors.py               # FailClosedError, CrossTenantError, LeaseConflictError…
│   │   ├── media.py                # MediaContext, MediaArtifact
│   │   ├── provider.py             # ProviderCommand, ProviderMutationResult, ProviderSecurityContext
│   │   ├── state.py                # ConversationState, TenantContext, SecurityContext
│   │   └── tools.py                # ToolInvocationRequest/Result, AgentToolDefinition
│   │
│   ├── eligibility/
│   │   ├── decision.py             # EligibilityChecker (8 regras, com idempotency+debounce)
│   │   └── normalizer.py           # ChatwootEventNormalizer → NormalizedChatwootEvent
│   │
│   ├── graph/                      # Orquestração LangGraph
│   │   ├── builder.py              # build_conversational_graph() + build_production_graph()
│   │   ├── state.py                # GraphState (response_history, hitl_reason, …)
│   │   ├── edges.py                # Funções de roteamento condicional
│   │   └── nodes/
│   │       ├── agent.py            # LLM call + bind_tools()
│   │       ├── command_assistant.py # CommandAssistantSubgraph
│   │       ├── command_dispatch.py  # Enfileira ProviderCommands ao outbox
│   │       ├── context_builder.py  # Monta ConversationContext
│   │       ├── eligibility.py      # Nó eligibility no grafo
│   │       ├── guardrails.py       # blocklist · anti-loop · baixa confiança
│   │       ├── hitl.py             # hitl_node: persiste HitlHoldRecord → END
│   │       ├── media_understanding.py # MediaUnderstandingSubgraph wrapper
│   │       ├── memory_writer.py    # Extrai fatos da conversa → agent_memory_items
│   │       └── tool_executor.py    # tool_executor_node wrapper
│   │
│   ├── llm/
│   │   └── factory.py              # build_llm(): Anthropic ou OpenAI
│   │
│   ├── media/
│   │   └── subgraph.py             # MediaUnderstandingSubgraph: Whisper/GPT-4o/pypdf
│   │
│   ├── memory/
│   │   └── postgres_store.py       # PostgresMemoryStore: TTL, namespace, redaction
│   │
│   ├── messaging/
│   │   ├── consumer.py             # MessageConsumer (aio_pika)
│   │   ├── publisher.py            # MessagePublisher + ConversationJob
│   │   └── topology.py             # Declaração de exchanges, queues, DLX
│   │
│   ├── observability/
│   │   ├── logging.py              # structlog + RedactionProcessor
│   │   └── metrics.py              # Counters/histograms Prometheus
│   │
│   ├── persistence/
│   │   ├── models.py               # Todos os modelos ORM SQLAlchemy 2.0
│   │   │                           #   Tenant · ProviderAccount · ProviderInbox
│   │   │                           #   EventReceipt · ConversationDebounce · ConversationRunLease
│   │   │                           #   AgentRun · RuntimeRunStep · RuntimeError
│   │   │                           #   ProviderCommandOutbox · SafeAuditEvent · AgentMemoryItem
│   │   │                           #   AgentProfile · AgentConfigVersion · ConfigPublication
│   │   │                           #   HitlHoldRecord · FollowUpQueueItem
│   │   ├── checkpointer.py         # postgres_checkpointer() para LangGraph
│   │   ├── services/
│   │   │   └── outbox_service.py   # OutboxService (wraps OutboxRepository)
│   │   ├── repositories/
│   │   │   ├── debounce.py         # DebounceRepository
│   │   │   ├── follow_up.py        # FollowUpRepository
│   │   │   ├── hitl_holds.py       # HitlHoldRepository
│   │   │   ├── idempotency.py      # IdempotencyRepository
│   │   │   ├── leases.py           # LeaseRepository (fencing token)
│   │   │   └── outbox.py           # OutboxRepository
│   │   └── seeds/
│   │       └── seed_pilot.py       # Bootstrap idempotente do tenant piloto
│   │
│   ├── provider/
│   │   ├── chatwoot/
│   │   │   ├── mcp_adapter.py      # ChatwootMcpAdapter: HTTP JSON-RPC 2.0, retry, readback
│   │   │   ├── mcp_registry.py     # ChatwootMcpRegistry: intent → capability → MCP tool
│   │   │   └── fake_mcp.py         # Stub in-memory para testes
│   │   └── scheduling/
│   │       ├── protocol.py         # SchedulingAdapter protocol
│   │       ├── write_adapter.py    # SchedulingWriteAdapter protocol + NullSchedulingWriteAdapter
│   │       ├── factory.py          # build_scheduling_adapter / build_*_write_adapter
│   │       ├── null_adapter.py     # NullSchedulingAdapter (read-only)
│   │       ├── google_calendar.py  # GoogleCalendarAdapter (read)
│   │       ├── google_calendar_write.py # GoogleCalendarWriteAdapter (write)
│   │       ├── clinicorp.py        # ClinicorpAdapter (read)
│   │       └── clinicorp_write.py  # ClinicorpWriteAdapter (write)
│   │
│   ├── security/
│   │   ├── blocklist.txt           # Palavras proibidas (PT-BR)
│   │   ├── command_auth.py         # CommandAuthorizationGuard + CommandRequesterBinding
│   │   ├── content_filter.py       # ContentFilter: accent-insensitive, word-boundary
│   │   └── redaction.py            # redact_dict(): remove campos sensíveis de dicts
│   │
│   ├── tenancy/
│   │   ├── resolver.py             # TenantResolver + InMemoryTenantRepository (dev)
│   │   └── postgres_repository.py  # PostgresTenantRepository (staging/prod)
│   │
│   └── tools/
│       ├── registry.py             # AgentToolRegistry: 20 tools registradas
│       ├── gateway.py              # AgentToolMcpGateway + métricas Prometheus
│       ├── policy_guard.py         # ToolPolicyGuard: kanban/campaign modes
│       ├── executor.py             # ToolExecutor: read · draft · outbox · followup_queue
│       ├── skill_resolver.py       # ToolSkillResolver
│       └── catalog/
│           ├── campaigns.py        # campaign_create_draft · validate_audience · dispatch_batch…
│           ├── followup.py         # build_followup_schedule_payload (quiet hours, opt-out)
│           ├── history.py          # history_analyze_conversations
│           ├── kanban.py           # kanban_analyze_conversation · build_kanban_update_command
│           ├── kb.py               # kb_suggest_update
│           ├── lead.py             # lead_search
│           ├── qualification.py    # qualify · disqualify
│           ├── scheduling.py       # handle_availability · schedule/cancel/reschedule commands
│           └── voice.py            # voice_respond_audio
│
├── workers/
│   └── src/iara/workers/
│       ├── main.py                 # Ponto de entrada: 3 tasks assíncronas
│       ├── job_consumer.py         # JobConsumerWorker: RabbitMQ → LangGraph (com lease)
│       ├── outbox_drainer.py       # OutboxDrainerWorker: outbox → chatwoot/gcal/clinicorp
│       └── follow_up_scheduler.py  # FollowUpSchedulerWorker: follow_up_queue → outbox (30s poll)
│
├── migrations/
│   └── versions/
│       ├── 20260605_0001_initial_schema.py        # tenants · provider_accounts · …
│       ├── 20260608_0002_memory_items_and_command_auth.py
│       ├── 20260614_0003_hitl_holds.py
│       ├── 20260623_0004_follow_up_queue_and_hitl_context.py
│       └── 20260623_0005_add_config_data_to_versions.py
│
├── tests/
│   ├── unit/                       # Testes de unidade (sem infra externa)
│   ├── integration/                # Testes com DB/RabbitMQ fake
│   └── security/                   # Invariantes INV-01 a INV-07
│
└── docs/
    ├── architecture.md             # Componentes + fluxo detalhado
    ├── configuration.md            # Variáveis de ambiente por tenant
    ├── der.md                      # Diagrama entidade-relacionamento lógico
    ├── gate_acceptance_checklists.md # G0–G6: evidências, checklists, critérios
    ├── g0_kickoff_template.md      # Template de decisão G0 (Breno Cocheto assina)
    ├── INVARIANTS.md               # INV-01 a INV-07 com exemplos de código
    ├── rollback.md                 # Procedimentos de rollback operacional
    ├── runbook.md                  # Operação, health checks, HITL workflow
    └── secrets.md                  # Gestão de credential_ref / secret://
```

---

## Invariantes de Segurança

| # | Nome | Regra |
|---|------|-------|
| INV-01 | Fail-Closed | Qualquer ambiguidade lança `FailClosedError` antes de qualquer chamada externa |
| INV-02 | Sem Cross-Tenant | `tenant_id` re-verificado antes de cada efeito colateral |
| INV-03 | LLM Isolado do MCP Raw | LLM vê apenas nomes lógicos; nunca nomes brutos da API Chatwoot |
| INV-04 | Efeitos Colaterais Únicos | Todas as escritas passam pelo outbox — sem mutações diretas no grafo |
| INV-05 | Sem PII em Storage | Apenas hashes SHA-256, refs e contagens em storage e logs |
| INV-06 | Escritas High-Risk Controladas | Campanhas em `draft_only`; kanban em `suggest_only` por padrão |
| INV-07 | Produção Bloqueada | `IARA_PRODUCTION_AUTHORIZED=true` obrigatório para qualquer caminho prod |

---

## Tools do Agente (20 registradas)

| Tool | Tipo | Risco | Descrição |
|------|------|-------|-----------|
| `availability` | READ | — | Consulta slots disponíveis no provider de agenda |
| `schedule` | WRITE | HIGH | Agenda consulta (outbox → google_calendar / clinicorp) |
| `cancel` | WRITE | HIGH | Cancela agendamento |
| `reschedule` | WRITE | HIGH | Reagenda |
| `qualify` | WRITE | LOW | Qualifica lead: label + nota privada + notificação |
| `disqualify` | WRITE | LOW | Desqualifica lead com motivo |
| `kanban_analyze_conversation` | READ | — | Sugere estágio kanban (suggest_only) |
| `kanban_update_status` | WRITE | LOW | Atualiza estágio kanban (policy guard) |
| `kanban_comment` | WRITE | LOW | Nota privada no kanban |
| `lead_search` | READ | — | Busca lead (sem PII — só contagens) |
| `history_analyze_conversations` | READ | — | Análise histórica sanitizada |
| `followup_reengage_conversation` | WRITE | LOW | Agenda follow-up deferido → `follow_up_queue` |
| `campaign_create_draft` | WRITE | HIGH | Cria rascunho de campanha |
| `campaign_validate_audience` | READ | — | Valida público-alvo |
| `campaign_request_approval` | WRITE | HIGH | Solicita aprovação HITL |
| `campaign_dispatch_batch` | WRITE | HIGH | Dispara lote (só `approved_send`) |
| `campaign_status` | READ | — | Status da campanha |
| `campaign_cancel_pending` | WRITE | HIGH | Cancela envios pendentes |
| `kb_suggest_update` | WRITE | MED | Gera KbUpdateDraft para revisão |
| `voice_respond_audio` | WRITE | MED | Resposta em áudio (TTS + policy + fallback texto) |

---

## Mapeamento Intent → Chatwoot MCP

| Intent (`capability_name`) | MCP tool | Risco |
|----------------------------|----------|-------|
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

¹ `conversations_set_labels` **substitui** a lista completa — o adapter faz leitura prévia para não apagar labels existentes.

---

## Início Rápido

```bash
# Requisitos: Python 3.13, uv, Docker

uv sync --all-groups
cp .env.example .env
# Configure ANTHROPIC_API_KEY, CHATWOOT_ACCOUNT_ID, CHATWOOT_MCP_SLUG no .env

make dev           # postgres + rabbitmq + migrate + API + worker (tudo de uma vez)

# Ou passo a passo:
make up            # postgres + rabbitmq
make migrate       # alembic upgrade head
make seed-pilot    # cria tenant piloto (idempotente)
make run           # API em http://localhost:8000
make worker        # worker em background
```

API: `http://localhost:8000` — Swagger: `http://localhost:8000/docs` (dev only)

---

## Comandos de Desenvolvimento

```bash
make format        # black + ruff --fix
make lint          # ruff (leitura)
make type          # mypy strict
make test          # 181 testes
make test-unit     # só unitários (sem infra)
make test-security # invariantes INV-01 a INV-07
make check         # format + lint + type + test-unit (gate CI)
make seed-pilot    # bootstrap tenant piloto (idempotente)
make dev           # sobe tudo localmente
```

---

## Variáveis de Ambiente

### Obrigatórias

| Variável | Descrição |
|----------|-----------|
| `ANTHROPIC_API_KEY` | Chave Anthropic (ou use `OPENAI_API_KEY`) |
| `CHATWOOT_ACCOUNT_ID` | Account ID numérico do Chatwoot (ex: `59`) |
| `CHATWOOT_MCP_SLUG` | Slug do MCP server (ex: `mcp-suporte`) |
| `CHATWOOT_MCP_TOKEN` | `Api-Access-Token` do MCP |

### Runtime

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| `IARA_ENV` | `development` | `development`, `sandbox`, `staging`, `production` |
| `LLM_PROVIDER` | `anthropic` | `anthropic` ou `openai` |
| `DATABASE_URL` | `postgresql+asyncpg://iara:iara_dev@localhost:5432/iara_dev` | |
| `RABBITMQ_URL` | `amqp://iara:iara_dev@localhost:5672/iara` | |
| `CHATWOOT_MCP_BASE_URL` | `https://app.digi2b.com` | Base URL sem trailing slash |
| `IARA_PRODUCTION_AUTHORIZED` | `false` | `true` obrigatório para produção (INV-07) |
| `IARA_KANBAN_DEFAULT_MODE` | `suggest_only` | `suggest_only`, `write_sandbox`, `write_confirmed` |
| `IARA_CAMPAIGN_DEFAULT_MODE` | `draft_only` | `draft_only`, `dry_run`, `sandbox`, `approved_send` |

### Tenant Piloto

| Variável | Descrição |
|----------|-----------|
| `IARA_PILOT_TENANT_ID` | UUID do tenant piloto |
| `IARA_PILOT_WEBHOOK_KEY` | Chave webhook (sha256 salvo em `tenants.webhook_key_hash`) |

### Agenda (opcionais)

| Variável | Descrição |
|----------|-----------|
| `GOOGLE_CALENDAR_ENABLED` | `true` para ativar |
| `GOOGLE_CALENDAR_CREDENTIAL_REF` | `secret://google_calendar/service_account_json` |
| `CLINICORP_ENABLED` | `true` para ativar |
| `CLINICORP_CREDENTIAL_REF` | `secret://clinicorp/api_key` |

---

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
| POST | `/hitl/{run_id}/approve` | Aprova e retoma execução parada |
| POST | `/hitl/{run_id}/reject` | Rejeita hold HITL |
| POST | `/config/{tenant_id}/draft` | Cria rascunho de configuração |
| POST | `/config/{tenant_id}/draft/{draft_id}/publish` | Publica configuração |
| GET | `/config/{tenant_id}/active` | Lê configuração ativa |
| POST | `/config/{tenant_id}/rollback/{publication_id}` | Rollback para publicação anterior |

---

## Modelo de Dados (tabelas implementadas)

```
tenants                     ── raiz; webhook_key_hash (sha256)
  └── provider_accounts     ── conta Chatwoot por tenant
        └── provider_inboxes

event_receipts              ── idempotência de eventos
conversation_debounce       ── janela de debounce por conversa
conversation_run_leases     ── fencing token (evita concorrência)
agent_runs                  ── registro de runs LangGraph
runtime_run_steps           ── steps por nó do grafo
runtime_errors              ── erros sanitizados
safe_audit_events           ── auditoria sem PII

provider_command_outbox     ── outbox de comandos (chatwoot/gcal/clinicorp)
follow_up_queue             ── fila de follow-ups agendados
hitl_holds                  ── holds HITL com status (pending/approved/rejected)
agent_memory_items          ── memória semântica por tenant + namespace

agent_profiles              ── perfil de agente por tenant
agent_config_versions       ── versões de config (status: draft/published)
config_publications         ── publicações ativas (imutável; rollback = nova pub)
```

---

## Workers

| Worker | Intervalo | Função |
|--------|-----------|--------|
| `JobConsumerWorker` | contínuo (RabbitMQ push) | Consome jobs, adquire lease, executa grafo LangGraph |
| `OutboxDrainerWorker` | 5s poll | Drena `provider_command_outbox` → adapter certo → readback |
| `FollowUpSchedulerWorker` | 30s poll | Promove itens com `trigger_at ≤ now` de `follow_up_queue` para outbox |

---

## Observabilidade

| Interface | URL | Nota |
|-----------|-----|------|
| Swagger UI | `http://localhost:8000/docs` | Somente `IARA_ENV=development` |
| Prometheus | `http://localhost:8000/metrics` | `webhook_requests_total`, `tool_invocations_total`… |
| RabbitMQ Management | `http://localhost:15672` | `iara` / `iara_dev` |

Logs são JSON estruturado via `structlog` com `RedactionProcessor`. Nenhum dado pessoal, token ou payload bruto aparece em logs ou evidências.

---

## Integrações Chatwoot MCP

Endpoint e autenticação:
```
URL:  {CHATWOOT_MCP_BASE_URL}/mcp/{CHATWOOT_ACCOUNT_ID}/{CHATWOOT_MCP_SLUG}
Auth: Api-Access-Token: <token>   (não Bearer)
Transport: HTTP POST — JSON-RPC 2.0
```

| Família | Tools principais disponíveis |
|---------|------------------------------|
| Contexto | `account_context` |
| Conversas | `conversations_get`, `conversations_list`, `conversations_toggle_status` |
| Mensagens | `messages_list`, `conversation_message_send` |
| Contatos | `contacts_get`, `contacts_update`, `contacts_search` |
| Kanban | `kanban_boards_list`, `kanban_steps_list`, `kanban_tasks_list`, `kanban_tasks_create`, `kanban_tasks_move` |
| Captain/AI | `captain_tasks_reply_suggestion`, `captain_tasks_summarize` |
| Reports | `reports_account_overview`, `audit_logs_list` |

---

## Licença

Proprietário: SymCorp. Todos os direitos reservados.
