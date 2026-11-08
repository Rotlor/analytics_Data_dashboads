"""Executable invariants; market trends and monotonicity are diagnostics only."""
from src.config import classify_vacancy_title
from src.db import get_connection


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run_validation_suite():
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT
            (SELECT COUNT(*) FROM stg.hh_vacancies),
            (SELECT COUNT(*) FROM dds.fct_vacancies),
            (SELECT COUNT(*) FROM dma.v_vacancies_wide),
            (SELECT COUNT(DISTINCT vacancy_id) FROM dma.v_vacancies_wide)""")
        stg, facts, wide, unique = cur.fetchone()
        require(facts > 0, "DDS is empty")
        require(facts == wide == unique, "DDS/DMA cardinality mismatch")
        cur.execute("""SELECT COUNT(*) FROM dds.fct_vacancies f
            LEFT JOIN stg.hh_vacancies s USING (vacancy_id) WHERE s.vacancy_id IS NULL""")
        require(cur.fetchone()[0] == 0, "DDS rows have no staging lineage")
        cur.execute("""SELECT COUNT(*) FROM dds.fct_vacancies WHERE
            has_salary IS DISTINCT FROM (salary_est_net_rub IS NOT NULL)
            OR (has_salary AND (salary_est_net_rub <= 0 OR salary_est_gross_rub IS NULL
                OR effective_ndfl_pct IS NULL OR NOT (effective_ndfl_pct BETWEEN 12.99 AND 22.01)
                OR salary_est_gross_rub < salary_est_net_rub
                OR (salary_from_net_rub IS NOT NULL AND salary_to_net_rub IS NOT NULL
                    AND salary_from_net_rub > salary_to_net_rub)))""")
        require(cur.fetchone()[0] == 0, "Invalid salary bounds, NULL tax or missing gross")
        cur.execute("""SELECT COUNT(*) FROM dma.v_role_grade_benchmarks
            WHERE salary_p25_net_rub > salary_median_net_rub
               OR salary_median_net_rub > salary_p75_net_rub
               OR unique_salaried_offers > vacancies_with_salary
               OR vacancies_with_salary > vacancies_total""")
        require(cur.fetchone()[0] == 0, "Invalid quantiles or denominator")
        cur.execute("""WITH expected AS (
                SELECT vacancy_id, unnest(skills_array) AS skill_name FROM dds.fct_vacancies
            ), actual AS (SELECT vacancy_id, skill_name FROM dds.bridge_vacancy_skills)
            SELECT COUNT(*) FROM (
                (SELECT * FROM expected EXCEPT SELECT * FROM actual)
                UNION ALL (SELECT * FROM actual EXCEPT SELECT * FROM expected)
            ) difference""")
        require(cur.fetchone()[0] == 0, "Skill array/bridge mismatch")
        cur.execute("""SELECT COUNT(*) FROM dma.v_skill_premium_mart
            WHERE penetration_pct NOT BETWEEN 0 AND 100""")
        require(cur.fetchone()[0] == 0, "Skill penetration outside [0, 100]")
        cur.execute("""SELECT s.payload->>'name' FROM stg.hh_vacancies s
            LEFT JOIN dds.fct_vacancies f USING (vacancy_id) WHERE f.vacancy_id IS NULL""")
        excluded_titles = cur.fetchall()
        require(stg == facts + len(excluded_titles), "STG/DDS reconciliation mismatch")
        require(all(classify_vacancy_title(title or "") is None for (title,) in excluded_titles),
                "Unexpected staging exclusions; inspect classifier/quarantine")
        print(f"[PASS] STG={stg}; excluded={len(excluded_titles)}; DDS=DMA={facts}; salary, quantile, skill checks passed")
        cur.execute("""SELECT grade, COUNT(*) AS vacancies,
            COUNT(*) FILTER (WHERE has_salary AND is_unique_offer) AS salary_n,
            ROUND((PERCENTILE_CONT(.5) WITHIN GROUP (ORDER BY salary_est_net_rub)
                FILTER (WHERE has_salary AND is_unique_offer))::numeric) AS median
            FROM dma.v_vacancies_wide GROUP BY grade, grade_order ORDER BY grade_order""")
        for grade, total, sample, median in cur.fetchall():
            print(f"[INFO] {grade}: vacancies={total}; salary n={sample}; median={median}; low_sample={sample < 5}")
        cur.execute("""SELECT vacancy_id, salary_est_method, salary_est_net_rub, alternate_url
            FROM dma.v_vacancies_wide WHERE has_salary
            ORDER BY salary_est_net_rub DESC LIMIT 8""")
        for row in cur.fetchall():
            print("[MANUAL CHECK]", row)


if __name__ == "__main__":
    run_validation_suite()
