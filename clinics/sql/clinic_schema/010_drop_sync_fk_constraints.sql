-- Remove FK constraints que dependem de ordem de chegada no sync.
-- Em arquitetura local-first, a integridade referencial é garantida pelo
-- SQLite local. O PostgreSQL é um destino de sync — não pode rejeitar
-- registros por chegarem fora de ordem.

ALTER TABLE appointments
    DROP CONSTRAINT IF EXISTS appointments_schedule_id_fkey,
    DROP CONSTRAINT IF EXISTS appointments_patient_id_fkey,
    DROP CONSTRAINT IF EXISTS appointments_professional_id_fkey;

ALTER TABLE schedule_availability
    DROP CONSTRAINT IF EXISTS schedule_availability_schedule_id_fkey;

ALTER TABLE schedule_blocks
    DROP CONSTRAINT IF EXISTS schedule_blocks_schedule_id_fkey;

ALTER TABLE schedules
    DROP CONSTRAINT IF EXISTS schedules_professional_id_fkey;
