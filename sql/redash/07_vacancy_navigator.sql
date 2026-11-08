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
