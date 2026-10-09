-- Etapa (a). Postgres 15+. Rode com: psql "$DATABASE_URL" -f schema.sql
-- Papéis recomendados (produção): app_web (INSERT em provider_events/audit_log), app_worker (demais),
-- sem DELETE/UPDATE em audit_log e ledger_entries (reforçado por trigger abaixo).

CREATE TABLE customers (
  id               BIGSERIAL PRIMARY KEY,
  provider_account TEXT NOT NULL UNIQUE,
  phone_enc        BYTEA,
  phone_idx        TEXT UNIQUE,
  status           TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','blocked','closed')),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE provider_events (            -- inbox idempotente
  id              BIGSERIAL PRIMARY KEY,
  provider        TEXT NOT NULL,
  entity          TEXT NOT NULL,
  dedupe_key      TEXT NOT NULL,
  body_hash       TEXT NOT NULL,
  payload_enc     BYTEA NOT NULL,          -- corpo bruto cifrado (contém dados pessoais)
  status          TEXT NOT NULL CHECK (status IN ('received','retry','processing','processed','ignored','quarantined')),
  attempts        INT NOT NULL DEFAULT 0,
  next_attempt_at TIMESTAMPTZ NOT NULL,
  lease_until     TIMESTAMPTZ,
  last_error      TEXT,
  received_at     TIMESTAMPTZ NOT NULL,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (provider, dedupe_key)
);
CREATE INDEX provider_events_pending ON provider_events (next_attempt_at)
  WHERE status IN ('received','retry','processing');

CREATE TABLE ledger_entries (             -- espelho para classificação/resumos; a fonte da verdade é o provedor
  id                         BIGSERIAL PRIMARY KEY,
  customer_id                BIGINT NOT NULL REFERENCES customers(id),
  direction                  TEXT NOT NULL CHECK (direction IN ('in','out')),
  amount_cents               BIGINT NOT NULL CHECK (amount_cents > 0),
  end_to_end_id              TEXT NOT NULL,
  provider_tx_id             TEXT,
  counterparty_name_enc      BYTEA,
  counterparty_doc_idx       TEXT,
  initiation_type            TEXT,
  transaction_identification TEXT,
  occurred_at                TIMESTAMPTZ NOT NULL,
  source_event_id            BIGINT NOT NULL REFERENCES provider_events(id),
  UNIQUE (end_to_end_id, direction)
);
CREATE INDEX ledger_customer_day ON ledger_entries (customer_id, occurred_at);

CREATE TABLE outbox_messages (
  id              BIGSERIAL PRIMARY KEY,
  customer_id     BIGINT NOT NULL REFERENCES customers(id),
  kind            TEXT NOT NULL,
  payload         JSONB NOT NULL,           -- só referências (ex.: ledger_id), nunca dados pessoais
  dedupe_key      TEXT NOT NULL UNIQUE,
  status          TEXT NOT NULL CHECK (status IN ('pending','sending','sent','failed')),
  attempts        INT NOT NULL DEFAULT 0,
  next_attempt_at TIMESTAMPTZ NOT NULL,
  lease_until     TIMESTAMPTZ,
  last_error      TEXT,
  created_at      TIMESTAMPTZ NOT NULL
);
CREATE INDEX outbox_pending ON outbox_messages (next_attempt_at) WHERE status IN ('pending','sending');

CREATE TABLE audit_log (                  -- append-only, encadeada por hash
  id           BIGSERIAL PRIMARY KEY,
  ts           TIMESTAMPTZ NOT NULL,
  action       TEXT NOT NULL,
  subject_type TEXT NOT NULL,
  subject_id   TEXT NOT NULL,
  details      JSONB NOT NULL,
  prev_hash    TEXT NOT NULL,
  hash         TEXT NOT NULL UNIQUE
);

CREATE FUNCTION forbid_mutation() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'tabela % é append-only', TG_TABLE_NAME; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_log_immutable BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
-- Atenção LGPD: o livro é imutável por requisito de trilha financeira. A política de exclusão/anonimização
-- (retenção legal x pedido do titular) deve ser definida com o advogado antes da etapa (g).
CREATE TRIGGER ledger_immutable BEFORE UPDATE OR DELETE ON ledger_entries
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
