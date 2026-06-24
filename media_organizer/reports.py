from __future__ import annotations

import html
from pathlib import Path

from .models import DuplicateGroup, MediaItem, ScanManifest


def generate_duplicate_report(manifest: ScanManifest, output_dir: Path) -> Path:
    groups = manifest.exact_duplicates + manifest.similar_duplicates
    if not groups:
        body = "<p>No duplicates found.</p>"
    else:
        body = "\n".join(_duplicate_group(group) for group in groups)
    path = output_dir / "duplicates_report.html"
    path.write_text(_page("Duplicate Photo Report", body), encoding="utf-8")
    return path


def generate_locations_report(manifest: ScanManifest, output_dir: Path) -> tuple[Path, Path]:
    items = [item for item in manifest.items if item.gps_lat is not None and item.gps_lon is not None]
    csv_path = output_dir / "locations_report.csv"
    html_path = output_dir / "locations_report.html"
    csv_path.write_text(_locations_csv(items), encoding="utf-8-sig")
    html_path.write_text(_page("Local GPS Location Report", _locations_html(items)), encoding="utf-8")
    return csv_path, html_path


def _duplicate_group(group: DuplicateGroup) -> str:
    files = "".join(f"<li>{html.escape(path)}</li>" for path in group.files)
    return f"<section><h2>{html.escape(group.kind)} duplicate</h2><p>{html.escape(group.key)}</p><ul>{files}</ul></section>"


def _locations_csv(items: list[MediaItem]) -> str:
    rows = ["file,type,date,latitude,longitude,device,source_path"]
    for item in items:
        rows.append(
            ",".join(
                _csv_cell(value)
                for value in [
                    Path(item.source_path).name,
                    item.media_type,
                    item.date,
                    item.gps_lat,
                    item.gps_lon,
                    item.device,
                    item.source_path,
                ]
            )
        )
    return "\n".join(rows) + "\n"


def _locations_html(items: list[MediaItem]) -> str:
    if not items:
        return "<p>No local GPS metadata found.</p>"
    rows = []
    for item in items:
        rows.append(
            "<tr>"
            f"<td>{html.escape(Path(item.source_path).name)}</td>"
            f"<td>{html.escape(item.media_type)}</td>"
            f"<td>{html.escape(item.date)}</td>"
            f"<td>{item.gps_lat:.6f}</td>"
            f"<td>{item.gps_lon:.6f}</td>"
            f"<td>{html.escape(item.device)}</td>"
            f"<td>{html.escape(item.source_path)}</td>"
            "</tr>"
        )
    return (
        "<p>Only embedded local GPS metadata is shown. No network geocoding is used.</p>"
        "<table><thead><tr><th>File</th><th>Type</th><th>Date</th><th>Latitude</th>"
        "<th>Longitude</th><th>Device</th><th>Source</th></tr></thead><tbody>"
        + "\n".join(rows)
        + "</tbody></table>"
    )


def _csv_cell(value: object) -> str:
    text = "" if value is None else str(value)
    return '"' + text.replace('"', '""') + '"'


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body {{ margin: 0; font-family: Segoe UI, system-ui, sans-serif; background: #f6f7f9; color: #20242a; }}
    header {{ padding: 28px 36px; background: #ffffff; border-bottom: 1px solid #dfe3e8; }}
    main {{ padding: 24px 36px 48px; }}
    section {{ margin: 0 0 28px; }}
    h1, h2 {{ margin: 0 0 8px; }}
    p {{ margin: 0 0 14px; color: #5b6470; word-break: break-all; }}
    li {{ margin: 6px 0; word-break: break-all; }}
    table {{ border-collapse: collapse; width: 100%; background: #fff; }}
    th, td {{ border: 1px solid #dfe3e8; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f0f3f7; }}
    td {{ word-break: break-all; }}
  </style>
</head>
<body>
  <header><h1>{html.escape(title)}</h1></header>
  <main>{body}</main>
</body>
</html>
"""
