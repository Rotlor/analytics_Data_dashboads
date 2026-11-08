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
