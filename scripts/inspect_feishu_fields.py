#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from feishu_bitable_ocr import (
    FeishuError,
    attachment_tokens,
    get_tenant_access_token,
    list_records,
    parse_feishu_url,
    resolve_app_token,
)
from feishu_material_enrich import cell_text


DEFAULT_FIELDS = [
    "图片",
    "AI提取文字",
    "AI标题",
    "AI内容总结",
    "AI推荐标签",
    "AI搜索关键词",
    "AI适用场景",
    "AI推荐话术",
    "AI检索文本",
    "AI分析状态",
    "图片类型",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect empty Feishu Bitable fields.")
    parser.add_argument("--url", required=True, help="Feishu wiki/base URL")
    parser.add_argument("--view-id", help="Optional view id. Omit to inspect all records.")
    parser.add_argument("--fields", nargs="*", default=DEFAULT_FIELDS, help="Field names to inspect.")
    return parser.parse_args()


def has_field_value(name: str, value: Any) -> bool:
    if name == "图片":
        return bool(attachment_tokens(value))
    return bool(cell_text(value))


def main() -> int:
    args = parse_args()
    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        raise FeishuError("Please set FEISHU_APP_ID and FEISHU_APP_SECRET")

    wiki_token, direct_app_token, table_id, url_view_id = parse_feishu_url(args.url)
    if not table_id:
        raise FeishuError("table id missing")

    view_id = args.view_id if args.view_id is not None else None
    tenant_token = get_tenant_access_token(app_id, app_secret)
    app_token = resolve_app_token(tenant_token, wiki_token, direct_app_token)
    records = list_records(tenant_token, app_token, table_id, view_id)

    print(f"records {len(records)}")
    for field_name in args.fields:
        empty = sum(
            1
            for record in records
            if not has_field_value(field_name, (record.get("fields") or {}).get(field_name))
        )
        print(f"{field_name} empty {empty}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
