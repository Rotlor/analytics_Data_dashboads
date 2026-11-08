"""Collection and enrichment are opt-in; bootstrap never deletes data."""
import argparse
from datetime import datetime, timezone

from src.db import bootstrap_database, log_dq_audit
from src.transform import DWHTransformer
from src.validate import run_validation_suite


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--extract", action="store_true")
    parser.add_argument("--enrich-limit", type=int, default=0)
    parser.add_argument("--init-only", action="store_true")
    args = parser.parse_args()
    started = datetime.now(timezone.utc)
    try:
        bootstrap_database()
        if args.init_only:
            return
        if args.extract:
            from src.extract import HHExtractor
            extractor = HHExtractor()
            try:
                extractor.run()
            finally:
                extractor.session.close()
        if args.enrich_limit:
            from src.enrich import run_enrichment
            run_enrichment(args.enrich_limit)
        DWHTransformer().transform_and_load()
        run_validation_suite()
    except Exception as exc:
        try:
            log_dq_audit("PIPELINE", started, 0, None, None, "FAIL", type(exc).__name__)
        except Exception:
            pass  # Logging must not hide the original error.
        raise


if __name__ == "__main__":
    main()
