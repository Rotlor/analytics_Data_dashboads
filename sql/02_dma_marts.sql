-- Аналитические представления DMA.
-- Conservative duplicate heuristic; salary associations are not causal effects.

CREATE SCHEMA IF NOT EXISTS dma;


-- Витрина 1: dma.v_vacancies_wide (1 строка = 1 уникальная вакансия)
CREATE OR REPLACE VIEW dma.v_vacancies_wide AS
WITH dedup_offers AS (
    SELECT
        f.*,
        (ROW_NUMBER() OVER (
            PARTITION BY f.employer_id, f.role_title, f.grade,
                LOWER(REGEXP_REPLACE(f.title, '\s+', ' ', 'g')),
                f.salary_from_net_rub, f.salary_to_net_rub,
                f.salary_currency, f.salary_gross, f.schedule_id,
                (f.published_at AT TIME ZONE 'UTC')::DATE,
                CASE WHEN f.schedule_id = 'remote' AND f.employer_id > 0 AND f.has_salary
                     THEN 0 ELSE f.vacancy_id END
            ORDER BY f.published_at DESC, f.vacancy_id DESC
        ) = 1) AS is_unique_offer
    FROM dds.fct_vacancies f
),
cohort_medians AS (
    SELECT
        role_title,
        grade,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY salary_est_net_rub) AS cohort_median_net_rub,
        COUNT(*) AS cohort_sample_n
    FROM dedup_offers
    WHERE has_salary = TRUE AND is_unique_offer = TRUE
    GROUP BY role_title, grade
    HAVING COUNT(*) >= 5
)
SELECT
    f.vacancy_id,
    f.title,
    f.role_family,
    f.role_title,
    f.grade,
    CASE f.grade
        WHEN 'Junior' THEN 1
        WHEN 'Middle' THEN 2
        WHEN 'Senior' THEN 3
        WHEN 'Lead'   THEN 4
        ELSE 5
    END AS grade_order,
    e.employer_name,
    e.is_trusted,
    e.is_it_accredited,
    a.area_name,
    CASE
        WHEN a.area_name = 'Москва' THEN 'Москва'
        WHEN a.area_name = 'Санкт-Петербург' THEN 'Санкт-Петербург'
        WHEN a.area_id = 113 OR a.area_id IS NULL THEN 'Регион не уточнён'
        ELSE 'Регионы РФ'
    END AS macro_region,
    f.experience_id,
    CASE f.experience_id
        WHEN 'noExperience' THEN 'Нет опыта (0 лет)'
        WHEN 'between1And3' THEN 'От 1 до 3 лет'
        WHEN 'between3And6' THEN 'От 3 до 6 лет'
        WHEN 'moreThan6'    THEN 'Более 6 лет'
        ELSE f.experience_id
    END AS experience_label,
    f.schedule_id,
    CASE f.schedule_id
        WHEN 'remote'   THEN 'Удаленная работа'
        WHEN 'flexible' THEN 'Гибрид / Гибкий'
        WHEN 'fullDay' THEN 'Офис (Полный день)'
        ELSE 'Не определён'
    END AS work_format_label,
    f.has_salary,
    f.is_unique_offer,
    f.salary_currency,
    f.salary_gross,
    f.salary_from_net_rub,
    f.salary_to_net_rub,
    ROUND(f.salary_est_net_rub, 0) AS salary_est_net_rub,
    ROUND(f.salary_est_gross_rub, 0) AS salary_est_gross_rub,
    f.effective_ndfl_pct,
    ROUND(cm.cohort_median_net_rub::NUMERIC, 0) AS cohort_median_net_rub,
    ROUND(
        ((f.salary_est_net_rub - cm.cohort_median_net_rub) / NULLIF(cm.cohort_median_net_rub, 0) * 100)::NUMERIC,
        1
    ) AS vs_cohort_median_pct,
    COALESCE(CARDINALITY(f.skills_array), 0) AS skills_count,
    ARRAY_TO_STRING(f.skills_array, ', ') AS skills_list,
    (f.published_at AT TIME ZONE 'UTC')::DATE AS published_date,
    f.alternate_url,
    f.employer_id, f.area_id, f.salary_disclosed, f.salary_status,
    f.salary_est_method, f.grade_source, cm.cohort_sample_n,
    s.enrichment_status, s.enriched_at,
    (s.enrichment_status = 'success'
        OR COALESCE(s.payload->>'description_text', '') <> ''
        OR CASE WHEN jsonb_typeof(s.payload->'key_skills') = 'array'
                THEN jsonb_array_length(s.payload->'key_skills') > 0 ELSE FALSE END
    ) AS has_skill_evidence
FROM dedup_offers f
LEFT JOIN stg.hh_vacancies s ON f.vacancy_id = s.vacancy_id
LEFT JOIN dds.dim_employers e ON f.employer_id = e.employer_id
LEFT JOIN dds.dim_areas a ON f.area_id = a.area_id
LEFT JOIN cohort_medians cm ON f.role_title = cm.role_title AND f.grade = cm.grade;

-- Витрина 2: dma.v_role_grade_benchmarks (Квантили по ролям и грейдам)
CREATE OR REPLACE VIEW dma.v_role_grade_benchmarks AS
SELECT
    role_family,
    role_title,
    grade,
    grade_order,
    COUNT(*) AS vacancies_total,
    COUNT(*) FILTER (WHERE has_salary) AS vacancies_with_salary,
    COUNT(*) FILTER (WHERE has_salary AND is_unique_offer) AS unique_salaried_offers,
    ROUND((COUNT(*) FILTER (WHERE has_salary) * 100.0 / NULLIF(COUNT(*), 0))::NUMERIC, 1) AS salary_transparency_pct,
    ROUND((COUNT(*) FILTER (WHERE schedule_id = 'remote') * 100.0 / NULLIF(COUNT(*), 0))::NUMERIC, 1) AS remote_share_pct,
    ROUND((COUNT(*) FILTER (WHERE is_it_accredited) * 100.0 / NULLIF(COUNT(*), 0))::NUMERIC, 1) AS it_accredited_share_pct,
    ROUND((PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY salary_est_net_rub) FILTER (WHERE has_salary AND is_unique_offer))::NUMERIC, 0) AS salary_p25_net_rub,
    ROUND((PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY salary_est_net_rub) FILTER (WHERE has_salary AND is_unique_offer))::NUMERIC, 0) AS salary_median_net_rub,
    ROUND((PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY salary_est_net_rub) FILTER (WHERE has_salary AND is_unique_offer))::NUMERIC, 0) AS salary_p75_net_rub,
    ROUND((PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY salary_est_gross_rub) FILTER (WHERE has_salary AND is_unique_offer))::NUMERIC, 0) AS salary_median_gross_rub,
    COUNT(*) FILTER (WHERE salary_disclosed) AS vacancies_salary_disclosed,
    ROUND(COUNT(*) FILTER (WHERE salary_disclosed) * 100.0 / NULLIF(COUNT(*), 0), 1) AS salary_disclosure_pct,
    (COUNT(*) FILTER (WHERE has_salary AND is_unique_offer) < 5) AS is_low_sample
FROM dma.v_vacancies_wide
GROUP BY role_family, role_title, grade, grade_order;

-- Exploratory only: prevalence among observable texts; descriptive salary residuals.
-- No claim that observed skill causes salary differences or removes all confounding.
CREATE OR REPLACE VIEW dma.v_skill_premium_mart AS
WITH observed AS (
    SELECT * FROM dma.v_vacancies_wide WHERE has_skill_evidence
), market_base AS (
    SELECT COUNT(*) AS observed_n FROM observed
), skill_agg AS (
    SELECT b.skill_name, b.skill_category,
        COUNT(*) AS mentions_cnt,
        COUNT(*) FILTER (WHERE w.has_salary AND w.is_unique_offer AND w.cohort_sample_n >= 5) AS salaried_mentions_cnt,
        PERCENTILE_CONT(.5) WITHIN GROUP (ORDER BY w.salary_est_net_rub)
            FILTER (WHERE w.has_salary AND w.is_unique_offer AND w.cohort_sample_n >= 5) AS median_salary,
        PERCENTILE_CONT(.5) WITHIN GROUP (ORDER BY w.cohort_median_net_rub)
            FILTER (WHERE w.has_salary AND w.is_unique_offer AND w.cohort_sample_n >= 5) AS median_base,
        PERCENTILE_CONT(.5) WITHIN GROUP (ORDER BY w.salary_est_net_rub - w.cohort_median_net_rub)
            FILTER (WHERE w.has_salary AND w.is_unique_offer AND w.cohort_sample_n >= 5) AS median_residual,
        PERCENTILE_CONT(.5) WITHIN GROUP (ORDER BY 100.0 * (w.salary_est_net_rub / NULLIF(w.cohort_median_net_rub, 0) - 1))
            FILTER (WHERE w.has_salary AND w.is_unique_offer AND w.cohort_sample_n >= 5) AS median_residual_pct
    FROM dds.bridge_vacancy_skills b JOIN observed w USING (vacancy_id)
    GROUP BY b.skill_name, b.skill_category
)
SELECT s.skill_name, s.skill_category, s.mentions_cnt, s.salaried_mentions_cnt,
    ROUND(s.mentions_cnt * 100.0 / NULLIF(m.observed_n, 0), 1) AS penetration_pct,
    ROUND(s.median_base::numeric, 0) AS base_median_net_rub,
    ROUND(s.median_salary::numeric, 0) AS skill_median_net_rub,
    ROUND(s.median_residual::numeric, 0) AS skill_premium_rub,
    ROUND(s.median_residual_pct::numeric, 1) AS skill_premium_pct,
    m.observed_n AS observed_vacancies_n
FROM skill_agg s CROSS JOIN market_base m;
