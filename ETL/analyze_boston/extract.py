"""CSV parsing and extract artifact writing for Analyze Boston assessments."""

from __future__ import annotations

import argparse
import csv
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from ETL.analyze_boston.config import CORE_COLUMNS, SOURCE_DATASET_URL, SOURCE_NAME
from ETL.analyze_boston.normalize import (
    assessment_record_from_row,
    core_csv_row,
    is_housing_record,
    matches_geography,
    normalize_header,
    normalize_postal_code,
    normalize_row,
    utc_now_iso,
)
from ETL.analyze_boston.source import AssessmentResource


LOGGER = logging.getLogger(__name__)
EXTRACT_PROGRESS_INTERVAL = 25_000


@dataclass
class ExtractCounts:
    """Counters written to the manifest for observability."""

    records_read: int = 0
    geography_records: int = 0
    housing_records: int = 0
    raw_geography_records_written: int = 0
    core_housing_records_written: int = 0
    raw_housing_jsonl_records_written: int = 0


def output_paths(
    output_root: Path,
    fiscal_year: int | None,
    label: str,
) -> dict[str, Path]:
    """Return the standard extract paths for one run."""

    suffix = f"fy{fiscal_year}" if fiscal_year else "unknown_fy"
    extract_dir = output_root / "extracted" / "analyze_boston"
    return {
        "raw_geography_csv": extract_dir
        / f"{label}_property_assessment_raw_{suffix}.csv",
        "raw_housing_jsonl": extract_dir / f"{label}_housing_raw_{suffix}.jsonl",
        "core_housing_csv": extract_dir / f"{label}_housing_core_{suffix}.csv",
        "manifest": extract_dir / f"{label}_manifest_{suffix}.json",
    }


def extract_housing_records(
    source_csv: Path,
    paths: dict[str, Path],
    resource: AssessmentResource | None,
    assessment_year_override: int | None,
    zip_codes: Iterable[str],
    city_names: Iterable[str],
    match_mode: str,
    include_mixed_use: bool,
    include_parking: bool,
    include_condo_main: bool,
) -> ExtractCounts:
    """Parse the source CSV and write scoped raw/core extract artifacts."""

    counts = ExtractCounts()
    extracted_at = utc_now_iso()
    zip_code_set = {normalize_postal_code(value) or value for value in zip_codes}
    city_name_set = {(value or "").upper() for value in city_names if value}

    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)

    LOGGER.info("Extracting target geography and housing records from %s...", source_csv)
    LOGGER.info(
        "Filters: ZIP=%s, city=%s, match_mode=%s.",
        ", ".join(zip_code_set) or "any",
        ", ".join(sorted(city_name_set)) or "any",
        match_mode,
    )

    with source_csv.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames:
            raise RuntimeError(f"{source_csv} has no CSV header row.")
        normalized_fieldnames = [normalize_header(field) for field in reader.fieldnames]

        with (
            paths["raw_geography_csv"].open("w", newline="", encoding="utf-8") as raw_csv,
            paths["core_housing_csv"].open("w", newline="", encoding="utf-8") as core_csv,
            paths["raw_housing_jsonl"].open("w", encoding="utf-8") as raw_jsonl,
        ):
            raw_writer = csv.DictWriter(raw_csv, fieldnames=normalized_fieldnames)
            core_writer = csv.DictWriter(core_csv, fieldnames=CORE_COLUMNS)
            raw_writer.writeheader()
            core_writer.writeheader()

            for raw_row in reader:
                counts.records_read += 1
                if counts.records_read % EXTRACT_PROGRESS_INTERVAL == 0:
                    LOGGER.info(
                        "Scanned %s rows; matched %s geography rows; "
                        "wrote %s housing rows.",
                        f"{counts.records_read:,}",
                        f"{counts.geography_records:,}",
                        f"{counts.core_housing_records_written:,}",
                    )
                row = normalize_row(raw_row)
                if not matches_geography(row, zip_code_set, city_name_set, match_mode):
                    continue

                counts.geography_records += 1
                raw_writer.writerow({field: row.get(field, "") for field in normalized_fieldnames})
                counts.raw_geography_records_written += 1

                if not is_housing_record(
                    row,
                    include_mixed_use=include_mixed_use,
                    include_parking=include_parking,
                    include_condo_main=include_condo_main,
                ):
                    continue

                counts.housing_records += 1
                record = assessment_record_from_row(
                    row,
                    resource=resource,
                    source_csv=source_csv,
                    extracted_at=extracted_at,
                    assessment_year_override=assessment_year_override,
                )
                raw_jsonl.write(
                    json.dumps(
                        {
                            "source_name": SOURCE_NAME,
                            "source_record_key": record.source_record_key,
                            "source_record_hash": record.source_record_hash,
                            "assessment_year": record.assessment_year,
                            "observed_at": extracted_at,
                            "payload": row,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + "\n"
                )
                counts.raw_housing_jsonl_records_written += 1
                core_writer.writerow(core_csv_row(record))
                counts.core_housing_records_written += 1

    LOGGER.info(
        "Extraction complete: scanned %s rows; matched %s geography rows; "
        "wrote %s housing rows.",
        f"{counts.records_read:,}",
        f"{counts.geography_records:,}",
        f"{counts.core_housing_records_written:,}",
    )
    return counts


def write_manifest(
    manifest_path: Path,
    source_csv: Path,
    resource: AssessmentResource | None,
    counts: ExtractCounts,
    paths: dict[str, Path],
    args: argparse.Namespace,
) -> None:
    """Write source metadata, run configuration, output paths, and counts."""

    LOGGER.info("Writing manifest to %s.", manifest_path)
    payload = {
        "source_name": SOURCE_NAME,
        "source_dataset_url": SOURCE_DATASET_URL,
        "source_csv": str(source_csv),
        "resource": asdict(resource) if resource else None,
        "run": {
            "completed_at": utc_now_iso(),
            "zip_codes": args.zip_code,
            "city_names": args.city,
            "geography_match_mode": args.match_mode,
            "include_mixed_use": not args.exclude_mixed_use,
            "include_parking": args.include_parking,
            "include_condo_main": not args.exclude_condo_main,
        },
        "counts": asdict(counts),
        "outputs": {name: str(path) for name, path in paths.items()},
    }
    manifest_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
