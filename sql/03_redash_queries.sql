-- Two MVP dashboards. Each block is a separate Redash query.
-- Parameters role_family and grade MUST be restricted dropdowns, default Все.

-- name: 01_summary
WITH scoped AS (
    SELECT * FROM dma.v_vacancies_wide
    WHERE ('{{role_family}}' = 'Все' OR role_family = '{{role_family}}')
      AND ('{{grade}}' = 'Все' OR grade = '{{grade}}')
)
SELECT COUNT(*) AS vacancies_collected,
    COUNT(DISTINCT employer_id) FILTER (WHERE employer_id > 0) AS employers,
    COUNT(*) FILTER (WHERE salary_disclosed) AS salary_disclosed_n,
    COUNT(*) FILTER (WHERE has_salary AND is_unique_offer) AS salary_sample_n,
    ROUND(COUNT(*) FILTER (WHERE salary_disclosed) * 100.0 / NULLIF(COUNT(*), 0), 1) AS salary_disclosure_pct,
    ROUND(COUNT(*) FILTER (WHERE schedule_id='remote') * 100.0 / NULLIF(COUNT(*), 0), 1) AS remote_share_pct,
    ROUND(COUNT(*) FILTER (WHERE schedule_id='unknown') * 100.0 / NULLIF(COUNT(*), 0), 1) AS unknown_format_pct,
    CASE WHEN COUNT(*) FILTER (WHERE has_salary AND is_unique_offer) >= 5 THEN
        ROUND((PERCENTILE_CONT(.5) WITHIN GROUP (ORDER BY salary_est_net_rub)
            FILTER (WHERE has_salary AND is_unique_offer))::numeric) END AS median_est_net_rub,
    MIN(published_date) AS first_publication_date, MAX(published_date) AS last_publication_date
FROM scoped;

-- name: 02_demand_roles
WITH scoped AS (
    SELECT * FROM dma.v_vacancies_wide
    WHERE ('{{role_family}}' = 'Все' OR role_family = '{{role_family}}')
      AND ('{{grade}}' = 'Все' OR grade = '{{grade}}')
)
SELECT role_family, role_title, grade, grade_order, COUNT(*) AS vacancies
FROM scoped GROUP BY role_family, role_title, grade, grade_order
ORDER BY SUM(COUNT(*)) OVER (PARTITION BY role_title) DESC, grade_order;

-- name: 03_work_formats
WITH scoped AS (
    SELECT * FROM dma.v_vacancies_wide
    WHERE ('{{role_family}}' = 'Все' OR role_family = '{{role_family}}')
      AND ('{{grade}}' = 'Все' OR grade = '{{grade}}')
)
SELECT role_title, work_format_label, COUNT(*) AS vacancies,
    ROUND(COUNT(*) * 100.0 / NULLIF(SUM(COUNT(*)) OVER (PARTITION BY role_title), 0), 1) AS share_pct
FROM scoped GROUP BY role_title, work_format_label ORDER BY role_title, vacancies DESC;

-- name: 04_employers
WITH scoped AS (
    SELECT * FROM dma.v_vacancies_wide
    WHERE ('{{role_family}}' = 'Все' OR role_family = '{{role_family}}')
      AND ('{{grade}}' = 'Все' OR grade = '{{grade}}')
)
SELECT employer_id, employer_name, COUNT(*) AS vacancies,
    COUNT(DISTINCT role_title) AS roles,
    COUNT(*) FILTER (WHERE has_salary AND is_unique_offer) AS salary_sample_n
FROM scoped WHERE employer_id > 0
GROUP BY employer_id, employer_name ORDER BY vacancies DESC, employer_id LIMIT 15;

-- name: 05_salary_cohorts
SELECT role_family, role_title, grade, grade_order, vacancies_total,
    vacancies_salary_disclosed, unique_salaried_offers AS salary_sample_n, salary_disclosure_pct,
    CASE WHEN NOT is_low_sample THEN salary_p25_net_rub END AS p25_est_net_rub,
    CASE WHEN NOT is_low_sample THEN salary_median_net_rub END AS p50_est_net_rub,
    CASE WHEN NOT is_low_sample THEN salary_p75_net_rub END AS p75_est_net_rub,
    CASE WHEN is_low_sample THEN 'Мало данных: n < 5' ELSE 'Описательная оценка' END AS sample_note
FROM dma.v_role_grade_benchmarks
WHERE ('{{role_family}}' = 'Все' OR role_family = '{{role_family}}')
  AND ('{{grade}}' = 'Все' OR grade = '{{grade}}')
ORDER BY grade_order, p50_est_net_rub DESC NULLS LAST, role_title;

-- name: 06_salary_by_grade
WITH scoped AS (
    SELECT * FROM dma.v_vacancies_wide
    WHERE ('{{role_family}}' = 'Все' OR role_family = '{{role_family}}')
      AND ('{{grade}}' = 'Все' OR grade = '{{grade}}')
)
SELECT role_family, grade, grade_order,
    COUNT(*) FILTER (WHERE has_salary AND is_unique_offer) AS salary_sample_n,
    CASE WHEN COUNT(*) FILTER (WHERE has_salary AND is_unique_offer) >= 5 THEN
        ROUND((PERCENTILE_CONT(.5) WITHIN GROUP (ORDER BY salary_est_net_rub)
            FILTER (WHERE has_salary AND is_unique_offer))::numeric) END AS median_est_net_rub
FROM scoped GROUP BY role_family, grade, grade_order ORDER BY grade_order, role_family;

-- name: 07_vacancy_navigator
WITH scoped AS (
    SELECT * FROM dma.v_vacancies_wide
    WHERE ('{{role_family}}' = 'Все' OR role_family = '{{role_family}}')
      AND ('{{grade}}' = 'Все' OR grade = '{{grade}}')
)
SELECT role_title, grade, grade_source, title, employer_name, area_name,
    work_format_label, salary_est_method, salary_est_net_rub, cohort_sample_n,
    cohort_median_net_rub, vs_cohort_median_pct, alternate_url
FROM scoped WHERE has_salary
ORDER BY salary_est_net_rub DESC, vacancy_id LIMIT 200;

-- name: 08_data_quality
WITH scoped AS (
    SELECT * FROM dma.v_vacancies_wide
    WHERE ('{{role_family}}' = 'Все' OR role_family = '{{role_family}}')
      AND ('{{grade}}' = 'Все' OR grade = '{{grade}}')
)
SELECT COUNT(*) AS vacancies,
    COUNT(*) FILTER (WHERE has_skill_evidence) AS observable_skill_texts_n,
    COUNT(*) FILTER (WHERE enrichment_status='success') AS api_details_success_n,
    COUNT(*) FILTER (WHERE salary_disclosed AND NOT has_salary) AS excluded_salaries_n,
    COUNT(*) FILTER (WHERE grade_source='experience') AS inferred_grade_n,
    COUNT(*) FILTER (WHERE grade='Unknown') AS unknown_grade_n,
    COUNT(*) FILTER (WHERE schedule_id='unknown') AS unknown_work_format_n,
    (SELECT MAX(fetched_at) FROM stg.hh_vacancies) AS last_search_fetch_at
FROM scoped;
