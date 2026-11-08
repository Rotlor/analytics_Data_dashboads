import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class DBConfig:
    host: str = field(default_factory=lambda: os.getenv("DWH_HOST", "localhost"))
    port: int = field(default_factory=lambda: int(os.getenv("DWH_PORT", "5432")))
    user: str = field(default_factory=lambda: os.getenv("DWH_USER", "hh_owner"))
    password: str = field(default_factory=lambda: os.getenv("DWH_PASSWORD", ""))
    dbname: str = field(default_factory=lambda: os.getenv("DWH_DB", "hh_dwh"))


@dataclass(frozen=True)
class RoleSpec:
    role_family: str
    role_title: str
    queries: tuple[str, ...]
    title_pattern: re.Pattern


HH_API_BASE_URL = "https://api.hh.ru"
HH_WEB_SEARCH_URL = "https://hh.ru/search/vacancy"

# Исключения из целевой выборки.
STOP_TITLE_PATTERN = re.compile(
    r"(\b1с\b|\b1c\b|бухгалтер|продаж|маркетолог|закуп|логист|склад|кадр|hr[\s-]*аналитик|системн\w*\s+аналитик|system\s+analyst)",
    re.IGNORECASE,
)

# Правила классификации названий вакансий.
ROLE_SPECS: tuple[RoleSpec, ...] = (
    RoleSpec(
        role_family="BI & Reporting",
        role_title="BI Developer",
        queries=(
            "BI разработчик",
            "BI developer",
            "Разработчик BI",
            "Power BI разработчик",
            "Tableau разработчик",
            "BI Engineer",
        ),
        title_pattern=re.compile(
            r"(\bbi\b.*(разработчик|developer|инженер|engineer|архитектор|architect)"
            r"|(разработчик|developer|инженер|engineer).*\bbi\b"
            r"|(power\s*bi|tableau|superset|qlik|datalens|redash).*(разработчик|developer))",
            re.IGNORECASE,
        ),
    ),
    RoleSpec(
        role_family="BI & Reporting",
        role_title="BI Analyst",
        queries=("BI аналитик", "BI analyst", "Аналитик BI"),
        title_pattern=re.compile(
            r"(\bbi\b.*(аналитик|analyst)|(аналитик|analyst).*\bbi\b|business\s+intelligence\s+analyst)",
            re.IGNORECASE,
        ),
    ),
    RoleSpec(
        role_family="Data Engineering",
        role_title="Analytics Engineer",
        queries=("Analytics Engineer", "Инженер-аналитик данных"),
        title_pattern=re.compile(
            r"(analytics\s+engineer|аналитик[\s-]*инженер\s+данных|инженер[\s-]*аналитик\s+данных)",
            re.IGNORECASE,
        ),
    ),
    RoleSpec(
        role_family="Data Engineering",
        role_title="DWH / ETL Developer",
        queries=("DWH разработчик", "Разработчик DWH", "ETL разработчик", "Инженер DWH"),
        title_pattern=re.compile(
            r"(\bdwh\b|\betl\b|хранилищ\w*\s+данных)",
            re.IGNORECASE,
        ),
    ),
    RoleSpec(
        role_family="Data Engineering",
        role_title="Data Engineer",
        queries=("Data Engineer", "Инженер данных", "Дата инженер", "Data Architect"),
        title_pattern=re.compile(
            r"(data\s*engineer|инженер\s*данных|дата[\s-]*инженер|data\s*architect|архитектор\s*данных)",
            re.IGNORECASE,
        ),
    ),
    RoleSpec(
        role_family="Data Analytics",
        role_title="Product Analyst",
        queries=("Продуктовый аналитик", "Product Analyst", "Аналитик продукта"),
        title_pattern=re.compile(
            r"(продуктов\w*\s+аналитик|product\s+analyst|аналитик\s+продукт\w*)",
            re.IGNORECASE,
        ),
    ),
    RoleSpec(
        role_family="Data Analytics",
        role_title="Data Analyst",
        queries=("Data Analyst", "Аналитик данных", "Дата аналитик"),
        title_pattern=re.compile(
            r"(data\s*analyst|аналитик\s*(баз\s*)?данных|дата[\s-]*аналитик)",
            re.IGNORECASE,
        ),
    ),
    RoleSpec(
        role_family="Data Science & ML",
        role_title="ML Engineer",
        queries=("ML Engineer", "ML инженер", "Machine Learning Engineer", "ML разработчик", "NLP Engineer", "CV Engineer"),
        title_pattern=re.compile(
            r"(\bml\b|machine\s+learning|машинн\w+\s+обучен|\bnlp\b|\bcv\b|computer\s+vision|\bllm\b|mlops)",
            re.IGNORECASE,
        ),
    ),
    RoleSpec(
        role_family="Data Science & ML",
        role_title="Data Scientist",
        queries=("Data Scientist", "Дата сайентист", "Data Science"),
        title_pattern=re.compile(
            r"(data\s*scientist|data\s*science|дата[\s-]*сайентист|исследователь\s+данных)",
            re.IGNORECASE,
        ),
    ),
)

TARGET_AREAS: tuple[int, ...] = (113,)  # Россия
REQUEST_SLEEP_SEC: float = 0.35


def classify_vacancy_title(title: str) -> tuple[str, str] | None:
    """
    Детерминированно классифицирует вакансию по её названию в (role_family, role_title).
    Возвращает None, если заголовок содержит стоп-слова или не подходит ни под одну из 9 ролей.
    """
    if not title or STOP_TITLE_PATTERN.search(title):
        return None
    # An explicit Data Scientist title takes priority over a later ML qualifier.
    specs = (*ROLE_SPECS[:-2], ROLE_SPECS[-1], ROLE_SPECS[-2])
    for spec in specs:
        if spec.title_pattern.search(title):
            return spec.role_family, spec.role_title
    return None
