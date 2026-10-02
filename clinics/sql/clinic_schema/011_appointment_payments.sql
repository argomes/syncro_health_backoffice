-- TASK-BO-R01 — pagamentos particulares (appointment_payments), sincronizados
-- do SQLite local do gateway. Até esta migration, só o faturamento de convênio
-- chegava ao Postgres da clínica (appointments.tiss_codigo_procedimento /
-- tiss_valor_procedimento, já sincronizados via upsert_appointment desde
-- 002_sync_upsert_functions.sql) — faturamento particular nunca sincronizava.
--
-- amount/method/created_by NÃO são PHI (não são dado clínico, não há
-- diagnóstico/condição aqui) — mesma decisão já tomada para `professionals`:
-- sem envelope de criptografia por DEK, trafegam em texto puro. patient_id é
-- referência, igual appointments.patient_id já faz hoje.
--
-- =========================================================
-- TABELA: APPOINTMENT_PAYMENTS
-- =========================================================

CREATE TABLE IF NOT EXISTS appointment_payments (
    id             UUID PRIMARY KEY,

    appointment_id UUID        NOT NULL,
    patient_id     UUID        NOT NULL,
    amount         NUMERIC(10, 2) NOT NULL,
    method         TEXT        NOT NULL CHECK (method IN ('dinheiro', 'pix', 'cartao')),
    created_by     TEXT        NOT NULL,

    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at     TIMESTAMPTZ,

    device_id      TEXT        NOT NULL DEFAULT '',
    synced_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum       TEXT        NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_appointment_payments_appointment ON appointment_payments(appointment_id);
CREATE INDEX IF NOT EXISTS idx_appointment_payments_patient     ON appointment_payments(patient_id);
CREATE INDEX IF NOT EXISTS idx_appointment_payments_created_at  ON appointment_payments(created_at);

-- =========================================================
-- UPSERT: APPOINTMENT_PAYMENTS
-- =========================================================

CREATE OR REPLACE FUNCTION upsert_appointment_payment(
    p_id             UUID,
    p_appointment_id UUID,
    p_patient_id     UUID,
    p_amount         NUMERIC,
    p_method         TEXT,
    p_created_by     TEXT,
    p_created_at     TIMESTAMPTZ,
    p_deleted_at     TIMESTAMPTZ,
    p_device_id      TEXT,
    p_checksum       TEXT
) RETURNS VOID AS $$
BEGIN
    INSERT INTO appointment_payments (
        id, appointment_id, patient_id, amount, method, created_by,
        created_at, deleted_at, device_id, synced_at, checksum
    )
    VALUES (
        p_id, p_appointment_id, p_patient_id, p_amount, p_method, p_created_by,
        COALESCE(p_created_at, NOW()), p_deleted_at, p_device_id, NOW(), p_checksum
    )
    ON CONFLICT (id) DO UPDATE SET
        appointment_id = EXCLUDED.appointment_id,
        patient_id     = EXCLUDED.patient_id,
        amount         = EXCLUDED.amount,
        method         = EXCLUDED.method,
        created_by     = EXCLUDED.created_by,
        created_at     = EXCLUDED.created_at,
        deleted_at     = EXCLUDED.deleted_at,
        device_id      = EXCLUDED.device_id,
        synced_at      = NOW(),
        checksum       = EXCLUDED.checksum,
        updated_at     = NOW()
    WHERE appointment_payments.checksum IS DISTINCT FROM EXCLUDED.checksum;
END;
$$ LANGUAGE plpgsql;
