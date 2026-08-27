#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from feishu_bitable_ocr import (
    FeishuError,
    attachment_tokens,
    download_attachment,
    get_tenant_access_token,
    list_fields,
    list_records,
    parse_feishu_url,
    record_id,
    resolve_app_token,
)
from feishu_material_enrich import make_pitch as build_pitch


IMAGE_FIELD = "图片"
OUTPUT_DIR = Path("web/assets")
IMAGE_TYPE_RULES = [
    ("竞品对比", ["竞品", "其他机构", "辅导班", "线下班", "补习班", "对比"]),
    ("平板", ["平板", "pad", "iPad", "学习机"]),
    ("成交确认", ["客户转账", "发起收款", "付款", "报名"]),
    ("学生没钱/跟家长沟通", ["学生没钱", "家长沟通", "跟家长沟通", "没钱"]),
    ("异议", ["太贵", "价格", "负担", "考虑", "犹豫", "不需要"]),
    ("教育理念/老师推荐图", ["老师推荐", "教育理念", "学习方法", "规划", "建议"]),
    ("报过辅导班对比图", ["辅导班", "补课", "线下", "一对一"]),
    ("好评", ["谢谢", "感谢", "认可", "物超所值", "进步", "提升", "推荐"]),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export Feishu Bitable materials for the local web demo.")
    parser.add_argument("--url", required=True, help="Feishu wiki/base URL")
    parser.add_argument("--view-id", help="Optional view id. Defaults to URL view= query.")
    parser.add_argument("--ignore-view", action="store_true", help="Export all table records instead of the URL view.")
    parser.add_argument("--image-field", default=IMAGE_FIELD, help="Image attachment field name")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR), help="Web assets output directory")
    parser.add_argument("--limit", type=int, default=0, help="Max records to export. 0 means all records.")
    parser.add_argument("--max-images-per-record", type=int, default=0, help="Max images per record. 0 means all images.")
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue exporting other records when one image download fails.",
    )
    parser.add_argument(
        "--reuse-existing-images",
        action="store_true",
        help="Reuse already exported material-NNN images instead of downloading them again.",
    )
    return parser.parse_args()


def cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("name") or ""))
            else:
                parts.append(str(item))
        return "\n".join(part.strip() for part in parts if part and part.strip())
    return str(value).strip()


def infer_image_type(fields: Dict[str, Any]) -> str:
    direct = cell_text(fields.get("图片类型"))
    if direct:
        return direct
    text = "\n".join(
        [
            cell_text(fields.get("人工标签")),
            cell_text(fields.get("AI对话类型")),
            cell_text(fields.get("AI推荐标签")),
            cell_text(fields.get("AI搜索关键词")),
            cell_text(fields.get("AI检索文本")),
        ]
    )
    for label, words in IMAGE_TYPE_RULES:
        if any(word in text for word in words):
            return label
    return "其他"


def split_terms(value: str) -> List[str]:
    return [part.strip() for part in value.replace("/", "、").split("、") if part.strip()]


def first_evidence(fields: Dict[str, Any]) -> str:
    for line in cell_text(fields.get("AI证据句")).splitlines():
        line = line.strip()
        if (
            line
            and line != "未提取到明确证据句"
            and len(line) >= 6
            and re.search(r"[\u4e00-\u9fff]", line)
        ):
            return line
    return ""


def customer_label_text(value: str) -> str:
    text = re.sub(r"\s+", "", value or "")
    text = re.sub(r"[\u202a-\u202e\u2066-\u2069]", "", text)
    text = text.replace("：", "，").replace(":", "，")
    text = re.sub(r"(红线处|红圈处|黄色框里|图片中|截图中|标记处|重点是|提到|强调|显示)", "", text)
    text = text.strip(" ，。！？!；;：:")
    return text[:72]


def is_internal_label(value: str) -> bool:
    return any(word in value for word in ["红线", "红圈", "黄色框", "标记", "图片", "截图", "位置"])


def is_internal_pitch(value: str) -> bool:
    markers = [
        "这张图适合",
        "这个案例适合",
        "这类素材适合",
        "素材适合",
        "素材主要体现",
        "适合在需要",
        "适合给",
        "您看这个反馈，家长核心感受是",
        "您看这个反馈，家长的核心感受是",
        "这张图我会先让家长看结论",
        "这张截图的价值点在于",
        "这个素材适合",
        "我一般会用这条说明实际变化",
        "这条可以作为证明素材",
        "这不是空泛夸课程",
        "这条不用解释太多",
        "每个孩子卡住的地方不一样",
        "先把问题看清楚",
        "真正补上",
        "课程最终还是要落到孩子身上",
        "这个先给您做参考",
        "这类结果更适合提醒",
        "这类反馈可以先做参考",
    ]
    return any(marker in value for marker in markers)


def fallback_pitch(fields: Dict[str, Any], image_type: str) -> str:
    existing = cell_text(fields.get("AI推荐话术"))
    manual = cell_text(fields.get("人工标签"))
    manual_label = customer_label_text(manual)
    if existing and not manual_label and not is_internal_pitch(existing):
        return existing
    text = "\n".join(
        [
            manual,
            cell_text(fields.get("补充说明")),
            cell_text(fields.get("AI重点文字")),
            cell_text(fields.get("AI证据句")),
            cell_text(fields.get("AI提取文字")),
        ]
    )
    tags = split_terms(cell_text(fields.get("AI推荐标签")))
    scenarios = split_terms(cell_text(fields.get("AI适用场景")) or cell_text(fields.get("AI试用场景")))
    evidence = [
        line.strip()
        for line in cell_text(fields.get("AI证据句")).splitlines()
        if line.strip() and line.strip() != "未提取到明确证据句"
    ]
    return build_pitch(
        text,
        tags,
        scenarios,
        evidence or ([first_evidence(fields)] if first_evidence(fields) else []),
        image_type,
        manual,
    )


def existing_exported_image(output_dir: Path, material_index: int, image_index: int) -> Optional[Path]:
    pattern = f"material-{material_index:03d}-{image_index}.*"
    matches = sorted(output_dir.glob(pattern))
    return matches[0] if matches else None


def previous_images_by_record(output_dir: Path) -> Dict[str, List[str]]:
    materials_path = output_dir / "materials.json"
    if not materials_path.exists():
        return {}
    try:
        previous_materials = json.loads(materials_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    result: Dict[str, List[str]] = {}
    for material in previous_materials:
        rec_id = str(material.get("id") or "")
        images = [str(image) for image in material.get("images") or [] if image]
        if rec_id and images:
            result[rec_id] = images
    return result


def reusable_record_image(output_dir: Path, existing_images: List[str], image_index: int) -> Optional[str]:
    if image_index > len(existing_images):
        return None
    image = existing_images[image_index - 1]
    relative = image[7:] if image.startswith("assets/") else image
    if (output_dir / relative).exists():
        return image
    return None


def main() -> int:
    args = parse_args()
    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        raise FeishuError("Please set FEISHU_APP_ID and FEISHU_APP_SECRET")

    wiki_token, direct_app_token, table_id, url_view_id = parse_feishu_url(args.url)
    view_id = None if args.ignore_view else (args.view_id or url_view_id)
    if not table_id:
        raise FeishuError("table id missing")

    token = get_tenant_access_token(app_id, app_secret)
    app_token = resolve_app_token(token, wiki_token, direct_app_token)
    fields_by_name = list_fields(token, app_token, table_id)
    image_field = fields_by_name[args.image_field]
    image_field_id = image_field["field_id"]
    records = list_records(token, app_token, table_id, view_id)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    previous_images = previous_images_by_record(output_dir) if args.reuse_existing_images else {}
    materials: List[Dict[str, Any]] = []
    failures: List[Dict[str, str]] = []

    with tempfile.TemporaryDirectory(prefix="feishu-demo-assets-") as tmp:
        temp_dir = Path(tmp)
        for record in records:
            if args.limit and len(materials) >= args.limit:
                break

            fields = record.get("fields") or {}
            attachments = attachment_tokens(fields.get(args.image_field))
            if not attachments:
                continue

            rec_id = record_id(record)
            exported_images = []
            selected_attachments = attachments
            if args.max_images_per_record:
                selected_attachments = attachments[: args.max_images_per_record]
            for image_index, attachment in enumerate(selected_attachments, start=1):
                material_index = len(materials) + 1
                existing_image = reusable_record_image(
                    output_dir,
                    previous_images.get(rec_id, []),
                    image_index,
                )
                if existing_image:
                    exported_images.append(existing_image)
                    continue

                try:
                    downloaded = download_attachment(
                        token,
                        attachment["file_token"],
                        attachment.get("name") or f"image_{image_index}.png",
                        table_id,
                        image_field_id,
                        rec_id,
                        temp_dir,
                    )
                    suffix = downloaded.suffix.lower() if downloaded.suffix else ".png"
                    target_name = f"material-{material_index:03d}-{image_index}{suffix}"
                    target_path = output_dir / target_name
                    shutil.copyfile(downloaded, target_path)
                    exported_images.append(f"assets/{target_name}")
                    time.sleep(0.05)
                except Exception as exc:
                    failures.append(
                        {
                            "record_id": rec_id,
                            "file_token": str(attachment.get("file_token") or ""),
                            "image_index": str(image_index),
                            "error": str(exc),
                        }
                    )
                    print(
                        f"WARN failed image record={rec_id} index={image_index}: {exc}",
                        file=sys.stderr,
                        flush=True,
                    )
                    if not args.continue_on_error:
                        raise

            if not exported_images:
                print(f"SKIP {rec_id}: no images exported", flush=True)
                continue

            image_type = infer_image_type(fields)
            pitch = fallback_pitch(fields, image_type)
            scenario = cell_text(fields.get("AI适用场景")) or cell_text(fields.get("AI试用场景"))
            ai_tags = split_terms(cell_text(fields.get("AI推荐标签")))[:6]
            keywords = split_terms(cell_text(fields.get("AI搜索关键词")))[:10]

            materials.append(
                {
                    "id": rec_id,
                    "images": exported_images,
                    "title": cell_text(fields.get("AI标题")) or "微信对话素材",
                    "manualTag": cell_text(fields.get("人工标签"))
                    or cell_text(fields.get("AI证据句"))
                    or cell_text(fields.get("AI内容总结"))[:80],
                    "stage": cell_text(fields.get("所属学段")),
                    "grade": cell_text(fields.get("具体年级")) or "",
                    "imageType": image_type,
                    "aiTags": ai_tags,
                    "keywords": keywords,
                    "scenario": scenario,
                    "evidence": cell_text(fields.get("AI证据句")),
                    "focus": cell_text(fields.get("AI重点文字")),
                    "summary": cell_text(fields.get("AI内容总结")),
                    "pitch": pitch,
                    "status": cell_text(fields.get("AI分析状态"))
                    or ("已完成" if cell_text(fields.get("AI标题")) else "待分析"),
                    "searchText": "\n".join(
                        [
                            cell_text(fields.get("人工标签")),
                            image_type,
                            cell_text(fields.get("AI重点文字")),
                            cell_text(fields.get("AI标题")),
                            pitch,
                            cell_text(fields.get("AI推荐标签")),
                            cell_text(fields.get("AI搜索关键词")),
                            scenario,
                            cell_text(fields.get("AI证据句")),
                            cell_text(fields.get("AI检索文本")),
                        ]
                    ),
                }
            )
            print(f"EXPORTED {len(materials)}/{len(records)} {rec_id}: {len(exported_images)} image(s)", flush=True)

    (output_dir / "materials.json").write_text(
        json.dumps(materials, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "export_failures.json").write_text(
        json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    image_count = sum(len(material["images"]) for material in materials)
    print(
        f"Exported {len(materials)} materials and {image_count} images to {output_dir}. "
        f"Failed images: {len(failures)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
