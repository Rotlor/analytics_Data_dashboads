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
