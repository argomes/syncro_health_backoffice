-- =========================================================
-- FUNÇÕES DE UPSERT PARA SYNC BIDIRECIONAL
-- Regra: server wins em dados mestres, device wins em dados clínicos.
-- Idempotência garantida por (id, device_id, checksum).
-- =========================================================

-- Upsert de patient — ignora se checksum idêntico (sem mudança real)
CREATE OR REPLACE FUNCTION upsert_patient(
    p_id                UUID,
    p_name              TEXT,
    p_name_index        TEXT,
    p_birth_date        DATE,
    p_document          TEXT,
    p_phone             TEXT,
    p_email             TEXT,
    p_metadata          JSONB,
    p_document_hash     TEXT,
    p_document_enc      TEXT,
    p_phone_enc         TEXT,
    p_email_enc         TEXT,
    p_metadata_enc      TEXT,
    p_dek_encrypted     TEXT,
    p_tiss_card_number  TEXT,
    p_tiss_atendimento_rn TEXT,
    p_version           INTEGER,
    p_deleted_at        TIMESTAMPTZ,
    p_device_id         TEXT,
    p_checksum          TEXT
) RETURNS VOID AS $$
BEGIN
    INSERT INTO patients (
        id, name, name_index, birth_date,
        document, phone, email, metadata,
        document_hash, document_enc, phone_enc, email_enc, metadata_enc, dek_encrypted,
        tiss_card_number, tiss_atendimento_rn,
        version, deleted_at,
        device_id, synced_at, checksum
    ) VALUES (
        p_id, p_name, p_name_index, p_birth_date,
        p_document, p_phone, p_email, p_metadata,
        p_document_hash, p_document_enc, p_phone_enc, p_email_enc, p_metadata_enc, p_dek_encrypted,
        p_tiss_card_number, p_tiss_atendimento_rn,
        p_version, p_deleted_at,
        p_device_id, NOW(), p_checksum
    )
    ON CONFLICT (id) DO UPDATE SET
        name                = EXCLUDED.name,
        name_index          = EXCLUDED.name_index,
        birth_date          = EXCLUDED.birth_date,
        document            = EXCLUDED.document,
        phone               = EXCLUDED.phone,
        email               = EXCLUDED.email,
        metadata            = EXCLUDED.metadata,
        document_hash       = EXCLUDED.document_hash,
        document_enc        = EXCLUDED.document_enc,
        phone_enc           = EXCLUDED.phone_enc,
        email_enc           = EXCLUDED.email_enc,
        metadata_enc        = EXCLUDED.metadata_enc,
        dek_encrypted       = EXCLUDED.dek_encrypted,
        tiss_card_number    = EXCLUDED.tiss_card_number,
        tiss_atendimento_rn = EXCLUDED.tiss_atendimento_rn,
        version             = EXCLUDED.version,
        deleted_at          = EXCLUDED.deleted_at,
        device_id           = EXCLUDED.device_id,
        synced_at           = NOW(),
        checksum            = EXCLUDED.checksum,
        updated_at          = NOW()
    WHERE patients.checksum IS DISTINCT FROM EXCLUDED.checksum;
END;
$$ LANGUAGE plpgsql;

-- Upsert de appointment
CREATE OR REPLACE FUNCTION upsert_appointment(
    p_id                        UUID,
    p_patient_id                UUID,
    p_schedule_id               UUID,
    p_professional_id           UUID,
    p_start_time                TIMESTAMPTZ,
    p_end_time                  TIMESTAMPTZ,
    p_status                    TEXT,
    p_appointment_type          TEXT,
    p_notes                     TEXT,
    p_clinical_notes_enc        TEXT,
    p_metadata                  JSONB,
    p_metadata_enc              TEXT,
    p_dek_encrypted             TEXT,
    p_tiss_tipo_consulta        TEXT,
    p_tiss_codigo_procedimento  TEXT,
    p_tiss_codigo_tabela        TEXT,
    p_tiss_valor_procedimento   NUMERIC,
    p_tiss_regime_atendimento   TEXT,
    p_version                   INTEGER,
    p_deleted_at                TIMESTAMPTZ,
    p_device_id                 TEXT,
    p_checksum                  TEXT
) RETURNS VOID AS $$
BEGIN
    INSERT INTO appointments (
        id, patient_id, schedule_id, professional_id,
        start_time, end_time, status, appointment_type, notes,
        clinical_notes_enc, metadata, metadata_enc, dek_encrypted,
        tiss_tipo_consulta, tiss_codigo_procedimento, tiss_codigo_tabela,
        tiss_valor_procedimento, tiss_regime_atendimento,
        version, deleted_at,
        device_id, synced_at, checksum
    ) VALUES (
        p_id, p_patient_id, p_schedule_id, p_professional_id,
        p_start_time, p_end_time, p_status, p_appointment_type, p_notes,
        p_clinical_notes_enc, p_metadata, p_metadata_enc, p_dek_encrypted,
        p_tiss_tipo_consulta, p_tiss_codigo_procedimento, p_tiss_codigo_tabela,
        p_tiss_valor_procedimento, p_tiss_regime_atendimento,
        p_version, p_deleted_at,
        p_device_id, NOW(), p_checksum
    )
    ON CONFLICT (id) DO UPDATE SET
        status                   = EXCLUDED.status,
        notes                    = EXCLUDED.notes,
        clinical_notes_enc       = EXCLUDED.clinical_notes_enc,
        metadata                 = EXCLUDED.metadata,
        metadata_enc             = EXCLUDED.metadata_enc,
        dek_encrypted            = EXCLUDED.dek_encrypted,
        tiss_tipo_consulta       = EXCLUDED.tiss_tipo_consulta,
        tiss_codigo_procedimento = EXCLUDED.tiss_codigo_procedimento,
        tiss_codigo_tabela       = EXCLUDED.tiss_codigo_tabela,
        tiss_valor_procedimento  = EXCLUDED.tiss_valor_procedimento,
        tiss_regime_atendimento  = EXCLUDED.tiss_regime_atendimento,
        version                  = EXCLUDED.version,
        deleted_at               = EXCLUDED.deleted_at,
        device_id                = EXCLUDED.device_id,
        synced_at                = NOW(),
        checksum                 = EXCLUDED.checksum,
        updated_at               = NOW()
    WHERE appointments.checksum IS DISTINCT FROM EXCLUDED.checksum;
END;
$$ LANGUAGE plpgsql;

-- Upsert genérico de professional
CREATE OR REPLACE FUNCTION upsert_professional(
    p_id            UUID,
    p_name          TEXT,
    p_role          TEXT,
    p_registry_type TEXT,
    p_registry      TEXT,
    p_status        TEXT,
    p_metadata      JSONB,
    p_cbo_code      TEXT,
    p_version       INTEGER,
    p_deleted_at    TIMESTAMPTZ,
    p_device_id     TEXT,
    p_checksum      TEXT
) RETURNS VOID AS $$
BEGIN
    INSERT INTO professionals (
        id, name, role, registry_type, registry, status, metadata, cbo_code,
        version, deleted_at, device_id, synced_at, checksum
    ) VALUES (
        p_id, p_name, p_role, p_registry_type, p_registry, p_status, p_metadata, p_cbo_code,
        p_version, p_deleted_at, p_device_id, NOW(), p_checksum
    )
    ON CONFLICT (id) DO UPDATE SET
        name          = EXCLUDED.name,
        role          = EXCLUDED.role,
        registry_type = EXCLUDED.registry_type,
        registry      = EXCLUDED.registry,
        status        = EXCLUDED.status,
        metadata      = EXCLUDED.metadata,
        cbo_code      = EXCLUDED.cbo_code,
        version       = EXCLUDED.version,
        deleted_at    = EXCLUDED.deleted_at,
        device_id     = EXCLUDED.device_id,
        synced_at     = NOW(),
        checksum      = EXCLUDED.checksum,
        updated_at    = NOW()
    WHERE professionals.checksum IS DISTINCT FROM EXCLUDED.checksum;
END;
$$ LANGUAGE plpgsql;

-- =========================================================
-- HEARTBEAT DA CLÍNICA
-- =========================================================

CREATE OR REPLACE FUNCTION upsert_edge_heartbeat(
    p_device_id     TEXT,
    p_clinic_name   TEXT,
    p_sync_enabled  BOOLEAN
) RETURNS VOID AS $$
BEGIN
    INSERT INTO edge_registry (device_id, clinic_name, sync_enabled, last_heartbeat)
    VALUES (p_device_id, p_clinic_name, p_sync_enabled, NOW())
    ON CONFLICT (device_id) DO UPDATE SET
        clinic_name    = EXCLUDED.clinic_name,
        sync_enabled   = EXCLUDED.sync_enabled,
        last_heartbeat = NOW(),
        updated_at     = NOW();
END;
$$ LANGUAGE plpgsql;
