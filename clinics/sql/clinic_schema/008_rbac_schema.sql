-- EDGW-095: as tabelas de RBAC (EDGW-075: permissions/roles/role_permissions/
-- user_roles) nunca foram criadas neste schema Postgres — só "users" existe
-- desde 001_initial_schema.sql. O pull remoto de RBAC (rbac_strategy.go)
-- falha hoje com "relation "permissions" does not exist" em produção.
--
-- Estrutura espelha 013_roles_and_permissions.sql / 036_professionals_permissions.sql
-- do lado SQLite local, adaptada pra Postgres (mesmos nomes/tipos de coluna
-- assumidos por rbac_strategy.go: fetchPermissions ~L367, fetchRoles ~L393,
-- fetchRolePermissions ~L419, fetchUserRoles ~L445), seguindo o mesmo padrão
-- de version/updated_at/deleted_at + device_id/synced_at já usado por
-- professionals/specialties/users em 001_initial_schema.sql.
--
-- IMPORTANTE: mesmo permissions/role_permissions não tendo soft-delete no
-- SQLite local (013 não previu — hard-delete local, ver applyPermissions/
-- applyRolePermissions em rbac_strategy.go), as 4 tabelas remotas TÊM
-- deleted_at aqui — as queries de pull SELECIONAM deleted_at das 5 tabelas
-- (users incluída) sem exceção; é o pull local que decide fazer hard-delete
-- ao ver deleted_at preenchido, não a ausência da coluna no remoto.
--
-- NOTA DE DEPLOY: esta migration ainda precisa ser aplicada manualmente em
-- produção (Railway/Postgres do Portal Gestor) — não existe hoje nenhuma
-- automação que aplique `cloud/migrations/` no provisionamento (EDGW-091,
-- fora de escopo aqui). Aplicar com o mesmo processo manual já usado para
-- 001-007.

-- =========================================================
-- PERMISSIONS
-- Sem soft-delete no lado SQLite local (hard-delete), mas a coluna
-- deleted_at existe aqui pois fetchPermissions() sempre a seleciona.
-- =========================================================

CREATE TABLE IF NOT EXISTS permissions (
    id          UUID PRIMARY KEY,

    code        TEXT        NOT NULL UNIQUE,
    name        TEXT        NOT NULL,
    description TEXT,
    module      TEXT        NOT NULL,

    version     INTEGER     NOT NULL DEFAULT 1,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at  TIMESTAMPTZ,

    device_id   TEXT        NOT NULL DEFAULT '',
    synced_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_permissions_module ON permissions(module);

-- =========================================================
-- ROLES
-- =========================================================

CREATE TABLE IF NOT EXISTS roles (
    id          UUID PRIMARY KEY,

    clinic_id   UUID        NOT NULL,
    name        TEXT        NOT NULL,
    description TEXT,
    is_system   BOOLEAN     NOT NULL DEFAULT FALSE,
    is_archived BOOLEAN     NOT NULL DEFAULT FALSE,
    created_by  UUID        REFERENCES users(id),

    version     INTEGER     NOT NULL DEFAULT 1,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at  TIMESTAMPTZ,

    device_id   TEXT        NOT NULL DEFAULT '',
    synced_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_roles_clinic_id ON roles(clinic_id);
CREATE INDEX IF NOT EXISTS idx_roles_is_system ON roles(is_system);

-- =========================================================
-- ROLE_PERMISSIONS (tabela de junção, PK composta)
-- Sem soft-delete no lado SQLite local (hard-delete = desassociação), mas
-- deleted_at existe aqui pois fetchRolePermissions() sempre a seleciona.
-- FK explícita para permissions(id) sem ON DELETE CASCADE — mesma
-- semântica do 013 local: quem limpa a referência antes de excluir uma
-- permission é o próprio código de aplicação (applyPermissions), não o
-- banco. Aqui no remoto essa mesma disciplina deve ser respeitada pelo
-- Portal Gestor ao fazer soft/hard-delete de uma permission.
-- =========================================================

CREATE TABLE IF NOT EXISTS role_permissions (
    role_id       UUID        NOT NULL REFERENCES roles(id),
    permission_id UUID        NOT NULL REFERENCES permissions(id),

    version       INTEGER     NOT NULL DEFAULT 1,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at    TIMESTAMPTZ,

    device_id     TEXT        NOT NULL DEFAULT '',
    synced_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (role_id, permission_id)
);

CREATE INDEX IF NOT EXISTS idx_role_permissions_permission_id ON role_permissions(permission_id);

-- =========================================================
-- USER_ROLES (tabela de junção, PK composta)
-- =========================================================

CREATE TABLE IF NOT EXISTS user_roles (
    user_id     UUID        NOT NULL REFERENCES users(id),
    role_id     UUID        NOT NULL REFERENCES roles(id),
    is_primary  BOOLEAN     NOT NULL DEFAULT FALSE,
    assigned_by UUID        REFERENCES users(id),

    version     INTEGER     NOT NULL DEFAULT 1,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at  TIMESTAMPTZ,

    device_id   TEXT        NOT NULL DEFAULT '',
    synced_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (user_id, role_id)
);

CREATE INDEX IF NOT EXISTS idx_user_roles_role_id ON user_roles(role_id);
