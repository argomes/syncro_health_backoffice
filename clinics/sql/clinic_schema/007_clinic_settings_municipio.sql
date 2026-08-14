-- EDGW-090b: clinic_settings ganhou município/UF/timezone no SQLite local
-- (038_clinic_settings_timezone.sql) mas a tabela espelho no Postgres nunca
-- recebeu as mesmas colunas — bloqueava 100% do push de clinic_settings
-- (payload divergente, "cannot unmarshal array into Go struct field").
ALTER TABLE clinic_settings ADD COLUMN IF NOT EXISTS municipio_ibge TEXT;
ALTER TABLE clinic_settings ADD COLUMN IF NOT EXISTS municipio_nome TEXT;
ALTER TABLE clinic_settings ADD COLUMN IF NOT EXISTS uf             TEXT;
ALTER TABLE clinic_settings ADD COLUMN IF NOT EXISTS timezone       TEXT;
