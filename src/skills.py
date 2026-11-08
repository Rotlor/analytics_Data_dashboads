"""Explicit tool mentions only. Taxonomy version: tools-v0.1.1.

Presence is an observation, not proof that a skill is required. Generic concepts
and inferred tools (SQL from DWH, dbt from role title) are intentionally excluded.
"""
TOOLS = {
    "Languages": {
        "SQL": r"sql|t-sql|pl/pgsql|pl/sql", "Python": r"python|питон",
        "R": r"r|rstudio", "Scala": r"scala", "Java": r"java", "Go": r"golang",
        "C++": r"c\+\+",
    },
    "Databases": {
        "PostgreSQL": r"postgres(?:ql)?|постгрес", "ClickHouse": r"click\s*house|кликхаус",
        "Greenplum": r"greenplum|гринплам", "MS SQL Server": r"ms\s*sql|sql\s*server",
        "Oracle": r"oracle", "Redis": r"redis", "MongoDB": r"mongodb",
        "Cassandra": r"cassandra", "Elasticsearch": r"elasticsearch",
    },
    "Data platform": {
        "Airflow": r"airflow|эйрфлоу|аирфлоу", "dbt": r"dbt|data\s+build\s+tool",
        "Spark": r"spark|pyspark|спарк", "Kafka": r"kafka|кафка", "RabbitMQ": r"rabbit\s*mq",
        "Debezium": r"debezium", "Hadoop": r"hadoop", "Trino": r"trino",
        "NiFi": r"nifi", "Informatica": r"informatica", "Airbyte": r"airbyte",
        "S3": r"s3", "MinIO": r"minio", "Iceberg": r"iceberg", "Delta Lake": r"delta\s*lake",
    },
    "BI": {
        "Power BI": r"power\s*bi|pbi", "DAX": r"dax", "Power Query": r"power\s*query",
        "Tableau": r"tableau", "Superset": r"superset|суперсет", "Redash": r"redash|редаш",
        "DataLens": r"data\s*lens|даталенс", "Qlik": r"qlik(?:sense|view)?",
        "Metabase": r"metabase", "Grafana": r"grafana", "Excel": r"excel|эксель",
    },
    "Infrastructure": {
        "Docker": r"docker|докер", "Kubernetes": r"kubernetes|k8s", "Git": r"git|gitlab|github",
        "Linux": r"linux", "Bash": r"bash", "MLflow": r"mlflow", "DVC": r"dvc",
    },
    "Libraries": {
        "Pandas": r"pandas", "NumPy": r"numpy", "SciPy": r"scipy",
        "scikit-learn": r"scikit[\s-]*learn|sklearn", "CatBoost": r"catboost",
        "XGBoost": r"xgboost", "LightGBM": r"lightgbm", "PyTorch": r"pytorch",
        "TensorFlow": r"tensorflow", "Keras": r"keras", "OpenCV": r"open\s*cv",
        "ONNX": r"onnx(?:runtime)?", "Transformers": r"transformers",
    },
}
SKILL_CATALOG = tuple(
    (name, category, r"(?<![\w])(?:" + aliases + r")(?![\w])")
    for category, tools in TOOLS.items() for name, aliases in tools.items()
)
