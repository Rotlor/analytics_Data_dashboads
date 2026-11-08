"""Opt-in, resumable API enrichment; one worker per database for this MVP."""
import argparse
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
import json
import os
import random
import time

import requests

from src.config import HH_API_BASE_URL
from src.db import get_connection, log_dq_audit


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(value):
    parser = TextExtractor()
    parser.feed(value or "")
    return " ".join(unescape(" ".join(parser.parts)).split())


def retry_at(attempt, retry_after=None, now=None):
    now = now or datetime.now(timezone.utc)
    delay = min(3600, 60 * 2 ** min(attempt - 1, 6)) + random.uniform(0, 10)
    if retry_after:
        try:
            delay = max(delay, float(retry_after))
        except ValueError:
            try:
                delay = max(delay, (parsedate_to_datetime(retry_after) - now).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass
    return now + timedelta(seconds=delay)


def interpret_response(response, vacancy_id):
    code = response.status_code
    if code == 200:
        try:
            payload = response.json()
            if str(payload.get("id")) != str(vacancy_id):
                raise ValueError("vacancy ID mismatch")
            if not isinstance(payload.get("key_skills"), list) or "description" not in payload:
                raise ValueError("missing detail fields")
            if payload["description"] is not None and not isinstance(payload["description"], str):
                raise ValueError("invalid description")
            return "success", payload, None
        except (ValueError, TypeError, AttributeError):
            return "retry", None, "invalid_detail_response"
    if code in (401, 403):
        return "blocked", None, f"http_{code}"
    if code in (404, 410):
        return "not_found", None, f"http_{code}"
    if code == 429 or code >= 500:
        return "retry", None, f"http_{code}"
    return "failed", None, f"http_{code}"


def build_api_session():
    user_agent = os.getenv("HH_USER_AGENT", "").strip()
    token = os.getenv("HH_ACCESS_TOKEN", "").strip()
    if not user_agent or not token:
        raise RuntimeError("Set HH_USER_AGENT (application/contact) and HH_ACCESS_TOKEN; see README.")
    session = requests.Session()
    session.headers.update({"HH-User-Agent": user_agent, "User-Agent": user_agent,
                            "Accept": "application/json", "Authorization": f"Bearer {token}"})
    return session


def run_enrichment(limit=50, pause=1.2):
    if limit <= 0 or pause < 1:
        raise ValueError("limit must be positive and pause >= 1 second")
    counts = {}
    started_at = datetime.now(timezone.utc)
    with build_api_session() as session, get_connection() as leader:
        with leader.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(20261105, 1)")
            locked = cur.fetchone()[0]
        leader.commit()
        if not locked:
            raise RuntimeError("An enrichment worker is already running")
        try:
            with get_connection() as conn, conn.cursor() as cur:
                cur.execute("""SELECT vacancy_id, enrichment_attempts FROM stg.hh_vacancies
                    WHERE enrichment_status IN ('pending', 'retry')
                      AND enrichment_attempts < 5
                      AND (next_retry_at IS NULL OR next_retry_at <= NOW())
                    ORDER BY fetched_at, vacancy_id LIMIT %s""", (limit,))
                queue = cur.fetchall()
            for vacancy_id, attempts in queue:
                attempts += 1
                code, retry_header = None, None
                try:
                    response = session.get(f"{HH_API_BASE_URL}/vacancies/{vacancy_id}", timeout=(5, 30))
                    code = response.status_code
                    retry_header = response.headers.get("Retry-After")
                    status, detail, error = interpret_response(response, vacancy_id)
                except requests.RequestException:
                    status, detail, error = "retry", None, "network_error"
                if status == "retry" and attempts >= 5:
                    status = "failed"
                due = retry_at(attempts, retry_header) if status == "retry" else None
                patch = None
                if detail is not None:
                    patch = json.dumps({"key_skills": detail["key_skills"],
                                        "description_text": html_to_text(detail.get("description"))}, ensure_ascii=False)
                # Network outside transactions; persist each result so a restart resumes.
                with get_connection() as conn, conn.cursor() as cur:
                    cur.execute("""UPDATE stg.hh_vacancies SET
                        enrichment_status=%s, enrichment_attempts=%s, last_http_status=%s,
                        next_retry_at=%s, last_error=%s,
                        enriched_at=CASE WHEN %s='success' THEN NOW() ELSE enriched_at END,
                        detail_payload=COALESCE(%s::jsonb, detail_payload),
                        payload=payload || COALESCE(%s::jsonb, '{}'::jsonb)
                        WHERE vacancy_id=%s""", (status, attempts, code, due, error, status,
                        json.dumps(detail, ensure_ascii=False) if detail is not None else None, patch, vacancy_id))
                counts[status] = counts.get(status, 0) + 1
                print(f"[ENRICH] {vacancy_id}: {status}; HTTP={code}; attempt={attempts}")
                if status == "blocked" or code == 429:
                    break
                time.sleep(pause)
        finally:
            with leader.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(20261105, 1)")
            leader.commit()
    print("[ENRICH]", counts)
    log_dq_audit("ENRICH_API", started_at, sum(counts.values()), None, None,
                 "WARN" if any(k != "success" for k in counts) else "PASS", json.dumps(counts))
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--pause", type=float, default=1.2)
    args = parser.parse_args()
    run_enrichment(args.limit, args.pause)
