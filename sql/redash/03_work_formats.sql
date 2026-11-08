WITH scoped AS (
    SELECT * FROM dma.v_vacancies_wide
    WHERE ('{{role_family}}' = 'Все' OR role_family = '{{role_family}}')
      AND ('{{grade}}' = 'Все' OR grade = '{{grade}}')
)
SELECT role_title, work_format_label, COUNT(*) AS vacancies,
    ROUND(COUNT(*) * 100.0 / NULLIF(SUM(COUNT(*)) OVER (PARTITION BY role_title), 0), 1) AS share_pct
FROM scoped GROUP BY role_title, work_format_label ORDER BY role_title, vacancies DESC;
