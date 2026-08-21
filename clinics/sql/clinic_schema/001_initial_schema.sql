-- =========================================================
-- SYNCRO HEALTH — POSTGRESQL SCHEMA
-- Réplica do SQLite local adaptada para PostgreSQL.
-- Recebe dados via sync bidirecional do App Go.
-- Dados clínicos (clinical_notes) chegam sempre criptografados.
-- =========================================================

-- =========================================================
-- PATIENTS
-- =========================================================

CREATE TABLE IF NOT EXISTS patients (
    id                  UUID PRIMARY KEY,

    -- Campos plaintext para busca e exibição
    name                TEXT        NOT NULL,
    name_index          TEXT        NOT NULL,
    birth_date          DATE,

    -- Campos plaintext mantidos para retrocompatibilidade (registros antigos sem envelope)
    document            TEXT,
    phone               TEXT,
    email               TEXT,
    metadata            JSONB       NOT NULL DEFAULT '{}',

    -- Campos criptografados (envelope encryption — AES-256-GCM por registro)
    document_hash       TEXT        NOT NULL DEFAULT '',
    document_enc        TEXT        NOT NULL DEFAULT '',
    phone_enc           TEXT        NOT NULL DEFAULT '',
    email_enc           TEXT        NOT NULL DEFAULT '',
    metadata_enc        TEXT        NOT NULL DEFAULT '',
    dek_encrypted       TEXT        NOT NULL DEFAULT '',

    -- TISS
    tiss_card_number    TEXT,
    tiss_atendimento_rn TEXT        DEFAULT 'N',

    -- Controle
    version             INTEGER     NOT NULL DEFAULT 1,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at          TIMESTAMPTZ,

    -- Sync metadata
    device_id           TEXT        NOT NULL DEFAULT '',
    synced_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum            TEXT        NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_patients_name_index    ON patients(name_index);
CREATE INDEX IF NOT EXISTS idx_patients_document_hash ON patients(document_hash);
CREATE INDEX IF NOT EXISTS idx_patients_tiss_card     ON patients(tiss_card_number);
CREATE INDEX IF NOT EXISTS idx_patients_device        ON patients(device_id);
CREATE INDEX IF NOT EXISTS idx_patients_deleted_at    ON patients(deleted_at) WHERE deleted_at IS NULL;

-- =========================================================
-- PROFESSIONALS
-- =========================================================

CREATE TABLE IF NOT EXISTS professionals (
    id            UUID PRIMARY KEY,

    name          TEXT        NOT NULL,
    role          TEXT        NOT NULL,
    registry_type TEXT        NOT NULL,
    registry      TEXT        NOT NULL,
    status        TEXT        NOT NULL DEFAULT 'active',
    metadata      JSONB       NOT NULL DEFAULT '{}',

    -- TISS
    cbo_code      TEXT,

    version       INTEGER     NOT NULL DEFAULT 1,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at    TIMESTAMPTZ,

    device_id     TEXT        NOT NULL DEFAULT '',
    synced_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum      TEXT        NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_professionals_name     ON professionals(name);
CREATE INDEX IF NOT EXISTS idx_professionals_registry ON professionals(registry);
CREATE INDEX IF NOT EXISTS idx_professionals_status   ON professionals(status);
CREATE INDEX IF NOT EXISTS idx_professionals_device   ON professionals(device_id);

-- =========================================================
-- SPECIALTIES
-- =========================================================

CREATE TABLE IF NOT EXISTS specialties (
    id          UUID PRIMARY KEY,

    name        TEXT        NOT NULL,
    description TEXT,
    status      TEXT        NOT NULL DEFAULT 'active',
    metadata    JSONB       NOT NULL DEFAULT '{}',

    version     INTEGER     NOT NULL DEFAULT 1,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at  TIMESTAMPTZ,

    device_id   TEXT        NOT NULL DEFAULT '',
    synced_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum    TEXT        NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_specialties_name   ON specialties(name);
CREATE INDEX IF NOT EXISTS idx_specialties_status ON specialties(status);

-- =========================================================
-- PROFESSIONAL SPECIALTIES
-- =========================================================

CREATE TABLE IF NOT EXISTS professional_specialties (
    id               UUID PRIMARY KEY,

    professional_id  UUID        NOT NULL REFERENCES professionals(id),
    specialty_id     UUID        NOT NULL REFERENCES specialties(id),

    version          INTEGER     NOT NULL DEFAULT 1,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at       TIMESTAMPTZ,

    device_id        TEXT        NOT NULL DEFAULT '',
    synced_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (professional_id, specialty_id)
);

CREATE INDEX IF NOT EXISTS idx_prof_spec_professional ON professional_specialties(professional_id);
CREATE INDEX IF NOT EXISTS idx_prof_spec_specialty    ON professional_specialties(specialty_id);

-- =========================================================
-- OFFICES / CONSULTÓRIOS
-- =========================================================

CREATE TABLE IF NOT EXISTS offices (
    id          UUID PRIMARY KEY,

    name        TEXT        NOT NULL,
    description TEXT,
    status      TEXT        NOT NULL DEFAULT 'active',
    metadata    JSONB       NOT NULL DEFAULT '{}',

    version     INTEGER     NOT NULL DEFAULT 1,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at  TIMESTAMPTZ,

    device_id   TEXT        NOT NULL DEFAULT '',
    synced_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum    TEXT        NOT NULL DEFAULT ''
);

-- =========================================================
-- SCHEDULES
-- =========================================================

CREATE TABLE IF NOT EXISTS schedules (
    id              UUID PRIMARY KEY,

    professional_id UUID        NOT NULL UNIQUE REFERENCES professionals(id),
    name            TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'active',
    metadata        JSONB       NOT NULL DEFAULT '{}',

    version         INTEGER     NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ,

    device_id       TEXT        NOT NULL DEFAULT '',
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum        TEXT        NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_schedules_professional ON schedules(professional_id);

-- =========================================================
-- SCHEDULE AVAILABILITY
-- =========================================================

CREATE TABLE IF NOT EXISTS schedule_availability (
    id             UUID PRIMARY KEY,

    schedule_id    UUID        NOT NULL REFERENCES schedules(id),
    day_of_week    SMALLINT    NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
    start_time     TIME        NOT NULL,
    end_time       TIME        NOT NULL,
    slot_duration  INTEGER     NOT NULL DEFAULT 30,
    metadata       JSONB       NOT NULL DEFAULT '{}',

    version        INTEGER     NOT NULL DEFAULT 1,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at     TIMESTAMPTZ,

    device_id      TEXT        NOT NULL DEFAULT '',
    synced_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_schedule_avail_schedule ON schedule_availability(schedule_id);

-- =========================================================
-- SCHEDULE BLOCKS
-- =========================================================

CREATE TABLE IF NOT EXISTS schedule_blocks (
    id          UUID PRIMARY KEY,

    schedule_id UUID        NOT NULL REFERENCES schedules(id),
    start_at    TIMESTAMPTZ NOT NULL,
    end_at      TIMESTAMPTZ NOT NULL,
    block_type  TEXT        NOT NULL,
    reason      TEXT,
    metadata    JSONB       NOT NULL DEFAULT '{}',

    version     INTEGER     NOT NULL DEFAULT 1,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at  TIMESTAMPTZ,

    device_id   TEXT        NOT NULL DEFAULT '',
    synced_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_schedule_blocks_schedule ON schedule_blocks(schedule_id);
CREATE INDEX IF NOT EXISTS idx_schedule_blocks_period   ON schedule_blocks(start_at, end_at);

-- =========================================================
-- APPOINTMENTS
-- =========================================================

CREATE TABLE IF NOT EXISTS appointments (
    id                       UUID PRIMARY KEY,

    patient_id               UUID        NOT NULL REFERENCES patients(id),
    schedule_id              UUID        NOT NULL REFERENCES schedules(id),
    professional_id          UUID        NOT NULL REFERENCES professionals(id),

    start_time               TIMESTAMPTZ NOT NULL,
    end_time                 TIMESTAMPTZ NOT NULL,

    status                   TEXT        NOT NULL DEFAULT 'scheduled',
    appointment_type         TEXT,
    notes                    TEXT,

    -- clinical_notes NUNCA chega em plaintext — sempre criptografado com envelope
    clinical_notes_enc       TEXT        NOT NULL DEFAULT '',
    metadata                 JSONB       NOT NULL DEFAULT '{}',
    metadata_enc             TEXT        NOT NULL DEFAULT '',
    dek_encrypted            TEXT        NOT NULL DEFAULT '',

    -- TISS (campos de faturamento — não são PHI, ficam em plaintext)
    tiss_tipo_consulta       TEXT,
    tiss_codigo_procedimento TEXT,
    tiss_codigo_tabela       TEXT        DEFAULT '22',
    tiss_valor_procedimento  NUMERIC(10, 2),
    tiss_regime_atendimento  TEXT,

    version                  INTEGER     NOT NULL DEFAULT 1,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at               TIMESTAMPTZ,

    device_id                TEXT        NOT NULL DEFAULT '',
    synced_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum                 TEXT        NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_appointments_patient          ON appointments(patient_id);
CREATE INDEX IF NOT EXISTS idx_appointments_professional_time ON appointments(professional_id, start_time);
CREATE INDEX IF NOT EXISTS idx_appointments_schedule_time    ON appointments(schedule_id, start_time);
CREATE INDEX IF NOT EXISTS idx_appointments_status          ON appointments(status);
CREATE INDEX IF NOT EXISTS idx_appointments_period          ON appointments(start_time, end_time);
CREATE INDEX IF NOT EXISTS idx_appointments_device          ON appointments(device_id);
CREATE INDEX IF NOT EXISTS idx_appointments_tiss_proc       ON appointments(tiss_codigo_procedimento);

-- =========================================================
-- APPOINTMENT TRANSFERS
-- =========================================================

CREATE TABLE IF NOT EXISTS appointment_transfers (
    id               UUID PRIMARY KEY,

    appointment_id   UUID        NOT NULL REFERENCES appointments(id),
    from_schedule_id UUID        NOT NULL REFERENCES schedules(id),
    to_schedule_id   UUID        NOT NULL REFERENCES schedules(id),
    transferred_by   TEXT,
    reason           TEXT,
    metadata         JSONB       NOT NULL DEFAULT '{}',

    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    device_id        TEXT        NOT NULL DEFAULT '',
    synced_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_appt_transfers_appointment ON appointment_transfers(appointment_id);

-- =========================================================
-- USERS
-- =========================================================

CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY,

    cognito_sub     TEXT        NOT NULL UNIQUE,
    email           TEXT        NOT NULL,
    professional_id UUID        REFERENCES professionals(id),
    role            TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'active',
    metadata        JSONB       NOT NULL DEFAULT '{}',

    version         INTEGER     NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ,

    device_id       TEXT        NOT NULL DEFAULT '',
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_cognito_sub   ON users(cognito_sub);
CREATE INDEX IF NOT EXISTS idx_users_professional  ON users(professional_id);

-- =========================================================
-- AUDIT LOG
-- Dados de compliance LGPD — detail criptografado
-- =========================================================

CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL   PRIMARY KEY,

    user_id     TEXT,
    user_role   TEXT,
    action      TEXT        NOT NULL,
    entity_name TEXT        NOT NULL,
    entity_id   TEXT        NOT NULL,

    -- detail pode conter PHI — chega sempre criptografado
    detail      TEXT,

    ip_address  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    device_id   TEXT        NOT NULL DEFAULT '',
    synced_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_entity  ON audit_log(entity_name, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_user    ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_action  ON audit_log(action);
CREATE INDEX IF NOT EXISTS idx_audit_device  ON audit_log(device_id);

-- =========================================================
-- CLINIC SETTINGS
-- =========================================================

CREATE TABLE IF NOT EXISTS clinic_settings (
    id              UUID PRIMARY KEY,

    clinic_name     TEXT        NOT NULL,
    opening_time    TIME        NOT NULL,
    closing_time    TIME        NOT NULL,
    working_days    TEXT        NOT NULL,
    holidays        TEXT,
    max_slot_minutes INTEGER    NOT NULL DEFAULT 30,
    metadata        JSONB       NOT NULL DEFAULT '{}',

    version         INTEGER     NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    device_id       TEXT        NOT NULL DEFAULT '',
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum        TEXT        NOT NULL DEFAULT ''
);

-- =========================================================
-- INSURANCE OPERATORS
-- =========================================================

CREATE TABLE IF NOT EXISTS insurance_operators (
    id          UUID PRIMARY KEY,

    name        TEXT        NOT NULL,
    ans_code    TEXT        NOT NULL UNIQUE,
    cnpj        TEXT,
    active      BOOLEAN     NOT NULL DEFAULT TRUE,

    version     INTEGER     NOT NULL DEFAULT 1,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at  TIMESTAMPTZ,

    device_id   TEXT        NOT NULL DEFAULT '',
    synced_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_insurance_operators_ans  ON insurance_operators(ans_code);
CREATE INDEX IF NOT EXISTS idx_insurance_operators_name ON insurance_operators(name);

CREATE TABLE IF NOT EXISTS clinic_insurance_configs (
    id                              UUID PRIMARY KEY,

    operator_id                     UUID        NOT NULL UNIQUE REFERENCES insurance_operators(id),
    codigo_prestador_na_operadora   TEXT,
    cnes_clinica                    TEXT,

    version     INTEGER     NOT NULL DEFAULT 1,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    device_id   TEXT        NOT NULL DEFAULT '',
    synced_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =========================================================
-- TISS: TABELAS AUXILIARES ANS
-- Dados mestres — sincronizados sem criptografia
-- =========================================================

CREATE TABLE IF NOT EXISTS cbo_codes (
    code        TEXT PRIMARY KEY,
    description TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cbo_description ON cbo_codes(description);

CREATE TABLE IF NOT EXISTS procedure_codes (
    tuss_code   TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    table_code  TEXT NOT NULL DEFAULT '22'
);

CREATE INDEX IF NOT EXISTS idx_procedure_description ON procedure_codes(description);

CREATE TABLE IF NOT EXISTS cid10_codes (
    code        TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    chapter     TEXT
);

CREATE INDEX IF NOT EXISTS idx_cid10_description ON cid10_codes(description);
CREATE INDEX IF NOT EXISTS idx_cid10_chapter      ON cid10_codes(chapter);

-- =========================================================
-- FORMS (schemas JSON dinâmicos)
-- =========================================================

CREATE TABLE IF NOT EXISTS forms (
    id          BIGSERIAL   PRIMARY KEY,

    entity_name TEXT        NOT NULL,
    schema_json JSONB       NOT NULL,
    version     INTEGER     NOT NULL DEFAULT 1,
    active      BOOLEAN     NOT NULL DEFAULT TRUE,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    device_id   TEXT        NOT NULL DEFAULT '',
    synced_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (entity_name, version)
);

-- =========================================================
-- EDGE REGISTRY
-- Registro de cada instância (clínica) que sincroniza
-- =========================================================

CREATE TABLE IF NOT EXISTS edge_registry (
    device_id       TEXT        PRIMARY KEY,

    clinic_name     TEXT,
    sync_enabled    BOOLEAN     NOT NULL DEFAULT TRUE,
    last_heartbeat  TIMESTAMPTZ,
    metadata        JSONB       NOT NULL DEFAULT '{}',

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
