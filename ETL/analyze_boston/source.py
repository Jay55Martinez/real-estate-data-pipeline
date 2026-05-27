"""Analyze Boston CKAN discovery and CSV download helpers."""

from __future__ import annotations

import json
import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from ETL.analyze_boston.config import DOWNLOAD_CHUNK_SIZE


USER_AGENT = "Mozilla/5.0 real-estate-data-pipeline/0.1"
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class AssessmentResource:
    """Metadata for the selected annual property assessment CSV resource."""

    fiscal_year: int
    resource_id: str
    name: str
    url: str
    format: str
    size: int | None
    hash: str | None
    created_at: str | None
    metadata_modified_at: str | None
    last_modified_at: str | None


def fetch_json(url: str, timeout: int) -> dict:
    """Fetch a JSON document using a project-specific user agent."""

    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def resource_display_name(resource: dict) -> str:
    """Return the most useful human-readable name CKAN has for a resource."""

    translated = resource.get("name_translated") or {}
    return (
        translated.get("en")
        or resource.get("name")
        or resource.get("description")
        or resource.get("url")
        or ""
    )


def parse_fiscal_year(*values: str | None) -> int | None:
    """Extract an FY20XX year from filenames, resource names, or URLs."""

    for value in values:
        if not value:
            continue
        patterns = (
            r"\bFY\s*(20\d{2})\b",
            r"\bfy(20\d{2})\b",
            r"\bproperty[-_ ]assessment[-_ ]fy(20\d{2})\b",
            r"\bdata(20\d{2})[-_ ]?full\b",
        )
        for pattern in patterns:
            match = re.search(pattern, value, flags=re.IGNORECASE)
            if match:
                return int(match.group(1))
    return None


def discover_latest_resource(package_url: str, timeout: int) -> AssessmentResource:
    """Find the newest annual CSV in the Analyze Boston property package."""

    LOGGER.info("Discovering latest Analyze Boston property assessment resource...")
    package = fetch_json(package_url, timeout=timeout)
    if not package.get("success"):
        raise RuntimeError(f"Analyze Boston package request failed: {package!r}")

    candidates: list[AssessmentResource] = []
    for resource in package.get("result", {}).get("resources", []):
        name = resource_display_name(resource)
        url = resource.get("url") or resource.get("original_url") or ""
        fmt = (resource.get("format") or "").upper()
        fiscal_year = parse_fiscal_year(name, url)
        if not fiscal_year:
            continue
        if fmt != "CSV" and not url.lower().endswith(".csv"):
            continue

        candidates.append(
            AssessmentResource(
                fiscal_year=fiscal_year,
                resource_id=resource.get("id") or resource.get("resource_id") or "",
                name=name,
                url=url,
                format=fmt or "CSV",
                size=resource.get("size"),
                hash=resource.get("hash") or None,
                created_at=resource.get("created"),
                metadata_modified_at=resource.get("metadata_modified"),
                last_modified_at=resource.get("last_modified"),
            )
        )

    if not candidates:
        raise RuntimeError("No property assessment CSV resources found in CKAN package.")

    latest = max(
        candidates,
        key=lambda item: (
            item.fiscal_year,
            item.metadata_modified_at or "",
            item.created_at or "",
        ),
    )
    LOGGER.info(
        "Selected %s (%s, resource_id=%s).",
        latest.name,
        latest.format,
        latest.resource_id,
    )
    return latest


def filename_from_url(url: str, fallback: str) -> str:
    """Build a stable local filename from a resource URL."""

    filename = Path(urlparse(url).path).name
    return filename or fallback


def download_resource(
    resource: AssessmentResource,
    raw_dir: Path,
    timeout: int,
    force: bool,
) -> Path:
    """Download a CKAN resource atomically and return its local path."""

    raw_dir.mkdir(parents=True, exist_ok=True)
    destination = raw_dir / filename_from_url(
        resource.url,
        f"property_assessment_fy{resource.fiscal_year}.csv",
    )

    if destination.exists() and not force:
        LOGGER.info("Using cached source CSV: %s", destination)
        return destination

    LOGGER.info("Downloading source CSV to %s...", destination)
    tmp_path: Path | None = None
    try:
        with open_download_url(resource.url, timeout=timeout) as response:
            with tempfile.NamedTemporaryFile(
                "wb",
                delete=False,
                dir=raw_dir,
                prefix=f".{destination.name}.",
                suffix=".part",
            ) as tmp:
                tmp_path = Path(tmp.name)
                bytes_written = copy_with_progress(response, tmp)
        tmp_path.replace(destination)
        LOGGER.info(
            "Downloaded %.1f MB to %s.",
            bytes_written / (1024 * 1024),
            destination,
        )
    except Exception:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink()
        raise

    return destination


class NoRedirectHandler(HTTPRedirectHandler):
    """Return 3xx responses to the caller so signed S3 URLs can be repaired."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_download_url(url: str, timeout: int):
    """Open a CKAN download URL, normalizing Boston's signed S3 redirect.

    Analyze Boston currently emits redirects to ``s3.amazonaws.com:443``.
    Because the generated AWS signature is scoped to the ``host`` header, S3
    rejects that redirected request. Removing the redundant HTTPS port keeps
    the signed URL semantically identical while matching the expected host.
    """

    request = Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/csv,*/*"},
    )
    opener = build_opener(NoRedirectHandler)
    try:
        return opener.open(request, timeout=timeout)
    except HTTPError as exc:
        if exc.code not in {301, 302, 303, 307, 308}:
            raise
        location = exc.headers.get("Location")
        if not location:
            raise
        fixed_location = location.replace(
            "https://s3.amazonaws.com:443/",
            "https://s3.amazonaws.com/",
            1,
        )
        redirected = Request(
            fixed_location,
            headers={"User-Agent": USER_AGENT, "Accept": "text/csv,*/*"},
        )
        return urlopen(redirected, timeout=timeout)


def copy_with_progress(response, destination) -> int:
    """Copy a download stream and log progress every 25 MB."""

    bytes_written = 0
    next_progress = 25 * 1024 * 1024
    while True:
        chunk = response.read(DOWNLOAD_CHUNK_SIZE)
        if not chunk:
            break
        destination.write(chunk)
        bytes_written += len(chunk)
        if bytes_written >= next_progress:
            LOGGER.info("Downloaded %.1f MB...", bytes_written / (1024 * 1024))
            next_progress += 25 * 1024 * 1024
    return bytes_written
