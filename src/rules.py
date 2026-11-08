"""Pure, testable MVP rules; no database or network side effects."""
import re

import numpy as np
import pandas as pd


THRESHOLDS = np.array([2_400_000., 5_000_000., 20_000_000., 50_000_000.])


def calc_progressive_net_rub(gross_monthly):
    g = pd.Series(gross_monthly, dtype="float64")
    annual = g * 12
    tax = .13 * annual
    for threshold, increment in zip(THRESHOLDS, [.02, .03, .02, .02]):
        tax += increment * np.clip(annual - threshold, 0, None)
    return (g - tax / 12).where(g >= 0)


def calc_progressive_gross_rub(net_monthly):
    n = pd.Series(net_monthly, dtype="float64")
    annual = n * 12
    gross = annual / .87
    # At threshold G: net = G*(1-rate) + accumulated tax intercept.
    for threshold, rate, intercept in [
        (2_400_000., .15, 48_000.),
        (5_000_000., .18, 198_000.),
        (20_000_000., .20, 598_000.),
        (50_000_000., .22, 1_598_000.),
    ]:
        net_threshold = threshold * (1 - rate) + intercept
        gross = gross.where(annual <= net_threshold, (annual - intercept) / (1 - rate))
    return (gross / 12).where(n >= 0)


def normalize_salaries(df, rates):
    """HH dictionary rate is currency units per RUB; salary / rate -> RUB.

    Legacy web payloads lack salary period: MVP explicitly assumes monthly.
    No silent correction of thousands or unknown gross/currency/period.
    """
    result = df.copy()
    lower = pd.to_numeric(df["salary_from"], errors="coerce")
    upper = pd.to_numeric(df["salary_to"], errors="coerce")
    fx = pd.to_numeric(df["salary_currency"].map(rates), errors="coerce")
    disclosed = lower.notna() | upper.notna()
    gross_known = df["salary_gross"].map(lambda value: isinstance(value, (bool, np.bool_)))
    period = df.get("salary_period", pd.Series("MONTH", index=df.index))
    status = pd.Series("ok", index=df.index)
    status.loc[~disclosed] = "not_disclosed"
    status.loc[disclosed & (~np.isfinite(fx) | (fx <= 0))] = "unknown_currency"
    status.loc[disclosed & ~gross_known] = "unknown_gross"
    status.loc[disclosed & ~period.eq("MONTH")] = "unsupported_period"
    invalid = ((lower.notna() & (~np.isfinite(lower) | (lower <= 0))) |
               (upper.notna() & (~np.isfinite(upper) | (upper <= 0))) |
               (lower.notna() & upper.notna() & (lower > upper)))
    status.loc[disclosed & invalid] = "invalid_bounds"
    rate = fx.where(np.isfinite(fx) & (fx > 0))
    low_rub, high_rub = lower / rate, upper / rate
    both = lower.notna() & upper.notna()
    point = low_rub.where(lower.notna(), high_rub * .85)
    point = point.where(~both, (low_rub + high_rub) / 2)
    gross_flag = df["salary_gross"].eq(True)
    net = point.where(~gross_flag, calc_progressive_net_rub(point))
    status.loc[status.eq("ok") & ~net.between(25_000, 1_500_000)] = "outside_mvp_range"
    valid = status.eq("ok")
    result["salary_disclosed"] = disclosed
    result["salary_status"] = status
    result["salary_est_method"] = np.select(
        [both, lower.notna(), upper.notna()], ["midpoint", "lower_bound", "upper_x_0.85"],
        default="none",
    )
    result["has_salary"] = valid
    result["salary_est_net_rub"] = net.where(valid).round(2)
    gross = point.where(gross_flag, calc_progressive_gross_rub(point))
    result["salary_est_gross_rub"] = gross.where(valid).round(2)
    for name, value in [("salary_from_net_rub", low_rub), ("salary_to_net_rub", high_rub)]:
        result[name] = value.where(~gross_flag, calc_progressive_net_rub(value)).where(valid).round(2)
    result["effective_ndfl_pct"] = ((1 - net / gross) * 100).where(valid).round(2)
    return result


def classify_grade(title, experience):
    def match(pattern):
        return re.search(r"(?<![\w])(?:" + pattern + r")(?![\w])", title, re.I)
    lead = match(r"team\s*lead|tech\s*lead|lead|тимлид|лид|head|руководитель|директор|начальник")
    junior = match(r"junior|джуниор|джун|младший|стаж[её]р|intern|trainee|начинающий|ассистент")
    senior = match(r"senior|сеньор|синьор|старший|ведущий|главный|эксперт|sr|principal|staff|architect|архитектор")
    middle = match(r"middle|мидл|миддл")
    if sum(bool(x) for x in (lead, junior, senior, middle)) > 1:
        return "Unknown", "ambiguous_title"
    for flag, grade in [(lead, "Lead"), (senior, "Senior"), (junior, "Junior"), (middle, "Middle")]:
        if flag:
            return grade, "title"
    return {"noExperience": ("Junior", "experience"),
            "between1And3": ("Middle", "experience"),
            "between3And6": ("Middle", "experience"),
            "moreThan6": ("Senior", "experience")}.get(experience, ("Unknown", "missing"))
