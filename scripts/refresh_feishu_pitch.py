#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any, Dict, List

from feishu_bitable_ocr import (
    FeishuError,
    get_tenant_access_token,
    list_records,
    parse_feishu_url,
    record_id,
    resolve_app_token,
)
from feishu_material_enrich import cell_text, make_pitch, update_record_fields


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh only AI推荐话术 in Feishu Bitable records.")
    parser.add_argument("--url", required=True, help="Feishu wiki/base URL")
    parser.add_argument("--view-id", help="Optional view id. Defaults to URL view= query.")
    parser.add_argument("--ignore-view", action="store_true", help="Refresh all table records instead of the URL view.")
    parser.add_argument("--limit", type=int, default=0, help="Max records to update. 0 means all records.")
    parser.add_argument("--pitch-field", default="AI推荐话术", help="Pitch field name")
    parser.add_argument("--manual-field", default="人工标签", help="Manual tag field name")
    parser.add_argument("--note-field", default="补充说明", help="Human note field name")
    parser.add_argument("--image-type-field", default="图片类型", help="Image type field name")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing Feishu")
    parser.add_argument("--continue-on-error", action="store_true", help="Continue when one record update fails")
    return parser.parse_args()


def split_terms(value: str) -> List[str]:
    return [part.strip() for part in value.replace("/", "、").replace(",", "、").split("、") if part.strip()]


def evidence_lines(fields: Dict[str, Any]) -> List[str]:
    return [
        line.strip()
        for line in cell_text(fields.get("AI证据句")).splitlines()
        if line.strip() and line.strip() != "未提取到明确证据句"
    ]


def source_text(fields: Dict[str, Any], manual_field: str, note_field: str) -> str:
    return "\n".join(
        part
        for part in [
            cell_text(fields.get(manual_field)),
            cell_text(fields.get(note_field)),
            cell_text(fields.get("AI重点文字")),
            cell_text(fields.get("AI证据句")),
            cell_text(fields.get("AI提取文字")),
        ]
        if part
    )


def main() -> int:
    args = parse_args()
    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        raise FeishuError("Please set FEISHU_APP_ID and FEISHU_APP_SECRET")

    wiki_token, direct_app_token, table_id, url_view_id = parse_feishu_url(args.url)
    if not table_id:
        raise FeishuError("table id missing")
    view_id = None if args.ignore_view else (args.view_id or url_view_id)

    tenant_token = get_tenant_access_token(app_id, app_secret)
    app_token = resolve_app_token(tenant_token, wiki_token, direct_app_token)
    records = list_records(tenant_token, app_token, table_id, view_id)
    print(f"Loaded {len(records)} records from table {table_id}.")

    updated = 0
    failed = 0
    for record in records:
        if args.limit and updated >= args.limit:
            break
        fields = record.get("fields") or {}
        rec_id = record_id(record)
        manual = cell_text(fields.get(args.manual_field))
        tags = split_terms(cell_text(fields.get("AI推荐标签")))
        scenarios = split_terms(cell_text(fields.get("AI适用场景")) or cell_text(fields.get("AI试用场景")))
        image_type = cell_text(fields.get(args.image_type_field)) or "其他"
        pitch = make_pitch(
            source_text(fields, args.manual_field, args.note_field),
            tags,
            scenarios,
            evidence_lines(fields),
            image_type,
            manual,
        )
        old_pitch = cell_text(fields.get(args.pitch_field))
        if old_pitch == pitch:
            continue

        try:
            if args.dry_run:
                print(f"DRY-RUN {rec_id}: {pitch[:260]}")
            else:
                update_record_fields(tenant_token, app_token, table_id, rec_id, {args.pitch_field: pitch})
                print(f"UPDATED {rec_id}: {pitch[:80]}")
                time.sleep(0.12)
            updated += 1
        except Exception as exc:
            failed += 1
            if not args.continue_on_error:
                raise
            print(f"ERROR {rec_id}: {exc}", file=sys.stderr)

    print(f"Done. Updated {updated} record(s). Failed {failed} record(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
