"""Integration tests create a dedicated database; never target a user's DWH.

Opt in: HH_TEST_POSTGRES=1. Needs permission to CREATE DATABASE.
Each run retains its unique hh_test_* database until the test container is removed.
"""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import unittest
import uuid
from unittest.mock import Mock, patch

from src.config import PROJECT_ROOT
from src.db import bootstrap_database, get_connection, upsert_vacancies_batch, upsert_raw_dictionary
from src.transform import DWHTransformer
from src.validate import run_validation_suite
from src.enrich import run_enrichment


@unittest.skipUnless(os.getenv("HH_TEST_POSTGRES") == "1", "Set HH_TEST_POSTGRES=1 for isolated PostgreSQL tests")
class PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_db = os.environ.get("DWH_DB")
        os.environ["DWH_DB"] = "hh_test_" + uuid.uuid4().hex[:12]
        bootstrap_database()
        cls.rows = []
        for ident in range(1, 12):
            payload = {
                "name": "Senior Data Engineer", "id": ident,
                "employer": {"id": 10 if ident <= 6 else ident + 10, "name": "Fixture company"},
                "area": {"id": ident, "name": "Fixture region"},
                "salary": {"from": 100000, "to": 200000, "currency":"RUR", "gross":False},
                "experience": {"id":"moreThan6"}, "schedule":{"id":"remote"},
                "key_skills":[{"name":"SQL"}], "description_text":"Python, Airflow",
                "published_at":"2026-11-01T12:00:00+00:00",
            }
            if ident == 3:
                payload["salary"].update({"from":100000,"to":300000})
            if ident == 4:
                payload["salary"].update({"from":150000,"to":250000})
            if ident in (5,6):
                payload["salary"] = None
            if ident == 11:
                payload["name"] = "Системный аналитик"
            cls.rows.append((ident, "Data Engineering", "Data Engineer", "fixture", json.dumps(payload)))
        upsert_vacancies_batch(cls.rows)
        with redirect_stdout(io.StringIO()):
            DWHTransformer().transform_and_load()

    @classmethod
    def tearDownClass(cls):
        if cls.original_db is None:
            os.environ.pop("DWH_DB",None)
        else:
            os.environ["DWH_DB"] = cls.original_db

    def fetch(self, sql, params=None):
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql,params)
            return cur.fetchall()

    def test_bootstrap_preserves_rows_and_views(self):
        bootstrap_database()
        bootstrap_database()
        self.assertEqual(self.fetch("SELECT count(*) FROM stg.hh_vacancies")[0][0], 11)
        self.assertEqual(self.fetch("SELECT count(*) FROM dma.v_vacancies_wide")[0][0], 10)

    def test_duplicate_heuristic_preserves_distinct_bounds_and_no_salary(self):
        unique = dict(self.fetch("SELECT vacancy_id, is_unique_offer FROM dma.v_vacancies_wide"))
        self.assertEqual(int(unique[1]) + int(unique[2]), 1)
        self.assertTrue(unique[3] and unique[4])
        self.assertTrue(unique[5] and unique[6])

    def test_all_redash_queries_compile_and_empty_filter_is_safe(self):
        with get_connection() as conn, conn.cursor() as cur:
            for path in sorted((PROJECT_ROOT/"sql/redash").glob("*.sql")):
                for family, grade in [("Все","Все"),("BI & Reporting","Lead")]:
                    cur.execute(path.read_text(encoding="utf-8").replace("{{role_family}}",family).replace("{{grade}}",grade))
                    cur.fetchall()

    def test_marts_and_validation_invariants(self):
        with redirect_stdout(io.StringIO()):
            run_validation_suite()

    def test_search_refresh_preserves_enrichment(self):
        payload = json.loads(self.rows[0][-1])
        payload["key_skills"] = []
        payload.pop("description_text")
        upsert_vacancies_batch([(1,"Data Engineering","Data Engineer","fixture",json.dumps(payload))])
        data = self.fetch("SELECT payload FROM stg.hh_vacancies WHERE vacancy_id=1")[0][0]
        self.assertEqual(data["description_text"], "Python, Airflow")
        self.assertEqual(data["key_skills"], [{"name":"SQL"}])

    def test_dictionary_refresh_updates_rates(self):
        upsert_raw_dictionary("test_dictionary", {"currency": [{"code":"USD","rate":.01}]})
        upsert_raw_dictionary("test_dictionary", {"currency": [{"code":"USD","rate":.02}]})
        data = self.fetch("SELECT payload FROM stg.hh_dictionaries WHERE dict_key='test_dictionary'")[0][0]
        self.assertEqual(data["currency"][0]["rate"], .02)

    def test_enrichment_queue_persists_and_rate_limit_stops(self):
        records = []
        for ident in (901, 902, 903):
            payload = json.loads(self.rows[0][-1])
            payload["id"] = ident
            records.append((ident,"Data Engineering","Data Engineer","api-fixture",json.dumps(payload)))
        upsert_vacancies_batch(records)
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE stg.hh_vacancies SET enrichment_status='failed' WHERE vacancy_id < 900")
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        session.get.side_effect = [
            Mock(status_code=200, headers={}, json=Mock(return_value={"id":"901","key_skills":[],"description":"<p>SQL</p>"})),
            Mock(status_code=429, headers={"Retry-After":"900"}),
        ]
        try:
            with patch("src.enrich.build_api_session", return_value=session), patch("src.enrich.time.sleep"):
                with redirect_stdout(io.StringIO()):
                    counts = run_enrichment(limit=3)
            self.assertEqual(counts, {"success":1,"retry":1})
            self.assertEqual(session.get.call_count,2)
            states = self.fetch("SELECT enrichment_status,enrichment_attempts FROM stg.hh_vacancies WHERE vacancy_id>=900 ORDER BY vacancy_id")
            self.assertEqual(states, [("success",1),("retry",1),("pending",0)])
            payload = self.fetch("SELECT payload FROM stg.hh_vacancies WHERE vacancy_id=901")[0][0]
            self.assertEqual(payload["key_skills"], [])
            self.assertEqual(payload["description_text"], "SQL")
            self.assertTrue(self.fetch("SELECT next_retry_at >= NOW() + INTERVAL '899 seconds' FROM stg.hh_vacancies WHERE vacancy_id=902")[0][0])
        finally:
            with get_connection() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM stg.hh_vacancies WHERE vacancy_id IN (901,902,903)")
                cur.execute("UPDATE stg.hh_vacancies SET enrichment_status='pending' WHERE vacancy_id < 900")

    def test_upsert_updates_mutable_attributes(self):
        # Dedicated fixture row; restore it after the assertion.
        payload = json.loads(self.rows[9][-1])
        payload["area"] = {"id":99,"name":"Changed region"}
        payload["schedule"] = {"id":"fullDay"}
        payload["salary"]["gross"] = True
        upsert_vacancies_batch([(10,"Data Engineering","Data Engineer","fixture",json.dumps(payload))])
        try:
            with redirect_stdout(io.StringIO()):
                DWHTransformer().transform_and_load()
            self.assertEqual(self.fetch("SELECT area_id,schedule_id,salary_gross FROM dds.fct_vacancies WHERE vacancy_id=10")[0], (99,"fullDay",True))
        finally:
            upsert_vacancies_batch([self.rows[9]])
            with redirect_stdout(io.StringIO()):
                DWHTransformer().transform_and_load()

    def test_transaction_rolls_back_and_closes(self):
        held = None
        try:
            with get_connection() as conn:
                held = conn
                with conn.cursor() as cur:
                    cur.execute("UPDATE stg.hh_vacancies SET search_query='rollback-test' WHERE vacancy_id=1")
                raise ValueError("forced failure")
        except ValueError:
            pass
        self.assertTrue(held.closed)
        self.assertEqual(self.fetch("SELECT search_query FROM stg.hh_vacancies WHERE vacancy_id=1")[0][0], "fixture")


if __name__ == "__main__":
    unittest.main()
