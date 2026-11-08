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
