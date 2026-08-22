#!/usr/bin/env python3
"""
Read images from a Feishu/Lark Bitable attachment column, OCR them locally with
macOS Vision, and write the extracted text back to another column.

Required environment variables:
  FEISHU_APP_ID
  FEISHU_APP_SECRET
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


API_BASE = "https://open.feishu.cn/open-apis"


class FeishuError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OCR images in a Feishu Bitable attachment column and write text back."
    )
    parser.add_argument("--url", required=True, help="Feishu wiki/base URL")
    parser.add_argument("--table-id", help="Bitable table id. Defaults to URL table= query.")
    parser.add_argument("--view-id", help="Optional view id. Defaults to URL view= query.")
    parser.add_argument("--image-field", default="图片", help="Attachment/image field name")
    parser.add_argument("--target-field", default="AI提取文字", help="Field name to write OCR text")
    parser.add_argument("--limit", type=int, default=10, help="Number of records to process")
    parser.add_argument(
        "--skip-filled",
        action="store_true",
        help="Skip records whose target field already has a value.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Download and OCR only; do not write results back to Feishu.",
    )
    parser.add_argument(
        "--ocr-helper",
        default=str(Path(__file__).with_name("vision_ocr.swift")),
        help="Path to the Swift macOS Vision OCR helper.",
    )
    return parser.parse_args()


def http_json(
    method: str,
    path: str,
    token: Optional[str] = None,
    body: Optional[Dict[str, Any]] = None,
    query: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    url = API_BASE + path
    if query:
        encoded = urllib.parse.urlencode(query, doseq=True)
        url += "?" + encoded

    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise FeishuError(f"{method} {path} failed: HTTP {exc.code}: {details}") from exc
    except urllib.error.URLError as exc:
        raise FeishuError(f"{method} {path} failed: {exc}") from exc

    try:
        result = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise FeishuError(f"{method} {path} returned non-JSON response: {payload[:300]}") from exc

    if result.get("code") not in (0, None):
        raise FeishuError(
            f"{method} {path} failed: code={result.get('code')} msg={result.get('msg')}"
        )
    return result


def get_tenant_access_token(app_id: str, app_secret: str) -> str:
    result = http_json(
        "POST",
        "/auth/v3/tenant_access_token/internal",
        body={"app_id": app_id, "app_secret": app_secret},
    )
    token = result.get("tenant_access_token")
    if not token:
        raise FeishuError("tenant_access_token missing from auth response")
    return token


def parse_feishu_url(url: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    table_id = query.get("table", [None])[0]
    view_id = query.get("view", [None])[0]

    path_parts = [part for part in parsed.path.split("/") if part]
    wiki_token = None
    app_token = None

    if "wiki" in path_parts:
        index = path_parts.index("wiki")
        if len(path_parts) > index + 1:
            wiki_token = path_parts[index + 1]
    elif "base" in path_parts:
        index = path_parts.index("base")
        if len(path_parts) > index + 1:
            app_token = path_parts[index + 1]

    return wiki_token, app_token, table_id, view_id


def resolve_app_token(token: str, wiki_token: Optional[str], app_token: Optional[str]) -> str:
    if app_token:
        return app_token
    if not wiki_token:
        raise FeishuError("Cannot find wiki token or base app_token in URL")

    result = http_json("GET", "/wiki/v2/spaces/get_node", token=token, query={"token": wiki_token})
    data = result.get("data") or {}
    node = data.get("node") or data
    obj_type = node.get("obj_type")
    obj_token = node.get("obj_token")

    if not obj_token:
        raise FeishuError(f"Cannot resolve wiki node to obj_token. Response data: {data}")
    if obj_type and obj_type != "bitable":
        raise FeishuError(f"Wiki node is {obj_type}, not bitable")
    return obj_token


def paginate_items(
    path: str,
    token: str,
    base_query: Optional[Dict[str, Any]] = None,
    page_size: int = 100,
) -> Iterable[Dict[str, Any]]:
    page_token = None
    while True:
        query = dict(base_query or {})
        query["page_size"] = page_size
        if page_token:
            query["page_token"] = page_token

        result = http_json("GET", path, token=token, query=query)
        data = result.get("data") or {}
        for item in data.get("items") or []:
            yield item

        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
        if not page_token:
            break


def list_fields(token: str, app_token: str, table_id: str) -> Dict[str, Dict[str, Any]]:
    path = f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
    fields = list(paginate_items(path, token))
    return {field.get("field_name"): field for field in fields if field.get("field_name")}


def list_records(
    token: str,
    app_token: str,
    table_id: str,
    view_id: Optional[str],
    page_size: int = 100,
) -> List[Dict[str, Any]]:
    path = f"/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    query: Dict[str, Any] = {}
    if view_id:
        query["view_id"] = view_id
    return list(paginate_items(path, token, query, page_size=page_size))


def record_id(record: Dict[str, Any]) -> str:
    value = record.get("record_id") or record.get("id")
    if not value:
        raise FeishuError(f"Record id missing: {record}")
    return value


def attachment_tokens(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    attachments = []
    for item in value:
        if isinstance(item, dict) and item.get("file_token"):
            attachments.append(item)
    return attachments


def download_attachment(
    token: str,
    file_token: str,
    file_name: str,
    table_id: str,
    field_id: str,
    rec_id: str,
    output_dir: Path,
) -> Path:
    extra = {
        "bitablePerm": {
            "tableId": table_id,
            "attachments": {field_id: {rec_id: [file_token]}},
        }
    }
    query = urllib.parse.urlencode(
        {"extra": json.dumps(extra, ensure_ascii=False, separators=(",", ":"))}
    )
    url = f"{API_BASE}/drive/v1/medias/{file_token}/download?{query}"
    headers = {"Authorization": f"Bearer {token}"}
    request = urllib.request.Request(url, headers=headers, method="GET")

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            content_type = response.headers.get("Content-Type", "")
            content = response.read()
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise FeishuError(f"Download failed for {file_token}: HTTP {exc.code}: {details}") from exc
    except urllib.error.URLError as exc:
        raise FeishuError(f"Download failed for {file_token}: {exc}") from exc

    suffix = Path(file_name or "").suffix
    if not suffix:
        suffix = mimetypes.guess_extension(content_type.split(";")[0].strip()) or ".img"

    safe_name = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in file_name or file_token)
    output_path = output_dir / f"{file_token}_{safe_name}"
    if not output_path.suffix:
        output_path = output_path.with_suffix(suffix)
    output_path.write_bytes(content)
    return output_path


def ocr_image(helper_path: str, image_path: Path) -> str:
    env = os.environ.copy()
    env.setdefault("CLANG_MODULE_CACHE_PATH", "/tmp/clang-module-cache")
    completed = subprocess.run(
        ["swift", helper_path, str(image_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    if completed.returncode != 0:
        raise FeishuError(completed.stderr.strip() or f"OCR failed for {image_path}")
    return completed.stdout.strip()


def update_record(
    token: str,
    app_token: str,
    table_id: str,
    rec_id: str,
    target_field: str,
    text: str,
) -> None:
    path = f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{rec_id}"
    http_json("PUT", path, token=token, body={"fields": {target_field: text}})


def has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return len(value) > 0
    return True


def main() -> int:
    args = parse_args()

    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        raise FeishuError("Please set FEISHU_APP_ID and FEISHU_APP_SECRET environment variables")

    wiki_token, direct_app_token, url_table_id, url_view_id = parse_feishu_url(args.url)
    table_id = args.table_id or url_table_id
    view_id = args.view_id or url_view_id
    if not table_id:
        raise FeishuError("Table id missing. Pass --table-id or use a URL with table=...")

    tenant_token = get_tenant_access_token(app_id, app_secret)
    app_token = resolve_app_token(tenant_token, wiki_token, direct_app_token)

    fields_by_name = list_fields(tenant_token, app_token, table_id)
    image_field = fields_by_name.get(args.image_field)
    target_field = fields_by_name.get(args.target_field)
    if not image_field:
        raise FeishuError(f"Cannot find image field: {args.image_field}")
    if not target_field:
        raise FeishuError(f"Cannot find target field: {args.target_field}")

    image_field_id = image_field.get("field_id")
    if not image_field_id:
        raise FeishuError(f"Image field id missing for field: {args.image_field}")

    records = list_records(tenant_token, app_token, table_id, view_id)
    print(f"Loaded {len(records)} records from table {table_id}.")

    processed = 0
    with tempfile.TemporaryDirectory(prefix="feishu-bitable-ocr-") as tmp:
        output_dir = Path(tmp)
        for record in records:
            if processed >= args.limit:
                break

            fields = record.get("fields") or {}
            rec_id = record_id(record)
            if args.skip_filled and has_value(fields.get(args.target_field)):
                print(f"SKIP {rec_id}: target field already has value")
                continue

            attachments = attachment_tokens(fields.get(args.image_field))
            if not attachments:
                print(f"SKIP {rec_id}: no image attachments")
                continue

            texts = []
            for index, attachment in enumerate(attachments, start=1):
                file_token = attachment["file_token"]
                name = attachment.get("name") or f"image_{index}"
                image_path = download_attachment(
                    tenant_token,
                    file_token,
                    name,
                    table_id,
                    image_field_id,
                    rec_id,
                    output_dir,
                )
                text = ocr_image(args.ocr_helper, image_path)
                if text:
                    texts.append(text)

            combined_text = "\n\n---\n\n".join(texts).strip()
            if not combined_text:
                combined_text = "未识别到文字"

            if args.dry_run:
                print(f"DRY-RUN {rec_id}:")
                print(combined_text[:500] + ("..." if len(combined_text) > 500 else ""))
            else:
                update_record(tenant_token, app_token, table_id, rec_id, args.target_field, combined_text)
                print(f"UPDATED {rec_id}: {len(combined_text)} chars")

            processed += 1
            time.sleep(0.2)

    print(f"Done. Processed {processed} record(s).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FeishuError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
