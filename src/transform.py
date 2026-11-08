from datetime import datetime, timezone
from typing import Any

import pandas as pd
from psycopg2.extras import execute_values

from src.config import classify_vacancy_title, PROJECT_ROOT
from src.db import get_connection, log_dq_audit

from src.skills import SKILL_CATALOG
from src.rules import normalize_salaries, classify_grade


class DWHTransformer:
    @staticmethod
    def _load_currency_rates() -> dict[str, float]:
        rates = {"RUR": 1.0, "RUB": 1.0}
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT payload FROM stg.hh_dictionaries WHERE dict_key = 'hh_dictionaries';")
                row = cur.fetchone()
                if row and isinstance(row[0], dict):
                    for curr in row[0].get("currency", []):
                        code = curr.get("code")
                        rate = curr.get("rate")
                        if code and rate:
                            rates[str(code).upper()] = float(rate)
        return rates

    @staticmethod
    def _load_staging_dataframe() -> pd.DataFrame:
        query = """
            SELECT vacancy_id, role_family, role_title, payload
            FROM stg.hh_vacancies;
        """
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                rows = cur.fetchall()

        records: list[dict[str, Any]] = []
        for vac_id, role_family, role_title, p in rows:
            title = str(p.get("name") or "Unknown")[:300]
            classified = classify_vacancy_title(title)
            if classified is None:
                continue
            role_family, role_title = classified

            sal = p.get("salary") or {}
            emp = p.get("employer") or {}
            area = p.get("area") or {}
            exp = p.get("experience") or {}
            sched = p.get("schedule") or {}
            empl = p.get("employment") or {}
            key_skills = p.get("key_skills") or []
            desc_text = p.get("description_text") or ""

            records.append(
                {
                    "vacancy_id": int(vac_id),
                    "title": title,
                    "role_family": role_family,
                    "role_title": role_title,
                    "employer_id": int(emp.get("id") or 0),
                    "employer_name": str(emp.get("name") or "Не указан")[:255],
                    "is_trusted": bool(emp.get("trusted", False)),
                    "is_it_accredited": bool(emp.get("accredited_it_employer", False)),
                    "area_id": int(area.get("id") or 113),
                    "area_name": str(area.get("name") or "Россия")[:150],
                    "experience_id": str(exp.get("id") or "unknown"),
                    "schedule_id": str(sched.get("id") or "unknown"),
                    "employment_id": str(empl.get("id") or "unknown"),
                    "salary_from": pd.to_numeric(sal.get("from"), errors="coerce"),
                    "salary_to": pd.to_numeric(sal.get("to"), errors="coerce"),
                    "salary_currency": (
                        str(sal.get("currency")).upper() if sal.get("currency") else None
                    ),
                    "salary_gross": sal.get("gross") if sal else None,
                    "salary_period": sal.get("period", "MONTH"),
                    "raw_key_skills": key_skills if isinstance(key_skills, list) else [],
                    "description_text": str(desc_text),
                    "published_at": p.get("published_at"),
                    "alternate_url": str(p.get("alternate_url") or f"https://hh.ru/vacancy/{vac_id}"),
                }
            )
        return pd.DataFrame(records)

    def transform_and_load(self) -> None:
        started_at = datetime.now(timezone.utc)
        print("\n[TRANSFORM 1/4] Читаем сырые данные из stg.hh_vacancies и курсы валют...")
        rates = self._load_currency_rates()
        df = self._load_staging_dataframe()

        if df.empty:
            raise RuntimeError("Таблица stg.hh_vacancies пуста!")

        print(f"  -> Загружено чистых целевых вакансий: {len(df)} шт.")

        print("[TRANSFORM 2/4] Векторный пересчет валют и прогрессивного НДФЛ (13-22%)...")
        df = normalize_salaries(df, rates)
        grades = [classify_grade(t, e) for t, e in zip(df["title"], df["experience_id"])]
        df["grade"] = [g[0] for g in grades]
        df["grade_source"] = [g[1] for g in grades]

        joined_key_skills = df["raw_key_skills"].apply(
            lambda lst: " ".join(str(x.get("name", "")) if isinstance(x, dict) else str(x) for x in lst) if isinstance(lst, list) else ""
        )
        full_corpus = (
            df["title"].fillna("")
            + " "
            + joined_key_skills
            + " "
            + df["description_text"].fillna("")
        ).str.lower()

        bridge_rows: list[tuple[int, str, str]] = []
        skills_per_vacancy: dict[int, list[str]] = {vid: [] for vid in df["vacancy_id"]}

        for skill_name, skill_cat, pattern in SKILL_CATALOG:
            matched_mask = full_corpus.str.contains(pattern, regex=True, na=False)
            matched_vids = df.loc[matched_mask, "vacancy_id"].tolist()
            for vid in matched_vids:
                bridge_rows.append((int(vid), skill_name, skill_cat))
                skills_per_vacancy[int(vid)].append(skill_name)

        df["skills_array"] = df["vacancy_id"].map(skills_per_vacancy)

        print("[TRANSFORM 4/4] Загрузка измерений, фактов и моста навыков в слой DDS...")
        dim_emp_df = (
            df[["employer_id", "employer_name", "is_trusted", "is_it_accredited"]]
            .drop_duplicates(subset=["employer_id"])
        )
        dim_area_df = df[["area_id", "area_name"]].drop_duplicates(subset=["area_id"])

        with get_connection() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO dds.dim_employers (employer_id, employer_name, is_trusted, is_it_accredited)
                    VALUES %s
                    ON CONFLICT (employer_id) DO UPDATE SET
                        employer_name = EXCLUDED.employer_name,
                        is_trusted = EXCLUDED.is_trusted,
                        is_it_accredited = EXCLUDED.is_it_accredited,
                        updated_at = NOW();
                    """,
                    list(dim_emp_df.itertuples(index=False, name=None)),
                )

                execute_values(
                    cur,
                    """
                    INSERT INTO dds.dim_areas (area_id, area_name)
                    VALUES %s
                    ON CONFLICT (area_id) DO UPDATE SET
                        area_name = EXCLUDED.area_name,
                        updated_at = NOW();
                    """,
                    list(dim_area_df.itertuples(index=False, name=None)),
                )

                # Удаление ограничено отклонёнными записями текущего STG.
                valid_ids = [int(x) for x in df["vacancy_id"].tolist()]
                cur.execute("""DELETE FROM dds.fct_vacancies f
                    USING stg.hh_vacancies s
                    WHERE f.vacancy_id = s.vacancy_id AND NOT (f.vacancy_id = ANY(%s));""", (valid_ids,))

                fct_records = []
                for row in df.itertuples(index=False):
                    fct_records.append(
                        (
                            int(row.vacancy_id),
                            row.title,
                            row.role_family,
                            row.role_title,
                            row.grade,
                            int(row.employer_id),
                            int(row.area_id),
                            row.experience_id,
                            row.schedule_id,
                            row.employment_id,
                            bool(row.has_salary),
                            row.salary_currency if pd.notna(row.salary_currency) else None,
                            bool(row.salary_gross) if pd.notna(row.salary_gross) else None,
                            float(row.salary_from_net_rub) if pd.notna(row.salary_from_net_rub) else None,
                            float(row.salary_to_net_rub) if pd.notna(row.salary_to_net_rub) else None,
                            float(row.salary_est_net_rub) if pd.notna(row.salary_est_net_rub) else None,
                            float(row.salary_est_gross_rub) if pd.notna(row.salary_est_gross_rub) else None,
                            float(row.effective_ndfl_pct) if pd.notna(row.effective_ndfl_pct) else None,
                            row.skills_array,
                            row.published_at,
                            row.alternate_url,
                            bool(row.salary_disclosed), row.salary_status,
                            row.salary_est_method, row.grade_source,
                        )
                    )

                execute_values(
                    cur,
                    """
                    INSERT INTO dds.fct_vacancies (
                        vacancy_id, title, role_family, role_title, grade,
                        employer_id, area_id, experience_id, schedule_id, employment_id,
                        has_salary, salary_currency, salary_gross,
                        salary_from_net_rub, salary_to_net_rub, salary_est_net_rub,
                        salary_est_gross_rub, effective_ndfl_pct,
                        skills_array, published_at, alternate_url,
                        salary_disclosed, salary_status, salary_est_method, grade_source
                    )
                    VALUES %s
                    ON CONFLICT (vacancy_id) DO UPDATE SET
                        title = EXCLUDED.title,
                        role_family = EXCLUDED.role_family,
                        role_title = EXCLUDED.role_title,
                        grade = EXCLUDED.grade,
                        employer_id = EXCLUDED.employer_id,
                        area_id = EXCLUDED.area_id,
                        experience_id = EXCLUDED.experience_id,
                        schedule_id = EXCLUDED.schedule_id,
                        employment_id = EXCLUDED.employment_id,
                        salary_currency = EXCLUDED.salary_currency,
                        salary_gross = EXCLUDED.salary_gross,
                        published_at = EXCLUDED.published_at,
                        alternate_url = EXCLUDED.alternate_url,
                        salary_disclosed = EXCLUDED.salary_disclosed,
                        salary_status = EXCLUDED.salary_status,
                        salary_est_method = EXCLUDED.salary_est_method,
                        grade_source = EXCLUDED.grade_source,
                        has_salary = EXCLUDED.has_salary,
                        salary_from_net_rub = EXCLUDED.salary_from_net_rub,
                        salary_to_net_rub = EXCLUDED.salary_to_net_rub,
                        salary_est_net_rub = EXCLUDED.salary_est_net_rub,
                        salary_est_gross_rub = EXCLUDED.salary_est_gross_rub,
                        effective_ndfl_pct = EXCLUDED.effective_ndfl_pct,
                        skills_array = EXCLUDED.skills_array,
                        loaded_at = NOW();
                    """,
                    fct_records,
                )

                cur.execute("DELETE FROM dds.bridge_vacancy_skills WHERE vacancy_id = ANY(%s);", (valid_ids,))
                if bridge_rows:
                    execute_values(
                        cur,
                        """
                        INSERT INTO dds.bridge_vacancy_skills (vacancy_id, skill_name, skill_category)
                        VALUES %s
                        ON CONFLICT (vacancy_id, skill_name) DO NOTHING;
                        """,
                        bridge_rows,
                    )
                cur.execute((PROJECT_ROOT / "sql/02_dma_marts.sql").read_text(encoding="utf-8"))
            conn.commit()

        sal_cov_pct = round(float(df["has_salary"].mean() * 100.0), 2)
        skill_cov_pct = round(float((df["skills_array"].str.len() > 0).mean() * 100.0), 2)

        log_dq_audit(
            stage_name="TRANSFORM_DDS",
            started_at=started_at,
            rows_processed=len(df),
            salary_coverage_pct=sal_cov_pct,
            skills_coverage_pct=skill_cov_pct,
            status="PASS",
            details=f"DDS Loaded: {len(df)} facts, {len(bridge_rows)} skill links (Progressive NDFL)",
        )

        print("\n" + "=" * 76)
        print("ИТОГИ ЭТАПА TRANSFORM -> DDS & DMA (С УЧЕТОМ ПРОГРЕССИВНОГО НДФЛ 13-22%):")
        print(f"  Всего вакансий в dds.fct_vacancies:           {len(df)}")
        print(f"  Вакансий с открытой зарплатой:                {int(df['has_salary'].sum())} ({sal_cov_pct}%)")
        print(f"  Покрытие распознанным стеком технологий:       {skill_cov_pct}% ({len(bridge_rows)} связей)")
        print("-" * 76)
        print(f"  {'Специализация':<22} | {'Всего вак.'} | {'С ЗП (все грейды)'} | {'Медиана Middle Net (₽)'}")
        print("-" * 76)
        for r_title, grp in df.groupby("role_title"):
            tot_c = len(grp)
            sal_c = int(grp["has_salary"].sum())
            mid_sals = grp.loc[(grp["grade"] == "Middle") & grp["has_salary"], "salary_est_net_rub"]
            med_str = f"{int(mid_sals.median()):>7,} ₽ (по {len(mid_sals)} вак.)".replace(",", " ") if not mid_sals.empty else "н/д"
            print(f"  {r_title:<22} | {tot_c:<10} | {sal_c:<17} | {med_str}")
        print("=" * 76)


if __name__ == "__main__":
    from src.db import bootstrap_database
    bootstrap_database()
    transformer = DWHTransformer()
    transformer.transform_and_load()
