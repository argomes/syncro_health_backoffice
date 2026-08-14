-- =========================================================
-- UPSERT: SCHEDULES
-- =========================================================

CREATE OR REPLACE FUNCTION upsert_schedule(
    p_id              UUID,
    p_professional_id UUID,
    p_name            TEXT,
    p_status          TEXT,
    p_metadata        JSONB,
    p_version         INTEGER,
    p_deleted_at      TIMESTAMPTZ,
    p_device_id       TEXT,
    p_checksum        TEXT
) RETURNS VOID AS $$
BEGIN
    INSERT INTO schedules (
        id, professional_id, name, status, metadata,
        version, deleted_at, device_id, synced_at, checksum
    )
    VALUES (
        p_id, p_professional_id, p_name, p_status, COALESCE(p_metadata, '{}'),
        p_version, p_deleted_at, p_device_id, NOW(), p_checksum
    )
    ON CONFLICT (id) DO UPDATE SET
        professional_id = EXCLUDED.professional_id,
        name            = EXCLUDED.name,
        status          = EXCLUDED.status,
        metadata        = EXCLUDED.metadata,
        version         = EXCLUDED.version,
        deleted_at      = EXCLUDED.deleted_at,
        device_id       = EXCLUDED.device_id,
        synced_at       = NOW(),
        checksum        = EXCLUDED.checksum,
        updated_at      = NOW()
    WHERE schedules.checksum IS DISTINCT FROM EXCLUDED.checksum;
END;
$$ LANGUAGE plpgsql;
