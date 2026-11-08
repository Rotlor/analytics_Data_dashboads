WITH scoped AS (
    SELECT * FROM dma.v_vacancies_wide
    WHERE ('{{role_family}}' = 'Все' OR role_family = '{{role_family}}')
      AND ('{{grade}}' = 'Все' OR grade = '{{grade}}')
)
SELECT role_family, role_title, grade, grade_order, COUNT(*) AS vacancies
FROM scoped GROUP BY role_family, role_title, grade, grade_order
ORDER BY SUM(COUNT(*)) OVER (PARTITION BY role_title) DESC, grade_order;
