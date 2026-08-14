-- EDGW-078: metadados de prontuário (medical_records) — sync PUSH-ONLY,
-- espelhando audit_log (CROSS-021: "audit_log e medical_records continuam
-- só-push"). Só metadados de referência/listagem chegam ao Backoffice —
-- NUNCA o conteúdo clínico (queixa/evolucao/cid/conduta ficam SOMENTE no
-- SQLite local da clínica). O Backoffice não guarda PHI (regra dura do
-- projeto, ver CLAUDE.md) — esta tabela deliberadamente não tem colunas
-- para o SOAP.
CREATE TABLE IF NOT EXISTS medical_records (
    id              UUID PRIMARY KEY,

    appointment_id  UUID        NOT NULL,
    patient_id      UUID        NOT NULL,
    professional_id UUID        NOT NULL,

    finalized       BOOLEAN     NOT NULL DEFAULT FALSE,
    finalized_at    TIMESTAMPTZ,

    version         INTEGER     NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ,

    device_id       TEXT        NOT NULL DEFAULT '',
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum        TEXT        NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_medical_records_patient  ON medical_records(patient_id);
CREATE INDEX IF NOT EXISTS idx_medical_records_professional ON medical_records(professional_id);
CREATE INDEX IF NOT EXISTS idx_medical_records_appointment ON medical_records(appointment_id);
