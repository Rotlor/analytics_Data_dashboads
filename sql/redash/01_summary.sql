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
