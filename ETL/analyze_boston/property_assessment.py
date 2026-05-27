"""Command-line orchestration for Analyze Boston assessment ingestion.

The implementation is split into focused modules:

* ``source`` discovers/downloads the latest Analyze Boston CSV.
* ``extract`` parses the CSV and writes audit-friendly extract artifacts.
* ``load`` inserts raw records and maps relevant fields into canonical tables.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import asdict
from pathlib import Path
from urllib.error import URLError

from ETL.analyze_boston.config import (
    CKAN_PACKAGE_URL,
    DEFAULT_CITY_NAMES,
    DEFAULT_DB_ENV_FILE,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_ZIP_CODES,
)
from ETL.analyze_boston.extract import extract_housing_records, output_paths, write_manifest
from ETL.analyze_boston.load import LoadCounts, load_assessments_to_postgres
from ETL.analyze_boston.source import (
    AssessmentResource,
    discover_latest_resource,
    download_resource,
    parse_fiscal_year,
)
from ETL.database_init.db import get_engine


LOGGER = logging.getLogger(__name__)


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI used by local runs and future schedulers."""

    parser = argparse.ArgumentParser(
        description=(
            "Download/extract Analyze Boston property assessment data and "
            "optionally load it into PostgreSQL."
        ),
    )
    parser.add_argument(
        "--source-csv",
        type=Path,
        help="Use an existing local assessment CSV instead of downloading the latest resource.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Root directory for raw downloads and extracted files. Default: data",
    )
    parser.add_argument(
        "--package-url",
        default=CKAN_PACKAGE_URL,
        help="Analyze Boston CKAN package_show URL.",
    )
    parser.add_argument(
        "--zip-code",
        action="append",
        default=None,
        help="ZIP code to include. Repeat for multiple ZIP codes. Default: 02128",
    )
    parser.add_argument(
        "--city",
        action="append",
        default=None,
        help="City name to include. Repeat for multiple city names. Default: EAST BOSTON",
    )
    parser.add_argument(
        "--match-mode",
        choices=("any", "all"),
        default="any",
        help="Use any to match city OR ZIP, all to require both. Default: any",
    )
    parser.add_argument(
        "--label",
        default="east_boston",
        help="Output filename prefix. Default: east_boston",
    )
    parser.add_argument(
        "--assessment-year",
        type=int,
        help=(
            "Assessment year to use when the CSV/resource name does not include "
            "an FY year, such as local sample files."
        ),
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Re-download the latest source CSV even if it already exists locally.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Network timeout in seconds for CKAN metadata and CSV downloads. Default: 60",
    )
    parser.add_argument(
        "--exclude-mixed-use",
        action="store_true",
        help="Exclude mixed residential/commercial records from the housing extract.",
    )
    parser.add_argument(
        "--include-parking",
        action="store_true",
        help="Include residential parking assessment records in the housing extract.",
    )
    parser.add_argument(
        "--exclude-condo-main",
        action="store_true",
        help="Exclude CONDO MAIN records from the housing extract.",
    )
    parser.add_argument(
        "--load-db",
        action="store_true",
        help="Load raw and canonical assessment records into PostgreSQL.",
    )
    parser.add_argument(
        "--database-url",
        help="SQLAlchemy database URL. Defaults to DATABASE_URL or Database/.env values.",
    )
    parser.add_argument(
        "--db-env-file",
        type=Path,
        default=DEFAULT_DB_ENV_FILE,
        help="Path to Postgres env file. Default: Database/.env",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print final summary and errors.",
    )
    return parser


def resolve_source_csv(args: argparse.Namespace) -> tuple[Path, AssessmentResource | None]:
    """Return the local CSV path, downloading it first when needed."""

    if args.source_csv:
        source_csv = args.source_csv
        if not source_csv.exists():
            raise FileNotFoundError(f"--source-csv does not exist: {source_csv}")
        LOGGER.info("Using local source CSV: %s", source_csv)
        return source_csv, None

    resource = discover_latest_resource(args.package_url, timeout=args.timeout)
    source_csv = download_resource(
        resource,
        raw_dir=args.output_root / "raw" / "analyze_boston",
        timeout=args.timeout,
        force=args.force_download,
    )
    return source_csv, resource


def build_source_query(
    args: argparse.Namespace,
    source_csv: Path,
    resource: AssessmentResource | None,
    paths: dict[str, Path],
) -> dict:
    """Capture enough run metadata to reproduce the source slice."""

    return {
        "source_csv": str(source_csv),
        "source_resource": asdict(resource) if resource else None,
        "assessment_year_override": args.assessment_year,
        "zip_codes": args.zip_code,
        "city_names": args.city,
        "geography_match_mode": args.match_mode,
        "include_mixed_use": not args.exclude_mixed_use,
        "include_parking": args.include_parking,
        "include_condo_main": not args.exclude_condo_main,
        "core_housing_csv": str(paths["core_housing_csv"]),
        "raw_housing_jsonl": str(paths["raw_housing_jsonl"]),
    }


def print_summary(
    source_csv: Path,
    paths: dict[str, Path],
    counts,
    load_counts: LoadCounts | None,
) -> None:
    """Print a concise summary for terminal users and scheduled logs."""

    print(f"Read {counts.records_read:,} assessment records from {source_csv}.")
    print(f"Matched {counts.geography_records:,} target geography records.")
    print(f"Wrote {counts.core_housing_records_written:,} housing core records.")
    print(f"Core CSV: {paths['core_housing_csv']}")
    print(f"Raw geography CSV: {paths['raw_geography_csv']}")
    print(f"Raw housing JSONL: {paths['raw_housing_jsonl']}")
    print(f"Manifest: {paths['manifest']}")
    if load_counts:
        print(f"Postgres source_id: {load_counts.source_id}")
        print(f"Postgres ingestion_run_id: {load_counts.ingestion_run_id}")
        print(f"Loaded {load_counts.raw_records_inserted:,} new raw_source_records.")
        print(f"Skipped {load_counts.raw_records_skipped:,} duplicate raw_source_records.")
        print(f"Inserted {load_counts.properties_inserted:,} properties.")
        print(f"Updated {load_counts.properties_updated:,} properties.")
        print(f"Upserted {load_counts.assessments_upserted:,} assessments.")
        print(f"Upserted {load_counts.taxes_upserted:,} tax rows.")
        print(f"Linked {load_counts.owners_linked:,} current owners.")
        print(f"Inserted/updated {load_counts.features_inserted:,} assessor features.")


def configure_logging(quiet: bool) -> None:
    """Configure human-readable progress logs for CLI runs."""

    level = logging.WARNING if quiet else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def main(argv: list[str] | None = None) -> int:
    """Run discovery/download, extraction, and optional database loading."""

    parser = build_arg_parser()
    args = parser.parse_args(argv)
    configure_logging(quiet=args.quiet)
    args.zip_code = args.zip_code or list(DEFAULT_ZIP_CODES)
    args.city = args.city or list(DEFAULT_CITY_NAMES)

    try:
        LOGGER.info("Starting Analyze Boston property assessment ETL.")
        source_csv, resource = resolve_source_csv(args)
        fiscal_year = (
            args.assessment_year
            or (resource.fiscal_year if resource else parse_fiscal_year(source_csv.name))
        )
        LOGGER.info("Assessment year: %s.", fiscal_year or "unknown")
        paths = output_paths(args.output_root, fiscal_year=fiscal_year, label=args.label)
        counts = extract_housing_records(
            source_csv=source_csv,
            paths=paths,
            resource=resource,
            assessment_year_override=args.assessment_year,
            zip_codes=args.zip_code,
            city_names=args.city,
            match_mode=args.match_mode,
            include_mixed_use=not args.exclude_mixed_use,
            include_parking=args.include_parking,
            include_condo_main=not args.exclude_condo_main,
        )
        write_manifest(
            paths["manifest"],
            source_csv=source_csv,
            resource=resource,
            counts=counts,
            paths=paths,
            args=args,
        )

        load_counts: LoadCounts | None = None
        if args.load_db:
            LOGGER.info("Connecting to PostgreSQL...")
            source_url = (
                resource.url
                if resource
                else "https://data.boston.gov/dataset/property-assessment"
            )
            engine = get_engine(database_url=args.database_url, env_file=args.db_env_file)
            load_counts = load_assessments_to_postgres(
                engine,
                raw_housing_jsonl=paths["raw_housing_jsonl"],
                source_url=source_url,
                source_query=build_source_query(args, source_csv, resource, paths),
                source_csv=source_csv,
                resource=resource,
                assessment_year_override=args.assessment_year,
            )
        else:
            LOGGER.info("Skipping database load because --load-db was not set.")

    except (RuntimeError, OSError, URLError) as exc:
        print(f"Analyze Boston ETL failed: {exc}", file=sys.stderr)
        return 1

    print_summary(source_csv, paths, counts, load_counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
