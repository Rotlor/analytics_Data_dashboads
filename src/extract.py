import html
import json
import math
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.config import (
    HH_API_BASE_URL,
    HH_WEB_SEARCH_URL,
    REQUEST_SLEEP_SEC,
    ROLE_SPECS,
    TARGET_AREAS,
    RoleSpec,
    classify_vacancy_title,
)
from src.db import (
    bootstrap_database,
    get_connection,
    log_dq_audit,
    upsert_raw_dictionary,
    upsert_vacancies_batch,
)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "upgrade-insecure-requests": "1",
}


class HHExtractor:
    def __init__(self) -> None:
        self.session = self._build_session()
        self.seen_ids: set[int] = set()          # Глобальный фильтр дублей между всеми запросами
        self.duplicates_skipped: int = 0
        self.irrelevant_skipped: int = 0

    @staticmethod
    def _build_session() -> requests.Session:
        session = requests.Session()
        session.headers.update(BROWSER_HEADERS)
        retry_strategy = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    @staticmethod
    def fetch_dictionaries() -> None:
        print("\n[1/3] Загружаем справочники и курсы валют (/dictionaries)...")
        headers = {
            "HH-User-Agent": os.getenv("HH_USER_AGENT", ""),
            "User-Agent": os.getenv("HH_USER_AGENT", ""),
            "Accept": "application/json",
        }
        if not headers["HH-User-Agent"]:
            raise RuntimeError("Set HH_USER_AGENT to your application name and contact")
        if os.getenv("HH_ACCESS_TOKEN"):
            headers["Authorization"] = f"Bearer {os.environ['HH_ACCESS_TOKEN']}"
        resp = requests.get(f"{HH_API_BASE_URL}/dictionaries", headers=headers, timeout=15)
        resp.raise_for_status()
        upsert_raw_dictionary("hh_dictionaries", resp.json())
        print("  -> Справочники успешно сохранены в stg.hh_dictionaries.")

    @staticmethod
    def _extract_initial_state(html_text: str) -> dict[str, Any]:
        match = re.search(
            r'<template[^>]+id=["\']HH-Lux-InitialState["\'][^>]*>(.*?)</template>',
            html_text,
            re.DOTALL,
        )
        if not match:
            return {}
        try:
            return json.loads(html.unescape(match.group(1).strip()))
        except (ValueError, TypeError):
            return {}

    @staticmethod
    def _normalize_search_item(item: dict[str, Any]) -> dict[str, Any]:
        vac_id = item.get("vacancyId") or item.get("id")
        comp = item.get("compensation") or item.get("salary") or {}

        has_salary = (
            bool(comp)
            and not comp.get("noCompensation", False)
            and (comp.get("from") is not None or comp.get("to") is not None)
        )
        salary_obj = None
        if has_salary:
            salary_obj = {
                "from": comp.get("from"),
                "to": comp.get("to"),
                "currency": comp.get("currencyCode") or comp.get("currency"),
                "gross": comp.get("gross"),
                "period": comp.get("period") or "MONTH",
            }

        company = item.get("company") or item.get("employer") or {}
        area = item.get("area") or {}

        raw_exp = item.get("workExperience") or item.get("experience")
        exp_id = (
            raw_exp
            if isinstance(raw_exp, str)
            else (raw_exp.get("id") if isinstance(raw_exp, dict) else "unknown")
        )

        work_formats = str(item.get("workFormats") or "").upper()
        tags_text = str(item.get("tags") or "").lower()
        if "REMOTE" in work_formats or "удалённо" in tags_text or "удаленно" in tags_text:
            sched_id = "remote"
        elif "HYBRID" in work_formats or "гибрид" in tags_text:
            sched_id = "flexible"
        else:
            sched_id = ("fullDay" if "ON_SITE" in work_formats or "OFFICE" in work_formats
                        else (item.get("schedule") or {}).get("id", "unknown"))

        pub_raw = item.get("publicationTime") or item.get("published_at")
        pub_str = (
            pub_raw.get("$")
            if isinstance(pub_raw, dict)
            else (pub_raw if isinstance(pub_raw, str) else None)
        )
        if not pub_str:
            raise ValueError(f"Vacancy {vac_id}: missing publication timestamp")

        return {
            "id": int(vac_id),
            "name": item.get("name", "Unknown"),
            "salary": salary_obj,
            "employer": {
                "id": int(company.get("id") or company.get("companyId") or 0),
                "name": company.get("name") or "Не указан",
                "trusted": bool(company.get("@trusted") or company.get("trusted", False)),
                "accredited_it_employer": bool(
                    company.get("accreditedITEmployer") or company.get("accredited_it_employer", False)
                ),
            },
            "area": {
                "id": int(area.get("@id") or area.get("id") or 113),
                "name": area.get("name") or "Россия",
            },
            "experience": {"id": exp_id or "unknown"},
            "schedule": {"id": sched_id},
            "employment": item.get("employment") or {"id": "unknown"},
            "key_skills": [],
            "published_at": pub_str,
            "alternate_url": f"https://hh.ru/vacancy/{vac_id}",
        }

    def _collect_query(self, spec: RoleSpec, query: str) -> int:
        params: dict[str, Any] = {
            "text": query,
            "search_field": "name",
            "area": list(TARGET_AREAS),
            "items_on_page": 50,
            "page": 0,
        }
        resp = self.session.get(HH_WEB_SEARCH_URL, params=params, timeout=20)
        resp.raise_for_status()

        state = self._extract_initial_state(resp.text)
        if "vacancySearchResult" not in state:
            raise RuntimeError("HH search SSR schema changed or access challenge received")
        search_res = state.get("vacancySearchResult", {})
        total_results = int(search_res.get("totalResults") or 0)
        if total_results == 0:
            return 0

        if total_results > 1000:
            print(f"[WARN] Search '{query}' truncated: found={total_results}, cap=1000")
        total_pages = min(math.ceil(total_results / 50), 20)
        saved_count = 0

        for page in range(total_pages):
            if page == 0:
                raw_items = search_res.get("vacancies", [])
            else:
                p_resp = self.session.get(HH_WEB_SEARCH_URL, params={**params, "page": page}, timeout=20)
                p_resp.raise_for_status()
                p_state = self._extract_initial_state(p_resp.text)
                if "vacancySearchResult" not in p_state:
                    raise RuntimeError("HH search SSR schema changed on a later page")
                raw_items = p_state.get("vacancySearchResult", {}).get("vacancies", [])

            if not raw_items:
                raise RuntimeError("Search returned an unexpected empty page")

            batch: list[tuple[int, str, str, str, str]] = []
            for item in raw_items:
                raw_id = item.get("vacancyId") or item.get("id")
                if not raw_id:
                    continue
                vac_id = int(raw_id)

                if vac_id in self.seen_ids:
                    self.duplicates_skipped += 1
                    continue

                title = item.get("name", "")
                classified = classify_vacancy_title(title)
                if not classified:
                    self.irrelevant_skipped += 1
                    continue

                role_family, role_title = classified

                norm = self._normalize_search_item(item)
                batch.append(
                    (
                        vac_id,
                        role_family,
                        role_title,
                        query,
                        json.dumps(norm, ensure_ascii=False),
                    )
                )

            saved_count += upsert_vacancies_batch(batch)
            self.seen_ids.update(record[0] for record in batch)
            print(
                f"  -> [{spec.role_title}] Запрос: '{query}' | "
                f"Стр. {page + 1}/{total_pages} | Новых уникальных: {len(batch)}"
            )
            time.sleep(REQUEST_SLEEP_SEC)

        return saved_count

    def run(self) -> None:
        started_at = datetime.now(timezone.utc)
        self.seen_ids.clear()
        self.fetch_dictionaries()

        print("\n[2/3] Постраничный сбор по 9 чистым специализациям (с дедупликацией на лету)...")
        for spec in ROLE_SPECS:
            print(f"\n=== Специализация: {spec.role_title} ({spec.role_family}) ===")
            for q in spec.queries:
                self._collect_query(spec, q)

        print(
            f"\n[DEDUP SUMMARY] Всего уникальных отобрано: {len(self.seen_ids)} | "
            f"Отброшено дублей между запросами: {self.duplicates_skipped} | "
            f"Отфильтровано нерелевантных заголовков: {self.irrelevant_skipped}"
        )

        # Optional API enrichment is a separate, resumable command: python -m src.enrich

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        COUNT(*) AS total_vacancies,
                        ROUND(COUNT(*) FILTER (WHERE payload->'salary' IS NOT NULL AND payload->>'salary' <> 'null') * 100.0 / NULLIF(COUNT(*), 0), 2) AS sal_cov,
                        ROUND(COUNT(*) FILTER (WHERE jsonb_array_length(payload->'key_skills') > 0 OR COALESCE(payload->>'description_text', '') <> '') * 100.0 / NULLIF(COUNT(*), 0), 2) AS skill_cov
                    FROM stg.hh_vacancies;
                    """
                )
                total_vacs, sal_cov, skill_cov = cur.fetchone()

                cur.execute(
                    """
                    SELECT role_family, role_title, COUNT(*) AS cnt
                    FROM stg.hh_vacancies
                    GROUP BY role_family, role_title
                    ORDER BY role_family, cnt DESC;
                    """
                )
                breakdown = cur.fetchall()

        log_dq_audit(
            stage_name="EXTRACT_STG",
            started_at=started_at,
            rows_processed=total_vacs,
            salary_coverage_pct=sal_cov,
            skills_coverage_pct=skill_cov,
            status="PASS" if total_vacs > 300 else "WARN",
            details=f"Unique: {total_vacs}, Skipped Dups: {self.duplicates_skipped},Filtered Out: {self.irrelevant_skipped}",
        )

        print("\n" + "=" * 72)
        print("ИТОГИ ЭТАПА EXTRACT -> STG.HH_VACANCIES (9 ЧИСТЫХ РОЛЕЙ БЕЗ ДУБЛЕЙ):")
        print(f"  Всего уникальных вакансий в БД:      {total_vacs}")
        print(f"  Отсечено межкатегорийных дубликатов: {self.duplicates_skipped}")
        print(f"  Доля вакансий с зарплатой:           {sal_cov}%")
        print(f"  Доля вакансий с описанием/стеком:    {skill_cov}%")
        print("-" * 72)
        print(f"  {'Направление (role_family)':<22} | {'Специализация (role_title)':<22} | {'Вакансий'}")
        print("-" * 72)
        for family, role, cnt in breakdown:
            print(f"  {family:<22} | {role:<22} | {cnt}")
        print("=" * 72)


if __name__ == "__main__":
    bootstrap_database()
    extractor = HHExtractor()
    try:
        extractor.run()
    finally:
        extractor.session.close()
