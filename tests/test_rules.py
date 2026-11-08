import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

import numpy as np
import pandas as pd

from src.config import classify_vacancy_title
from src.enrich import html_to_text, interpret_response, retry_at
from src.rules import calc_progressive_gross_rub, calc_progressive_net_rub, classify_grade, normalize_salaries
from src.skills import SKILL_CATALOG


class SalaryTests(unittest.TestCase):
    def test_tax_thresholds_independently(self):
        annual = np.array([0, 2_400_000, 5_000_000, 20_000_000, 50_000_000, 60_000_000])
        tax = np.array([0, 312_000, 702_000, 3_402_000, 9_402_000, 11_602_000])
        np.testing.assert_allclose(calc_progressive_net_rub(annual / 12), (annual - tax) / 12)

    def test_inverse_roundtrip_at_boundaries_and_large_salaries(self):
        gross = pd.Series([1., 100_000., 200_000., 200_001., 5e6 / 12, 20e6 / 12, 50e6 / 12, 8e6])
        np.testing.assert_allclose(calc_progressive_gross_rub(calc_progressive_net_rub(gross)), gross, atol=.0001)

    def test_null_and_negative_tax(self):
        result = calc_progressive_net_rub(pd.Series([None, -1.]))
        self.assertTrue(result.isna().all())

    def normalize(self, **patch):
        row = dict(salary_from=100_000., salary_to=200_000., salary_currency="RUR", salary_gross=True)
        row.update(patch)
        return normalize_salaries(pd.DataFrame([row]), {"RUR": 1., "USD": .01}).iloc[0]

    def test_unknown_currency_is_not_rubles(self):
        self.assertEqual(self.normalize(salary_currency="XYZ").salary_status, "unknown_currency")

    def test_unknown_gross_is_not_net(self):
        self.assertEqual(self.normalize(salary_gross=None).salary_status, "unknown_gross")

    def test_reversed_bounds(self):
        self.assertEqual(self.normalize(salary_from=300_000).salary_status, "invalid_bounds")

    def test_no_automatic_thousands(self):
        self.assertEqual(self.normalize(salary_from=180, salary_to=None).salary_status, "outside_mvp_range")

    def test_currency_direction(self):
        self.assertAlmostEqual(self.normalize(salary_from=1000, salary_to=None, salary_currency="USD").salary_est_net_rub, 87_000)

    def test_upper_only_heuristic_is_named(self):
        row = self.normalize(salary_from=None, salary_to=200_000, salary_gross=False)
        self.assertEqual(row.salary_est_method, "upper_x_0.85")
        self.assertEqual(row.salary_est_net_rub, 170_000)

    def test_gross_midpoint_then_tax(self):
        row = self.normalize(salary_from=100_000, salary_to=500_000)
        self.assertEqual(row.salary_est_net_rub, 259_000)
        self.assertEqual(row.salary_est_gross_rub, 300_000)

    def test_net_inverse_is_progressive(self):
        row = self.normalize(salary_from=500_000, salary_to=None, salary_gross=False)
        self.assertAlmostEqual(calc_progressive_net_rub([row.salary_est_gross_rub]).iloc[0], 500_000, places=2)

    def test_hourly_is_not_monthly(self):
        self.assertEqual(self.normalize(salary_period="HOUR").salary_status, "unsupported_period")

    def test_empty_salary(self):
        row = self.normalize(salary_from=None, salary_to=None)
        self.assertEqual(row.salary_status, "not_disclosed")
        self.assertFalse(row.has_salary)

    def test_zero_bound_is_not_a_full_offer(self):
        self.assertFalse(self.normalize(salary_from=0).has_salary)


class ClassificationTests(unittest.TestCase):
    def test_six_years_does_not_make_a_lead(self):
        self.assertEqual(classify_grade("Senior Data Engineer", "moreThan6"), ("Senior", "title"))
        self.assertEqual(classify_grade("Data Engineer", "moreThan6"), ("Senior", "experience"))

    def test_title_overrides_experience(self):
        self.assertEqual(classify_grade("Senior BI Developer", "noExperience"), ("Senior", "title"))

    def test_ambiguous_title_is_visible(self):
        self.assertEqual(classify_grade("Junior / Senior Data Analyst", "between1And3"), ("Unknown", "ambiguous_title"))

    def test_unknown_experience(self):
        self.assertEqual(classify_grade("Data Analyst", "unknown"), ("Unknown", "missing"))

    def test_ml_qualifier_does_not_override_scientist(self):
        self.assertEqual(classify_vacancy_title("Data Scientist / ML"), ("Data Science & ML", "Data Scientist"))

    def test_no_inferred_skills_from_job_role(self):
        import re
        found = [name for name, _, pattern in SKILL_CATALOG if re.search(pattern, "Analytics Engineer DWH Developer", re.I)]
        self.assertEqual(found, [])

    def test_libraries_are_separate_tools(self):
        import re
        found = [name for name, _, pattern in SKILL_CATALOG if re.search(pattern, "Pandas NumPy PyTorch OpenCV ONNX", re.I)]
        self.assertEqual(set(found), {"Pandas", "NumPy", "PyTorch", "OpenCV", "ONNX"})


class EnrichmentTests(unittest.TestCase):
    def response(self, code, data=None):
        return Mock(status_code=code, json=Mock(return_value=data))

    def test_success_with_empty_employer_tags(self):
        state, payload, _ = interpret_response(self.response(200, {"id":"123", "key_skills":[], "description":""}), 123)
        self.assertEqual(state, "success")
        self.assertEqual(payload["key_skills"], [])

    def test_incorrect_vacancy_response_not_accepted(self):
        self.assertEqual(interpret_response(self.response(200, {"id":"999"}), 123)[0], "retry")

    def test_http_error_semantics(self):
        for code, expected in [(401,"blocked"), (403,"blocked"), (404,"not_found"), (429,"retry"), (503,"retry"), (400,"failed")]:
            with self.subTest(code=code):
                self.assertEqual(interpret_response(self.response(code), 123)[0], expected)

    def test_retry_after_is_respected(self):
        now = datetime(2026,11,5,tzinfo=timezone.utc)
        self.assertGreaterEqual((retry_at(1,"900",now)-now).total_seconds(), 900)
        self.assertGreaterEqual((retry_at(1,"Thu, 05 Nov 2026 01:00:00 GMT",now)-now).total_seconds(), 3600)

    def test_full_html_text_is_not_truncated(self):
        html = "<div><p>Python &amp; SQL</p><div>Airflow</div></div>" + " x" * 5000
        text = html_to_text(html)
        self.assertIn("Python & SQL Airflow", text)
        self.assertGreater(len(text), 4000)


if __name__ == "__main__":
    unittest.main()
