-- =========================================================
-- AUDIT LOG — DEK POR REGISTRO (envelope encryption)
-- detail pode conter PHI e é sempre criptografado antes do envio ao
-- PostgreSQL. Sem esta coluna, a DEK usada para cifrar detail nunca era
-- persistida, tornando o dado permanentemente indecifrável — quebrando
-- a rastreabilidade de auditoria exigida pela LGPD (Art. 37).
-- =========================================================

ALTER TABLE audit_log
    ADD COLUMN IF NOT EXISTS dek_encrypted TEXT NOT NULL DEFAULT '';
