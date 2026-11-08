-- DWH рынка вакансий Data & Analytics (база: hh_dwh)
-- Слои: STG (Staging JSONB) -> DDS (Detailed Data Store) -> DMA (Data Marts)

CREATE SCHEMA IF NOT EXISTS stg;
CREATE SCHEMA IF NOT EXISTS dds;
CREATE SCHEMA IF NOT EXISTS dma;

-- Additive bootstrap: existing data and dependent views are preserved.

-- 1. Слой STG (Staging Area: уникальные слепки по vacancy_id)
CREATE TABLE IF NOT EXISTS stg.hh_dictionaries (
    dict_key        VARCHAR(50) PRIMARY KEY,
    payload         JSONB NOT NULL,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS stg.hh_vacancies (
    vacancy_id      BIGINT PRIMARY KEY,         -- Жесткая защита от дублей на уровне СУБД
    role_family     VARCHAR(50) NOT NULL,       -- 4 макро-направления (BI, DE, DA, DS/ML)
    role_title      VARCHAR(50) NOT NULL,       -- 9 чистых специализаций
    search_query    VARCHAR(100) NOT NULL,      -- Каким запросом была найдена впервые
    payload         JSONB NOT NULL,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_stg_vac_family ON stg.hh_vacancies(role_family);
CREATE INDEX IF NOT EXISTS idx_stg_vac_role ON stg.hh_vacancies(role_title);

-- 2. Слой DDS (Detailed Data Store)
CREATE TABLE IF NOT EXISTS dds.dim_employers (
    employer_id         BIGINT PRIMARY KEY,
    employer_name       VARCHAR(255) NOT NULL,
    is_trusted          BOOLEAN NOT NULL DEFAULT FALSE,
    is_it_accredited    BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS dds.dim_areas (
    area_id             INTEGER PRIMARY KEY,
    area_name           VARCHAR(150) NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS dds.fct_vacancies (
    vacancy_id          BIGINT PRIMARY KEY,
    title               VARCHAR(300) NOT NULL,
    role_family         VARCHAR(50) NOT NULL,   -- BI & Reporting, Data Engineering, Data Analytics, Data Science & ML
    role_title          VARCHAR(50) NOT NULL,   -- BI Developer, BI Analyst, Data Engineer, DWH / ETL Developer...
    grade               VARCHAR(20) NOT NULL,   -- Junior, Middle, Senior, Lead
    employer_id         BIGINT REFERENCES dds.dim_employers(employer_id),
    area_id             INTEGER REFERENCES dds.dim_areas(area_id),
    experience_id       VARCHAR(50) NOT NULL,
    schedule_id         VARCHAR(50) NOT NULL,
    employment_id       VARCHAR(50) NOT NULL,
    has_salary          BOOLEAN NOT NULL,
    salary_currency     VARCHAR(10),
    salary_gross        BOOLEAN,
    salary_from_net_rub NUMERIC(12, 2),
    salary_to_net_rub   NUMERIC(12, 2),
    salary_est_net_rub  NUMERIC(12, 2),
    skills_array        TEXT[],
    published_at        TIMESTAMPTZ NOT NULL,
    alternate_url       TEXT NOT NULL,
    loaded_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS dds.bridge_vacancy_skills (
    vacancy_id          BIGINT REFERENCES dds.fct_vacancies(vacancy_id) ON DELETE CASCADE,
    skill_name          VARCHAR(50) NOT NULL,
    skill_category      VARCHAR(50) NOT NULL,
    PRIMARY KEY (vacancy_id, skill_name)
);

CREATE INDEX IF NOT EXISTS idx_fct_vac_role_grade ON dds.fct_vacancies(role_title, grade);
CREATE INDEX IF NOT EXISTS idx_fct_vac_family ON dds.fct_vacancies(role_family);
CREATE INDEX IF NOT EXISTS idx_bridge_skill_name ON dds.bridge_vacancy_skills(skill_name);

CREATE TABLE IF NOT EXISTS dds.dq_audit_log (
    run_id              SERIAL PRIMARY KEY,
    stage_name          VARCHAR(50) NOT NULL,
    started_at          TIMESTAMPTZ NOT NULL,
    finished_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    rows_processed      INTEGER NOT NULL,
    salary_coverage_pct NUMERIC(5, 2),
    skills_coverage_pct NUMERIC(5, 2),
    status              VARCHAR(20) NOT NULL,
    details             TEXT
);
-- Additive compatibility migration for the original v0.1 schema.
ALTER TABLE dds.fct_vacancies
    ADD COLUMN IF NOT EXISTS salary_est_gross_rub NUMERIC(12, 2),
    ADD COLUMN IF NOT EXISTS effective_ndfl_pct NUMERIC(5, 2),
    ADD COLUMN IF NOT EXISTS salary_disclosed BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS salary_status TEXT NOT NULL DEFAULT 'legacy_unchecked',
    ADD COLUMN IF NOT EXISTS salary_est_method TEXT NOT NULL DEFAULT 'legacy',
    ADD COLUMN IF NOT EXISTS grade_source TEXT NOT NULL DEFAULT 'legacy';

ALTER TABLE stg.hh_vacancies
    ADD COLUMN IF NOT EXISTS enrichment_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (enrichment_status IN ('pending', 'success', 'retry', 'blocked', 'not_found', 'failed')),
    ADD COLUMN IF NOT EXISTS enrichment_attempts INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS enriched_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_http_status INTEGER,
    ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_error TEXT,
    ADD COLUMN IF NOT EXISTS detail_payload JSONB;
CREATE INDEX IF NOT EXISTS idx_stg_enrichment_due
    ON stg.hh_vacancies(enrichment_status, next_retry_at);
