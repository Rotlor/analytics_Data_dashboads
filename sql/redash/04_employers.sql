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
