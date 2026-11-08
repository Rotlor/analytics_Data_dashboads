import json
from contextlib import contextmanager, closing
from datetime import datetime
from typing import Any

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from psycopg2.extras import execute_values

from src.config import DBConfig, PROJECT_ROOT


def _connect(dbname: str | None = None):
    cfg = DBConfig()
    return psycopg2.connect(
        host=cfg.host,
        port=cfg.port,
        user=cfg.user,
        password=cfg.password,
        dbname=dbname or cfg.dbname,
        connect_timeout=10,
        application_name="hh-dwh-mvp",
        options="-c timezone=UTC -c statement_timeout=120000",
    )


@contextmanager
def get_connection(dbname: str | None = None):
    """Commit on success, rollback on failure, always close the socket."""
    conn = _connect(dbname)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def bootstrap_database(sql_ddl_path: str = "sql/01_init_dwh_ddl.sql") -> None:
    cfg = DBConfig()
    with closing(_connect(dbname="postgres")) as admin_conn:
        admin_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with admin_conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s;", (cfg.dbname,))
            if not cur.fetchone():
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cfg.dbname)))

    ddl_sql = (PROJECT_ROOT / sql_ddl_path).read_text(encoding="utf-8")
    with get_connection() as dwh_conn:
        with dwh_conn.cursor() as cur:
            cur.execute(ddl_sql)
        dwh_conn.commit()
    print(f"[DB] Схемы stg, dds, dma успешно инициализированы в {cfg.dbname}.")


def upsert_raw_dictionary(dict_key: str, payload: dict[str, Any]) -> None:
    query = """
        INSERT INTO stg.hh_dictionaries (dict_key, payload, fetched_at)
        VALUES (%s, %s::jsonb, NOW())
        ON CONFLICT (dict_key) DO UPDATE SET
            payload = EXCLUDED.payload,
            fetched_at = NOW();
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (dict_key, json.dumps(payload, ensure_ascii=False)))
        conn.commit()


def upsert_vacancies_batch(records: list[tuple[int, str, str, str, str]]) -> int:
    """
    Пакетный идемпотентный UPSERT в stg.hh_vacancies.
    Запись: (vacancy_id, role_family, role_title, search_query, payload_json).
    """
    if not records:
        return 0
    records = list({record[0]: record for record in records}.values())

    query = """
        INSERT INTO stg.hh_vacancies (vacancy_id, role_family, role_title, search_query, payload)
        VALUES %s
        ON CONFLICT (vacancy_id) DO UPDATE SET
            role_family = EXCLUDED.role_family,
            role_title = EXCLUDED.role_title,
            payload = EXCLUDED.payload || (
                SELECT COALESCE(jsonb_object_agg(key, value), '{}'::jsonb)
                FROM jsonb_each(stg.hh_vacancies.payload)
                WHERE key IN ('key_skills', 'description_text', 'description')
            ),
            fetched_at = NOW();
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            execute_values(
                cur,
                query,
                records,
                template="(%s, %s, %s, %s, %s::jsonb)",
                page_size=500,
            )
        conn.commit()
    return len(records)


def update_vacancy_payload(vacancy_id: int, payload: dict[str, Any]) -> None:
    query = """
        UPDATE stg.hh_vacancies
        SET payload = %s::jsonb, fetched_at = NOW()
        WHERE vacancy_id = %s;
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (json.dumps(payload, ensure_ascii=False), vacancy_id))
        conn.commit()


def log_dq_audit(
    stage_name: str,
    started_at: datetime,
    rows_processed: int,
    salary_coverage_pct: float | None,
    skills_coverage_pct: float | None,
    status: str,
    details: str,
) -> None:
    query = """
        INSERT INTO dds.dq_audit_log (
            stage_name, started_at, finished_at, rows_processed,
            salary_coverage_pct, skills_coverage_pct, status, details
        )
        VALUES (%s, %s, NOW(), %s, %s, %s, %s, %s);
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                query,
                (
                    stage_name,
                    started_at,
                    rows_processed,
                    salary_coverage_pct,
                    skills_coverage_pct,
                    status,
                    details,
                ),
            )
        conn.commit()
