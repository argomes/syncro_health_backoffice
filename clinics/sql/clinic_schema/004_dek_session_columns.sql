-- =========================================================
-- DEK — ENVELOPE DUPLO (ClinicKey + TemporaryKey)
-- Hoje envelopeKey() escolhe UMA das duas chaves (Chave A permanente ou
-- Chave B/TemporaryKey dinâmica) para cifrar a DEK de cada registro. Se a
-- TemporaryKey estiver ativa no momento do sync, a DEK correspondente
-- nunca mais é recuperável pela ClinicKey, quebrando o restore local
-- (disaster recovery). Estas colunas guardam, em paralelo, a DEK cifrada
-- pela TemporaryKey da sessão (dek_encrypted_session) e o identificador
-- da TemporaryKey usada (session_key_id), sem alterar dek_encrypted
-- (ClinicKey), que permanece como fonte de verdade permanente.
-- Pré-requisito de schema para TASK-039 (lógica de aplicação).
-- =========================================================

ALTER TABLE patients
    ADD COLUMN IF NOT EXISTS dek_encrypted_session TEXT;
ALTER TABLE patients
    ADD COLUMN IF NOT EXISTS session_key_id TEXT;

ALTER TABLE appointments
    ADD COLUMN IF NOT EXISTS dek_encrypted_session TEXT;
ALTER TABLE appointments
    ADD COLUMN IF NOT EXISTS session_key_id TEXT;

ALTER TABLE audit_log
    ADD COLUMN IF NOT EXISTS dek_encrypted_session TEXT;
ALTER TABLE audit_log
    ADD COLUMN IF NOT EXISTS session_key_id TEXT;
