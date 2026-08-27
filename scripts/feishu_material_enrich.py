#!/usr/bin/env python3
"""
Build searchable material-card fields for Feishu Bitable image records.

This script reuses the local OCR pipeline from feishu_bitable_ocr.py, creates
missing text fields, and writes structured search metadata back to Bitable.

Required environment variables:
  FEISHU_APP_ID
  FEISHU_APP_SECRET

Optional:
  OPENAI_API_KEY  If set, this script can be extended to call a model. The
                  default implementation below is local/rule-based so the
                  Feishu writeback flow can be tested without another service.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
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
    has_value,
    http_json,
    list_fields,
    list_records,
    ocr_image,
    parse_feishu_url,
    record_id,
    resolve_app_token,
    update_record,
)


OCR_FIELD = "AI提取文字"
MATERIAL_FIELDS = [
    "AI重点文字",
    "AI标记说明",
    "AI标题",
    "AI内容总结",
    "AI对话类型",
    "AI情绪倾向",
    "AI推荐标签",
    "AI搜索关键词",
    "AI适用场景",
    "AI证据句",
    "AI素材价值",
    "AI推荐话术",
    "AI检索文本",
]


TAG_RULES = [
    ("成绩提升", ["提升", "进步", "提高", "提分", "高了", "涨了", "考得怎么样"]),
    ("成绩超预期", ["超预期", "超出", "没敢想", "比预估高", "没想到"]),
    ("中考成绩", ["中考", "查分", "重高", "高中提前录取", "考上"]),
    ("期中考试", ["期中考", "期中考试", "期中成绩"]),
    ("家长好评", ["谢谢老师", "感谢", "认可", "口碑", "推荐给大家", "推荐给朋友"]),
    ("课程认可", ["课程确实", "课程比较好", "洋葱确实是好", "物超所值", "实力不可埋没"]),
    ("物超所值", ["物超所值", "值", "5000块", "五千"]),
    ("转介绍意向", ["推荐给朋友", "身边朋友", "多推荐", "班级群", "推荐给别人"]),
    ("孩子主动学习", ["自觉", "自律", "每天都", "主动", "爱学习", "学习习惯"]),
    ("查漏补缺", ["补漏", "不会的", "复习", "补基础", "知识点"]),
    ("提前学习", ["预科", "衔接课", "初二物理", "初三化学", "提前"]),
    ("排名进步", ["排名", "全校第一", "年级第一", "班级第", "考场"]),
    ("成交确认", ["客户转账", "发起收款", "付款", "报名"]),
    ("售后反馈", ["售后", "资料", "练习", "补基础"]),
]

TYPE_RULES = [
    ("成绩反馈", ["成绩", "分数", "查分", "排名", "考场", "录取", "考上"]),
    ("期中考试反馈", ["期中考", "期中考试", "期中成绩"]),
    ("好评反馈", ["谢谢", "感谢", "认可", "口碑", "物超所值", "推荐"]),
    ("效果反馈", ["提升", "进步", "提分", "听得懂", "效果"]),
    ("转介绍", ["推荐给朋友", "推荐给别人", "班级群", "多推荐"]),
    ("课程咨询", ["有没有", "资料", "练习", "补基础"]),
    ("成交确认", ["客户转账", "发起收款", "付款"]),
]

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

GRADE_WORDS = [
    "一年级",
    "二年级",
    "三年级",
    "四年级",
    "五年级",
    "六年级",
    "初一",
    "初二",
    "初三",
    "高一",
    "高二",
    "高三",
    "小一",
    "小二",
    "小三",
    "小四",
    "小五",
    "小六",
]

SCENARIO_RULES = [
    ("客户担心学习效果时", ["提升", "进步", "提分", "成绩", "效果", "听得懂"]),
    ("需要展示真实成绩反馈时", ["成绩", "分数", "排名", "查分", "录取"]),
    ("需要展示期中阶段性反馈时", ["期中考", "期中考试", "期中成绩"]),
    ("处理价格异议时", ["物超所值", "5000块", "值"]),
    ("增强客户信任时", ["谢谢", "感谢", "认可", "口碑", "推荐"]),
    ("促进转介绍或老带新时", ["推荐给朋友", "身边朋友", "班级群", "推荐给别人"]),
    ("证明孩子学习习惯变好时", ["自觉", "自律", "每天都", "主动", "爱学习"]),
    ("说明查漏补缺有效时", ["补漏", "不会的", "补基础", "知识点"]),
    ("引导续费或长期学习时", ["三年", "高中三年", "继续", "衔接课"]),
]

COMPETITOR_WORDS = ["科大讯飞", "作业帮", "猿辅导", "学而思", "高途", "步步高", "辅导班", "一对一", "学习机", "平板"]

PRODUCT_EXPLAINER_CUES = [
    "课程",
    "课堂",
    "APP",
    "洋葱学园",
    "葱学",
    "每学园",
    "人机交互",
    "互动",
    "精品短动画",
    "短动画",
    "场景化",
    "学习不费力",
    "找图形的规律",
    "重复出现",
    "分析与解答",
    "知识点清单",
    "知识点",
    "知识讲解",
    "典例剖析",
    "板书",
    "模块",
    "大课",
    "课程已升级",
    "漯程已升级",
    "同步课",
    "视频",
    "颗粒度",
    "可视化",
    "画面",
    "大量刷题",
    "口诀",
    "套路",
    "死记硬背",
    "讲得更清",
    "讲得更清楚",
    "互动性",
    "趣味性",
    "题目类型",
    "方法总结",
    "解题",
    "例题",
    "题型",
    "达标检测",
    "课前预习",
    "函数",
    "图形",
    "物理",
    "化学",
    "数学",
    "观察",
    "猜想",
    "验证",
]

PRODUCT_EXPLAINER_STRONG_CUES = [
    "同一个知识点",
    "洋葱讲得更清",
    "洋葱讲得更清楚",
    "独立教研体系",
    "人机交互课程",
    "课程颗粒度",
    "直播大课",
    "只分两个大模块",
    "直接给学生输出口诀",
    "大量刷题",
    "知识点清单",
]

COMPETITOR_FEEDBACK_CUES = [
    "谢谢老师",
    "感谢",
    "我相信",
    "后悔",
    "没用",
    "没啥用",
    "白买",
    "买了",
    "卖了",
    "下载QQ",
    "会员号",
    "付款",
    "报名",
    "转账",
    "推荐给",
    "成绩",
    "排名",
    "考上",
    "提升",
    "进步",
    "物超所值",
]

STOP_WORDS = {
    "老师",
    "孩子",
    "洋葱",
    "课程",
    "学习",
    "这个",
    "现在",
    "之前",
    "可以",
    "真的",
    "就是",
}

NOISE_LINE_PATTERNS = [
    re.compile(r"^\d{1,2}[:：]\d{2}$"),
    re.compile(r"^[\d\s:：./\\-]{1,10}$"),
    re.compile(r"^[@＠]?\s*微信$"),
    re.compile(r"对方默认同意存档会话内容"),
    re.compile(r"(水印|小红书号|豆包AI生成|复制图片|聊天信息|加载中)"),
    re.compile(r"^(返回|更多|发送|按住说话|相册|拍摄|文件|位置|红包|转账)$"),
    re.compile(r"^(我|你|他|她|它|嗯|啊|哦|好|可以)$"),
]
INTERNAL_LABEL_WORDS = ["红线", "红圈", "黄色框", "标记", "图片", "截图", "提到", "强调", "位置"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create and write searchable AI material fields for Feishu image records."
    )
    parser.add_argument("--url", required=True, help="Feishu wiki/base URL")
    parser.add_argument("--table-id", help="Bitable table id. Defaults to URL table= query.")
    parser.add_argument("--view-id", help="Optional view id. Defaults to URL view= query.")
    parser.add_argument("--ignore-view", action="store_true", help="Read all table records instead of the URL view.")
    parser.add_argument("--image-field", default="图片", help="Attachment/image field name")
    parser.add_argument("--manual-field", default="人工标签", help="Manual tag field name")
    parser.add_argument("--note-field", default="补充说明", help="Optional human note field name")
    parser.add_argument("--manual-tag", help="Only process records whose manual tag matches this text")
    parser.add_argument("--ocr-field", default=OCR_FIELD, help="OCR text field name")
    parser.add_argument("--status-field", default="AI分析状态", help="Analysis status field name")
    parser.add_argument("--image-type-field", default="图片类型", help="Image type field name")
    parser.add_argument(
        "--status-values",
        default="",
        help="Only process records whose analysis status is in this comma-separated list",
    )
    parser.add_argument("--limit", type=int, default=10, help="Number of records to process")
    parser.add_argument(
        "--skip-filled",
        action="store_true",
        help="Skip records whose AI标题 already has a value.",
    )
    parser.add_argument(
        "--only-missing",
        action="store_true",
        help="Only write fields that are currently empty.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Log per-record errors and continue processing the next record.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not create fields or write records")
    parser.add_argument("--quiet-skips", action="store_true", help="Do not print skipped records")
    parser.add_argument(
        "--ocr-helper",
        default=str(Path(__file__).with_name("vision_ocr.swift")),
        help="Path to the Swift macOS Vision OCR helper.",
    )
    parser.add_argument(
        "--focus-helper",
        default=str(Path(__file__).with_name("marked_focus_ocr.swift")),
        help="Path to the Swift red-marked-region OCR helper.",
    )
    return parser.parse_args()


def log_skip(args: argparse.Namespace, message: str) -> None:
    if not args.quiet_skips:
        print(message)


def create_text_field(token: str, app_token: str, table_id: str, field_name: str) -> None:
    path = f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
    http_json("POST", path, token=token, body={"field_name": field_name, "type": 1})


def ensure_fields(
    token: str,
    app_token: str,
    table_id: str,
    field_names: List[str],
    dry_run: bool,
) -> Dict[str, Dict[str, Any]]:
    fields_by_name = list_fields(token, app_token, table_id)
    missing = [name for name in field_names if name not in fields_by_name]
    if missing and dry_run:
        print(f"DRY-RUN would create fields: {', '.join(missing)}")
        return fields_by_name

    for name in missing:
        create_text_field(token, app_token, table_id, name)
        print(f"CREATED FIELD {name}")
        time.sleep(0.8)

    if missing:
        fields_by_name = list_fields(token, app_token, table_id)
    return fields_by_name


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


def clean_ocr_text(text: str) -> str:
    lines = []
    seen = set()
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        if is_noise_line(line):
            continue
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return "\n".join(lines)


def is_noise_line(line: str) -> bool:
    text = line.strip()
    if not text:
        return True
    if len(text) <= 2 and not re.search(r"[\u4e00-\u9fff]{2}", text):
        return True
    return any(pattern.search(text) for pattern in NOISE_LINE_PATTERNS)


def customer_label_text(value: str) -> str:
    text = clean_ocr_text(value).replace("\n", "，")
    text = re.sub(r"[\u202a-\u202e\u2066-\u2069]", "", text)
    text = text.replace("：", "，").replace(":", "，")
    text = re.sub(r"(红线处|红圈处|黄色框里|图片中|截图中|标记处|重点是|提到|强调|显示)", "", text)
    text = re.sub(r"\s+", "", text).strip(" ，。！？!；;：:")
    text = text.replace("用户反应", "家长反馈").replace("用户反馈", "家长反馈")
    return text[:72]


def is_internal_label(value: str) -> bool:
    return any(word in value for word in INTERNAL_LABEL_WORDS)


def contains_any(text: str, words: List[str]) -> bool:
    return any(contains_rule_word(text, word) for word in words)


def contains_rule_word(text: str, word: str) -> bool:
    if word == "中考":
        return re.search(r"(^|[^期])中考", text) is not None
    if word == "期中考":
        return "期中考" in text or "期中考试" in text
    return word in text


def pick_by_rules(text: str, rules: List[tuple[str, List[str]]], limit: int) -> List[str]:
    matched = [label for label, words in rules if contains_any(text, words)]
    return matched[:limit]


def pick_evidence(text: str, focus_text: str = "") -> List[str]:
    evidence_keywords = [
        "谢谢",
        "感谢",
        "物超所值",
        "推荐",
        "提升",
        "进步",
        "考上",
        "录取",
        "全校第一",
        "年级第一",
        "比预估高",
        "没敢想",
        "每天",
        "自觉",
    ]
    focus_lines = [line.strip() for line in focus_text.splitlines() if line.strip() and line.strip() != "---"]
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    scored = []
    for line in focus_lines + lines:
        if is_noise_line(line) or len(line) < 6 or not re.search(r"[\u4e00-\u9fff]", line):
            continue
        score = sum(1 for keyword in evidence_keywords if keyword in line)
        if line in focus_lines:
            score += 3
        if 8 <= len(line) <= 42:
            score += 1
        if score:
            scored.append((score, len(line), line))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [line for _, _, line in scored[:3]]


def extract_keywords(text: str, tags: List[str], evidence: List[str]) -> List[str]:
    keywords = list(tags)
    keyword_patterns = [
        r"(?<!期)中考",
        r"期中考(?:试)?",
        r"期中成绩",
        r"成绩",
        r"提分",
        r"进步",
        r"提升",
        r"排名",
        r"全校第一",
        r"年级第一",
        r"物超所值",
        r"推荐",
        r"感谢老师",
        r"考上[^，。\n ]{0,8}",
        r"提前录取",
        r"\d+(\.\d+)?/[\d.]+",
        r"\d+(\.\d+)?分",
        r"\d+(\.\d+)?块",
        r"比预估高了?\d+分",
    ]
    for pattern in keyword_patterns:
        for match in re.finditer(pattern, text):
            keywords.append(match.group(0))

    for line in evidence:
        if len(line) <= 18:
            keywords.append(line)

    words = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,12}", text)
    for word in words:
        if word not in STOP_WORDS and any(marker in word for marker in ["考", "分", "进", "谢", "荐", "值"]):
            keywords.append(word)

    return dedupe(keywords)[:20]


def dedupe(values: List[str]) -> List[str]:
    result = []
    seen = set()
    for value in values:
        normalized = value.strip(" ，。！？!；;：:")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def make_title(tags: List[str], evidence: List[str], text: str) -> str:
    if "期中考试" in tags and "成绩提升" in tags:
        return "用户反馈期中考试成绩提升"
    if "期中考试" in tags:
        return "用户反馈期中考试相关沟通"
    if "物超所值" in tags:
        return "家长认可课程物超所值并愿意推荐"
    if "成绩超预期" in tags and "中考成绩" in tags:
        return "用户反馈中考成绩超预期"
    if "提前学习" in tags and "课程认可" in tags:
        return "家长认可课程价值并反馈孩子提前学习"
    if "排名进步" in tags:
        return "用户反馈孩子排名和成绩明显进步"
    if "孩子主动学习" in tags:
        return "家长反馈孩子学习更主动自觉"
    if "转介绍意向" in tags:
        return "用户反馈课程有效并出现转介绍意向"
    if "成绩提升" in tags:
        return "用户反馈使用后成绩提升"
    if evidence:
        short = evidence[0]
        return short[:28] + ("..." if len(short) > 28 else "")
    return "微信对话素材"


def sentiment(text: str) -> str:
    negative = ["投诉", "不满意", "不好", "退费", "没效果", "太贵", "负担"]
    positive = ["谢谢", "感谢", "满意", "太棒", "认可", "进步", "提升", "考上", "录取", "物超所值", "推荐"]
    if contains_any(text, negative) and not contains_any(text, positive):
        return "负向"
    if contains_any(text, positive):
        return "正向"
    return "中性"


def summarize(text: str, tags: List[str], evidence: List[str], types: List[str], focus_text: str = "") -> str:
    parts = []
    if types:
        parts.append(f"这是一段{'、'.join(types[:3])}类微信素材。")
    else:
        parts.append("这是一段微信沟通素材。")

    if tags:
        parts.append(f"核心信息集中在{'、'.join(tags[:5])}。")

    if focus_text:
        focus_lines = [line.strip() for line in focus_text.splitlines() if line.strip() and line.strip() != "---"]
        if focus_lines:
            parts.append(f"图片标记区域重点强调：{'；'.join(focus_lines[:3])}。")

    if evidence:
        parts.append(f"关键证据包括：{'；'.join(evidence)}。")

    if "成绩提升" in tags or "成绩超预期" in tags:
        parts.append("可用于证明学习效果和成绩改善。")
    elif "物超所值" in tags:
        parts.append("可用于处理价格异议和增强报名信心。")
    elif "孩子主动学习" in tags:
        parts.append("可用于展示孩子学习习惯和主动性变化。")
    return "".join(parts)


def material_value(tags: List[str], scenarios: List[str]) -> str:
    if "物超所值" in tags:
        return "适合作为价格认可类强好评素材，用于回应客户对费用价值的顾虑。"
    if "成绩超预期" in tags:
        return "适合作为成绩超预期案例素材，用于增强客户对学习效果的信任。"
    if "排名进步" in tags or "成绩提升" in tags:
        return "适合作为成绩提升类证据素材，用于展示真实学习结果。"
    if "孩子主动学习" in tags:
        return "适合作为学习习惯改善素材，用于说明课程对自觉学习的推动。"
    if scenarios:
        return f"适合在“{scenarios[0]}”使用，帮助销售快速匹配客户沟通场景。"
    return "适合作为微信沟通案例素材，后续可通过标签和关键词检索复用。"


def infer_image_type(text: str, tags: List[str], types: List[str]) -> str:
    combined = "\n".join([text, " ".join(tags), " ".join(types)])
    matched = pick_by_rules(combined, IMAGE_TYPE_RULES, 1)
    if matched:
        return matched[0]
    if "好评反馈" in types or "效果反馈" in types:
        return "好评"
    return "其他"


def detected_grade(text: str) -> str:
    for word in GRADE_WORDS:
        if word in text:
            return word
    match = re.search(r"(小学|初中|高中)[一二三四五六七八九123456789]年级", text)
    return match.group(0) if match else ""


def choose_variant(seed: str, options: List[str]) -> str:
    digest = hashlib.md5(seed.encode("utf-8")).hexdigest()
    return options[int(digest[:8], 16) % len(options)]


def clean_pitch_anchor(value: str, limit: int = 52) -> str:
    text = clean_ocr_text(value).replace("\n", "，")
    text = re.sub(r"[\u202a-\u202e\u2066-\u2069]", "", text)
    text = text.replace("：", "，").replace(":", "，")
    text = re.sub(r"[“”\"'`]+", "", text)
    text = re.sub(r"\s+", "", text).strip(" ，。！？!；;：:")
    text = re.sub(r"[，,](会|回|跑|然后)$", "", text)
    text = re.sub(r"(的然后|了下)$", "", text)
    text = text.replace("用户反应", "家长反馈").replace("用户反馈", "家长反馈")
    text = text[:limit]
    text = re.sub(r"[，,](会|回|跑|然后)$", "", text)
    text = re.sub(r"(的然后|了下)$", "", text)
    return text.strip(" ，。！？!；;：:")


def split_pitch_chunks(value: str) -> List[str]:
    chunks = []
    for line in clean_ocr_text(value).splitlines():
        line = re.sub(r"\s+", "", line).strip(" ，。！？!；;：:")
        if not line:
            continue
        for chunk in re.split(r"[。！？!?；;]", line):
            chunk = clean_pitch_anchor(chunk, 72)
            if 4 <= len(chunk) <= 72:
                chunks.append(chunk)
    return dedupe(chunks)


def good_pitch_fact(value: str) -> bool:
    if not value:
        return False
    bad_words = [
        "这是一段",
        "核心信息集中",
        "关键证据包括",
        "图片标记区域",
        "适合作为",
        "用于",
        "销售",
        "搜索",
        "标签",
        "场景",
        "素材",
        "用户反馈",
        "对话素材",
        "微信素材",
    ]
    if any(word in value for word in bad_words):
        return False
    if value.count("、") >= 2 and not re.search(r"\d+分|排名|第[一二三四五六七八九十\d]+|提升|进步", value):
        return False
    if re.search(r"[A-Za-z]\d{4,}|\d{7,}", value):
        return False
    if re.search(r"[a-z]{3,}", value):
        return False
    if re.search(r"\d.*[A-Za-z]$", value) and not value.endswith(("PK", "QQ", "APP")):
        return False
    if "分钟" in value and not re.search(r"\d|十几|每天|学习时长|平均|一节|顶|不厌烦", value):
        return False
    digit_count = sum(1 for char in value if char.isdigit())
    if digit_count >= 6 and digit_count / max(len(value), 1) > 0.35:
        return False
    broken_starts = (
        "的人",
        "的，",
        "了，",
        "也，",
        "都，",
        "把，",
        "到，",
        "因为，",
        "但是，",
        "可是，",
        "然后，",
        "还有，",
        "同时，",
        "后面，",
        "前面，",
        "我们，",
        "你，",
        "您，",
        "所以，",
        "所以推荐",
        "想问问",
        "我想问问",
        "前学",
        "的物",
        "门聊",
        "以推荐",
        "游在",
        "跑",
        "为什么",
    )
    if value.startswith(broken_starts):
        return False
    if value.startswith(("了", "也", "都", "把")):
        return False
    if value.startswith("的") and not value.startswith("的确"):
        return False
    if value.endswith(("都", "也", "和", "因为", "也需要", "一直都是年级")):
        return False
    if re.search(r"(嘛|吗|可不可以|可以分享|要不要|怎么样)", value) and not re.search(r"\d+分|排名|第[一二三四五六七八九十\d]+|提升|进步", value):
        return False
    if re.match(r"^(的人|的|了|也|都|把|到|因为|但是|可是|然后|还有|同时)[，,。]", value):
        return False
    useful_words = [
        "分",
        "排名",
        "年级",
        "班级",
        "全县",
        "全校",
        "提升",
        "进步",
        "提高",
        "考上",
        "录取",
        "满分",
        "及格",
        "不及格",
        "前十",
        "第一",
        "物超所值",
        "好用",
        "太好用",
        "听懂",
        "易懂",
        "通俗",
        "清晰",
        "清楚",
        "喜欢",
        "有趣",
        "主动",
        "认真",
        "每天",
        "坚持",
        "复习",
        "预习",
        "练习",
        "PK",
        "推荐",
        "后悔",
        "科大讯飞",
        "作业帮",
        "猿辅导",
        "学而思",
        "高途",
        "辅导班",
        "平板",
        "学习机",
        "QQ",
        "管控",
        "高效",
        "轻松",
        "认可",
        "良心",
        "靠谱",
        "满意",
        "信心",
        "省心",
        "救星",
        "效果",
        "很棒",
    ]
    bad_fragments = [
        "你之前",
        "你觉得",
        "您觉得",
        "您帮我",
        "帮我介绍",
        "需要买课",
        "欢迎您使用",
        "祝学习进步",
        "A佳查分",
        "G5",
        "6G5",
        "基瑙",
        "zvemil",
        "重真出现",
        "業建",
        "分钟前",
        "文化总分",
        "打算是",
        "来电推荐课程",
        "的然后",
        "我的分数是",
        "要不是因为",
        "分享一个好",
        "进步很明",
        "您看这个就知道排名",
        "我的录取通知是",
        "几分钟，给人",
        "多分钟逸",
        "动有趣味",
        "安卉",
        "出车",
        "多目",
        "为什么上课",
        "和老师分享一下",
        "推荐给我",
        "课然后",
        "下课程",
        "除了您满意",
        "一直都是年级",
        "土超",
    ]
    if any(fragment in value for fragment in bad_fragments):
        return False
    if re.search(r"(吗|嘛|呀|呢|有没有|要不要|怎么样)", value) and value.startswith(("你", "您", "那你", "那您")):
        return False
    return any(word in value for word in useful_words)


def pitch_facts(trusted_text: str, manual_text: str, useful_evidence: List[str]) -> List[str]:
    candidates = []
    manual_label = customer_label_text(manual_text)
    if manual_label:
        candidates.append(manual_label)
    filtered = dedupe([item for item in candidates if good_pitch_fact(item)])
    if manual_label:
        for line in useful_evidence:
            fact = clean_pitch_anchor(line, 72)
            if good_pitch_fact(fact) and all(fact not in item and item not in fact for item in filtered):
                filtered.append(fact)
        if filtered:
            return filtered[:2]
    for line in useful_evidence:
        candidates.append(clean_pitch_anchor(line, 72))
    filtered = dedupe([item for item in candidates if good_pitch_fact(item)])
    if filtered:
        return filtered[:3]
    for chunk in split_pitch_chunks(trusted_text):
        if good_pitch_fact(chunk):
            candidates.append(chunk)
    return dedupe([item for item in candidates if good_pitch_fact(item)])[:3]


def subject_phrase(text: str) -> str:
    subjects = []
    for subject in ["语文", "数学", "英语", "物理", "化学", "生物", "地理", "历史", "政治", "科学", "全科"]:
        if subject in text and subject not in subjects:
            subjects.append(subject)
    if "语数英" in text:
        for subject in ["语文", "数学", "英语"]:
            if subject not in subjects:
                subjects.append(subject)
    return "、".join(subjects[:3])


def competitor_brand(source: str) -> str:
    for word in COMPETITOR_WORDS:
        if word in source:
            return word
    return ""


def cue_score(source: str, cues: List[str]) -> int:
    return sum(1 for cue in cues if cue in source)


def is_product_explainer_pitch(
    facts: List[str],
    tags: List[str],
    image_type: str,
    source: str,
) -> bool:
    if image_type != "竞品对比":
        return False
    combined = "\n".join(facts + tags + [source])
    product_score = cue_score(combined, PRODUCT_EXPLAINER_CUES)
    if product_score < 2:
        plain_competitor_label = len(facts) == 1 and facts[0] in COMPETITOR_WORDS
        feedback_source = "\n".join(facts + [source])
        return plain_competitor_label and product_score >= 1 and cue_score(feedback_source, COMPETITOR_FEEDBACK_CUES) == 0
    feedback_source = "\n".join(facts + [source])
    feedback_score = cue_score(feedback_source, COMPETITOR_FEEDBACK_CUES)
    if feedback_score and product_score < 4 and not contains_any(combined, PRODUCT_EXPLAINER_STRONG_CUES):
        return False
    return True


def product_explainer_intro(source: str, seed: str) -> str:
    brand = competitor_brand(source)
    if brand:
        return choose_variant(
            seed + "|product-explainer-intro-brand",
            [
                f"您可以先看这个课程对比，重点不是单纯比较{brand}和洋葱哪个名字更响，而是看课程设计和讲解方式对孩子是否真的友好。",
                f"如果您也在对比{brand}这类产品，可以重点看课程本身。孩子最后能不能学进去，关键还是内容讲得清不清楚、孩子愿不愿意持续学。",
                f"这个对比您可以看一下，同样是讲知识点，洋葱更强调让孩子先听懂、愿意学，再配合练习去巩固。",
            ],
        )
    return choose_variant(
        seed + "|product-explainer-intro",
        [
            "您可以先看这个课程展示，重点是洋葱不是让孩子自己硬学，而是把知识点拆开讲，让孩子更容易跟上。",
            "这个课程画面您可以看一下，洋葱更强调把抽象知识讲直观，让孩子先听懂，再去做题巩固。",
            "您可以先看这个课程内容，核心不是堆题量，而是把孩子卡住的知识点讲清楚、练到位。",
        ],
    )


def product_explainer_detail(source: str, seed: str) -> str:
    if contains_any(source, ["人机交互", "互动", "找图形的规律", "重复出现", "分析与解答"]):
        return choose_variant(
            seed + "|product-explainer-interactive",
            [
                "像图里这种内容，洋葱会引导孩子先观察规律、再一步步分析答案，孩子不是被动看结论，而是在跟着课程思考。",
                "这种互动课程的好处是孩子能一边看一边参与，先把思路走通，再做题就不容易只靠蒙或者死记。",
                "洋葱会把观察、归纳和解题步骤拆开，让孩子知道每一步为什么这么做，比单纯看答案更容易吸收。",
            ],
        )
    if contains_any(source, ["精品短动画", "短动画", "场景化", "学习不费力", "有趣", "趣味性"]):
        return choose_variant(
            seed + "|product-explainer-animation",
            [
                "对孩子来说，愿意打开学很关键。洋葱用短动画和场景化方式讲知识点，降低理解门槛，也更容易坚持每天学一点。",
                "很多孩子不是不想学，是内容太枯燥就容易放弃。洋葱把课程做得更直观、更有参与感，孩子更容易愿意学下去。",
                "先让孩子觉得课程能听懂、能跟上，后面复习和练习才会真正发生，洋葱的优势就在这个持续学习的过程里。",
            ],
        )
    if contains_any(source, ["大量刷题", "口诀", "套路", "死记硬背"]):
        return choose_variant(
            seed + "|product-explainer-rote",
            [
                "比起直接让孩子背口诀、刷大量题，洋葱更强调先理解底层知识点，再通过练习巩固，这样孩子遇到变式题也更有思路。",
                "只靠刷题和背套路，孩子短期可能会做一两道题，但知识点不通还是容易换个题就卡住。洋葱会先把原理讲明白。",
                "洋葱不是让孩子机械记结论，而是把知识点讲透，再让孩子练对应题型，这样补弱科会更稳。",
            ],
        )
    if contains_any(source, ["板书", "分类讨论", "颜色", "清晰", "明确", "重点"]):
        return choose_variant(
            seed + "|product-explainer-board",
            [
                "复杂题最怕孩子跟丢步骤，洋葱会把板书、颜色和解题过程拆清楚，孩子知道先看什么、再算什么，学习会更有方向。",
                "这种内容不是简单把答案放出来，而是把分类、步骤和关键点标清楚，孩子听课时更容易抓住重点。",
                "洋葱会把复杂知识点拆成孩子能跟上的步骤，减少听不懂、记不住、做题没思路的问题。",
            ],
        )
    if contains_any(source, ["模块", "课程已升级", "同步课", "视频", "颗粒度", "哪里不会点哪里"]):
        return choose_variant(
            seed + "|product-explainer-module",
            [
                "洋葱的课程颗粒度会更细，哪里不会就点哪里学，不用孩子一上来跟很长的大课，查缺补漏会更精准。",
                "同步课拆得细的好处是孩子可以按薄弱点补，不会的地方反复看，学会后再配合练习巩固。",
                "如果孩子基础有断层，洋葱这种按模块拆开的课程更适合补漏，先把不会的点补上，再往后学会更顺。",
            ],
        )
    return choose_variant(
        seed + "|product-explainer-detail",
        [
            "洋葱的核心优势是把知识点讲得更细、更直观，再配合练习和复习，帮助孩子从听懂到会做逐步补起来。",
            "真正影响效果的不是买了哪个工具，而是孩子能不能持续学进去。洋葱会把课程、练习和查缺补漏串起来。",
            "孩子学习最怕只看热闹、没真正掌握，洋葱会把知识点拆开讲，再通过练习把薄弱点补实。",
        ],
    )


def product_explainer_question(source: str, seed: str) -> str:
    subjects = subject_phrase(source)
    if subjects and "、" not in subjects:
        return choose_variant(
            seed + "|product-explainer-question-subject",
            [
                f"孩子现在{subjects}是听课理解比较吃力，还是做题不知道怎么下手呀？",
                f"您看要不要我先帮孩子看看，{subjects}用洋葱从哪个模块开始补更合适呀？",
                f"孩子现在{subjects}最卡的是基础知识，还是题型应用呀？",
            ],
        )
    return choose_variant(
        seed + "|product-explainer-question",
        [
            "孩子现在是更需要先把知识点讲懂，还是更需要我帮他把不会的内容按模块补起来呀？",
            "您看要不要我结合孩子现在薄弱科目，给您看看洋葱怎么安排学习路径更合适？",
            "孩子现在最卡的是听不懂课，还是做题时不知道怎么下手呀？",
        ],
    )


def fact_paragraph(facts: List[str], seed: str) -> str:
    if not facts:
        return choose_variant(
            seed + "|fact-empty",
            [
                "我给您发一个真实家长反馈，您可以先对照孩子现在的情况看一下。",
                "您先看下这个真实反馈，里面能看到家长对洋葱使用后的实际感受。",
                "我发您一个真实沟通案例，您可以先看看家长实际用完后的反馈。",
            ],
        )
    if len(facts) == 1:
        return choose_variant(
            seed + "|fact-one",
            [
                f"您看看这个真实反馈，家长说{facts[0]}。",
                f"我给您发一个类似案例，家长这边反馈{facts[0]}。",
                f"您可以先看这个家长反馈，核心是{facts[0]}。",
                f"这个案例里比较关键的一点是{facts[0]}。",
            ],
        )
    if len(facts) == 2:
        return choose_variant(
            seed + "|fact-two",
            [
                f"您看看这个真实反馈，家长先说{facts[0]}，后面又补充{facts[1]}。",
                f"我给您发一个类似案例，家长这边反馈{facts[0]}，后面也说到{facts[1]}。",
                f"这个案例您可以参考下，比较关键的是{facts[0]}，还有{facts[1]}。",
                f"您可以看下这条反馈，家长一开始说{facts[0]}，后面又补充{facts[1]}。",
            ],
        )
    return choose_variant(
        seed + "|fact-three",
        [
            f"您看看这个真实反馈，里面最关键的是{facts[0]}，后面还说到{facts[1]}。",
            f"这个案例比较完整，家长先反馈{facts[0]}，后面还能看到{facts[1]}。",
            f"我发您这个案例看一下，里面比较有参考性的点是{facts[0]}，另外还有{facts[1]}。",
            f"您可以对照看一下，这条反馈里比较明确的是{facts[0]}，后面也提到{facts[1]}。",
        ],
    )


def product_paragraph(facts: List[str], tags: List[str], image_type: str, seed: str) -> str:
    source = "\n".join(facts) or "\n".join(tags + [image_type])
    subjects = subject_phrase(source)
    subject_text = f"{subjects}这几科" if "、" in subjects else (subjects or "薄弱科目")
    if contains_any(source, ["期中考", "期中考试"]):
        return choose_variant(
            seed + "|product-midterm",
            [
                f"期中这种阶段考试最能看出孩子最近有没有真的学进去，洋葱这边就是把课和练习结合起来，让孩子每天补一点、练一点，{subject_text}更容易看到变化。",
                "期中成绩能有变化，核心还是平时跟着洋葱把不会的知识点补上了，不是临时抱佛脚。孩子只要愿意每天学一点，进步会更稳。",
                "这个反馈说明洋葱不是让孩子一次学很久，而是靠持续听课、练习和查缺补漏，把期中前后的问题一点点补起来。",
            ],
        )
    if contains_any(source, ["中考", "高考", "考上", "上岸", "重高", "重点高中", "高中录取", "附中"]):
        return choose_variant(
            seed + "|product-exam",
            [
                "升学考试拼的就是平时有没有把漏洞补起来，洋葱比较适合帮孩子把知识点拆细，再配合练习去巩固，越早开始越不容易被动。",
                "这种升学结果背后，其实就是孩子愿意跟着洋葱把不会的地方一块块补起来，关键考试前才会更有底气。",
                "到中高考这种关键阶段，不能只靠临时刷题，洋葱的价值就是帮孩子把基础、题型和薄弱点系统补上。",
            ],
        )
    if contains_any(source, ["作业帮", "猿辅导", "学而思", "高途", "科大讯飞", "步步高", "辅导班", "一对一", "课外班"]):
        return choose_variant(
            seed + "|product-compare",
            [
                "所以选学习工具不能只看名气，关键是孩子能不能真的听懂、能不能管住使用场景。洋葱更强调跟着课程和练习走，把时间用在学习上。",
                "对比其他学习机或者网课，最重要的还是孩子愿不愿意学、学完会不会做题。洋葱的优势就是讲得更容易理解，也方便孩子持续复习和练习。",
                "如果之前试过别的方式效果一般，可以重点看孩子在洋葱上能不能听懂、能不能坚持学，这个比单纯买设备更关键。",
            ],
        )
    if contains_any(source, ["价格", "贵", "便宜", "值", "物超所值", "续费", "续购", "升级", "会员", "平板"]) and not contains_any(
        source,
        ["成绩", "排名", "提升", "进步", "提高", "涨分", "第一", "前十", "满分", "及格", "不及格", "中考", "高考", "考上", "上岸", "重高", "重点高中", "附中"],
    ):
        return choose_variant(
            seed + "|product-value",
            [
                "家长最后觉得值，核心不是因为买了一个产品，而是孩子真的把洋葱用起来了，能听课、能练习、能把不会的地方补上。",
                "费用肯定要考虑，但更关键的是孩子愿不愿意长期用。只要孩子跟着洋葱持续学，课和练习都用起来，这个投入才真正有价值。",
                "这个案例最打动人的地方，是家长看到孩子用洋葱后的变化。不是单纯买课，而是让孩子有一个能持续查缺补漏的学习工具。",
            ],
        )
    if contains_any(source, ["主动", "愿意", "喜欢", "兴趣", "坚持", "每天", "自学", "PK"]):
        return choose_variant(
            seed + "|product-active",
            [
                "孩子愿意主动学特别重要，洋葱的短课、练习和答题反馈能让孩子更容易坚持下来，先有学习兴趣，后面成绩才更容易跟上。",
                "只要孩子愿意打开洋葱学，每天哪怕多学一节课、做一点练习，长期下来就是在查缺补漏，成绩和习惯都会慢慢变好。",
                "这个反馈最关键的是孩子愿意学，洋葱就是把知识点讲得更轻、更有意思，让孩子不那么抗拒，后面才容易坚持。",
            ],
        )
    if contains_any(source, ["听懂", "易懂", "理解", "讲得好", "有趣", "动画", "救星", "清楚", "通俗"]):
        return choose_variant(
            seed + "|product-understand",
            [
                "很多孩子不是不想学，是学校课上没听透。洋葱把知识点讲得更直观，孩子先听懂，再去做题，就不会那么吃力。",
                "洋葱比较适合用来补课上没消化的内容，先把概念听明白，再配合练习巩固，孩子会更容易跟上学校节奏。",
                "这种反馈说明孩子能听懂很关键，洋葱的课程讲得细、节奏也轻，适合把卡住的知识点重新补一遍。",
            ],
        )
    if contains_any(source, ["查漏", "补缺", "预习", "复习", "专项", "知识点", "大题", "题型", "刷题", "压中"]):
        return choose_variant(
            seed + "|product-gap",
            [
                "洋葱比较适合做预习和查缺补漏，孩子哪里不会就先补哪里，补完再练题，比盲目刷一堆题更有效。",
                "如果孩子现在有知识点断层，洋葱可以按模块去补，先把不会的地方讲明白，再通过练习把题型吃透。",
                "这类反馈其实很适合说明洋葱的用法：先听课把漏洞补上，再做配套练习，孩子学习会更有方向。",
            ],
        )
    if contains_any(source, ["提升", "进步", "提高", "涨分", "第一", "前十", "满分", "及格", "不及格", "从", "到"]):
        return choose_variant(
            seed + "|product-score",
            [
                f"成绩能有变化，关键还是孩子愿意跟着洋葱把{subject_text}里不会的地方补起来，课听懂了、题练到了，分数才会一点点往上走。",
                "这类进步不是凭空来的，核心是孩子能坚持用洋葱听课和练习，把原来不会的知识点慢慢补上。",
                "只要孩子愿意学，跟着洋葱课程节奏走，把漏洞及时补上，成绩和排名就有机会看到比较明显的变化。",
            ],
        )
    return choose_variant(
        seed + "|product-general",
        [
            "这个反馈主要能看出，孩子只要愿意用起来，洋葱就能帮他把听课、练习和查缺补漏串起来，学习会更有方向。",
            "洋葱不是让孩子盲目多学，而是把知识点拆开讲，再配合练习巩固，让孩子知道自己哪里不会、该怎么补。",
            "家长看重的其实是孩子能不能真正学进去，洋葱的价值就是让孩子更容易听懂，也更容易坚持去补薄弱点。",
        ],
    )


def closing_question(facts: List[str], tags: List[str], image_type: str, seed: str) -> str:
    source = "\n".join(facts) or "\n".join(tags + [image_type])
    subjects = subject_phrase(source)
    if contains_any(source, ["作业帮", "猿辅导", "学而思", "高途", "科大讯飞", "步步高", "学习机", "平板", "QQ", "管控"]):
        return choose_variant(
            seed + "|question-compare",
            [
                "您现在更担心孩子拿设备玩，还是想先看看洋葱怎么帮孩子把学习管起来呀？",
                "您主要是担心孩子用平板不专心，还是想看看洋葱怎么安排课程和练习更合适呀？",
                "您看要不要我先给孩子规划一下，用洋葱怎么学才能避免变成单纯玩设备呀？",
            ],
        )
    if contains_any(source, ["价格", "贵", "便宜", "值", "物超所值", "续费", "续购", "升级", "会员"]) and not contains_any(
        source,
        ["成绩", "排名", "提升", "进步", "提高", "涨分", "第一", "前十", "满分", "及格", "不及格", "中考", "高考", "考上", "上岸", "重高", "重点高中", "附中"],
    ):
        return choose_variant(
            seed + "|question-value",
            [
                "您看要不要我结合孩子现在的情况，帮您规划一下洋葱怎么学更容易看见效果呀？",
                "您现在最想先确认洋葱的学习效果，还是想先了解孩子适合从哪一科开始呀？",
                "您看我先帮孩子做个洋葱学习规划，让您判断这笔投入值不值可以吗？",
            ],
        )
    if contains_any(source, ["期中考", "期中考试"]):
        return choose_variant(
            seed + "|question-midterm",
            [
                "您家孩子这次期中后，哪一科最需要用洋葱重点补一下呀？",
                "您看要不要我先按期中情况，帮孩子规划一下洋葱从哪科开始提呀？",
                "孩子期中暴露出来的问题里，您最想先用洋葱补哪一块呀？",
            ],
        )
    if contains_any(source, ["中考", "高考", "考上", "上岸", "重高", "重点高中", "高中录取", "附中"]):
        return choose_variant(
            seed + "|question-exam",
            [
                "您看要不要我先帮孩子按升学目标规划一下，洋葱从哪一科开始补最合适呀？",
                "孩子现在离目标还有哪些科目比较吃力，我帮您看看洋葱怎么安排可以吗？",
                "您家孩子如果也要冲目标学校，要不要我先帮您看看洋葱怎么学更稳呀？",
            ],
        )
    if subjects:
        if "、" not in subjects:
            return choose_variant(
                seed + "|question-one-subject",
                [
                    f"孩子现在{subjects}最需要补的是哪一块呀，我帮您看看洋葱怎么安排更合适？",
                    f"您看要不要我先帮孩子分析一下，{subjects}用洋葱从哪里开始补比较好呀？",
                    f"孩子目前{subjects}最卡的是哪类题呀，我帮您规划一下洋葱学习路径可以吗？",
                ],
            )
        return choose_variant(
            seed + "|question-subject",
            [
                f"孩子现在{subjects}这几科里哪一科最吃力呀，我帮您看看洋葱怎么安排更合适？",
                f"您看要不要我先帮孩子分析一下，{subjects}这几科用洋葱从哪里开始补比较好呀？",
                f"孩子目前{subjects}里最卡的是哪一科呀，我帮您规划一下洋葱学习路径可以吗？",
            ],
        )
    return choose_variant(
        seed + "|question-general",
        [
            "您看要不要我先帮孩子分析一下，洋葱从哪一科开始学最合适呀？",
            "孩子现在学习上最头疼的是哪一块呀，我帮您看看洋葱怎么安排可以吗？",
            "您看我先帮孩子做个洋葱学习规划，看看先补哪里最容易出效果可以吗？",
        ],
    )


def make_pitch(
    text: str,
    tags: List[str],
    scenarios: List[str],
    evidence: List[str],
    image_type: str,
    manual_text: str = "",
) -> str:
    trusted_text = "\n".join([manual_text, text])
    useful_evidence = [
        line
        for line in evidence
        if line and line != "未提取到明确证据句" and len(line) >= 6 and re.search(r"[\u4e00-\u9fff]", line)
    ]
    facts = pitch_facts(trusted_text, manual_text, useful_evidence)
    if is_product_explainer_pitch(facts, tags, image_type, trusted_text):
        fact = product_explainer_intro(trusted_text, trusted_text)
        product = product_explainer_detail(trusted_text, trusted_text)
        question = product_explainer_question(trusted_text, trusted_text)
    else:
        fact = fact_paragraph(facts, trusted_text)
        product = product_paragraph(facts, tags, image_type, trusted_text)
        question = closing_question(facts, tags, image_type, trusted_text)
    if not question.endswith("？"):
        question = question.rstrip("。！？!?") + "？"
    return f"{fact}\n\n{product}\n\n{question}"


def enrich_text(
    ocr_text: str,
    focus_text: str = "",
    marked_region_count: int = 0,
    manual_text: str = "",
    note_text: str = "",
) -> Dict[str, str]:
    text = clean_ocr_text(ocr_text)
    focus = clean_ocr_text(focus_text)
    manual = clean_ocr_text(manual_text)
    note = clean_ocr_text(note_text)
    analysis_text = "\n".join(part for part in [manual, manual, manual, note, focus, focus, text] if part)
    tags = pick_by_rules(analysis_text, TAG_RULES, 12)
    types = pick_by_rules(analysis_text, TYPE_RULES, 5)
    scenarios = pick_by_rules(analysis_text, SCENARIO_RULES, 6)
    evidence = pick_evidence(analysis_text, focus)
    keywords = extract_keywords(text, tags, evidence)
    focus_keywords = extract_keywords(focus, tags, evidence) if focus else []
    keywords = dedupe(focus_keywords + keywords)[:20]
    title = make_title(tags, evidence, analysis_text)
    summary = summarize(text, tags, evidence, types, focus)

    if not tags:
        tags = ["微信对话", "待人工复核"]
    if not types:
        types = ["其他"]
    if not scenarios:
        scenarios = ["需要人工判断适用场景时"]
    if not evidence:
        evidence = ["未提取到明确证据句"]
    if not keywords:
        fallback_words = [
            word
            for word in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,12}", analysis_text)
            if word not in STOP_WORDS and not is_noise_line(word)
        ]
        keywords = dedupe(tags + fallback_words)[:20]

    image_type = infer_image_type(analysis_text, tags, types)
    pitch = make_pitch(analysis_text, tags, scenarios, evidence, image_type, manual)

    search_text = "\n".join(
        [
            manual,
            note,
            title,
            summary,
            " ".join(types),
            " ".join(tags),
            " ".join(keywords),
            " ".join(scenarios),
            " ".join(evidence),
            pitch,
            focus,
            text,
        ]
    )

    if marked_region_count > 0 and focus:
        marker_note = f"检测到{marked_region_count}个红色/高亮标记区域，已优先参考标记附近文字。"
    elif marked_region_count > 0:
        marker_note = f"检测到{marked_region_count}个红色/高亮标记区域，但标记区域未识别出稳定文字。"
    else:
        marker_note = "未检测到明显红色/高亮标记区域。"

    return {
        "AI重点文字": focus,
        "AI标记说明": marker_note,
        "AI标题": title,
        "AI内容总结": summary,
        "AI对话类型": " / ".join(types),
        "AI情绪倾向": sentiment(text),
        "AI推荐标签": "、".join(tags),
        "AI搜索关键词": "、".join(keywords),
        "AI适用场景": "、".join(scenarios),
        "AI证据句": "\n".join(evidence),
        "AI素材价值": material_value(tags, scenarios),
        "AI推荐话术": pitch,
        "AI检索文本": search_text,
        "_图片类型": image_type,
    }


def get_or_build_ocr(
    tenant_token: str,
    record: Dict[str, Any],
    fields: Dict[str, Any],
    image_field_name: str,
    image_field_id: str,
    table_id: str,
    output_dir: Path,
    ocr_helper: str,
    ocr_field_name: str,
) -> str:
    existing = cell_text(fields.get(ocr_field_name))
    if existing:
        return existing

    rec_id = record_id(record)
    texts = []
    for index, attachment in enumerate(attachment_tokens(fields.get(image_field_name)), start=1):
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
        try:
            text = ocr_image(ocr_helper, image_path)
            if text:
                texts.append(text)
        except Exception as exc:
            print(f"WARN OCR failed for {rec_id} image {index}: {exc}", file=sys.stderr)
    return "\n\n---\n\n".join(texts).strip() or "未识别到文字"


def focus_ocr_image(helper_path: str, image_path: Path) -> Dict[str, Any]:
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
        raise FeishuError(completed.stderr.strip() or f"Focus OCR failed for {image_path}")
    try:
        return json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise FeishuError(f"Focus OCR returned invalid JSON for {image_path}: {completed.stdout[:200]}") from exc


def get_marked_focus(
    tenant_token: str,
    record: Dict[str, Any],
    fields: Dict[str, Any],
    image_field_name: str,
    image_field_id: str,
    table_id: str,
    output_dir: Path,
    focus_helper: str,
) -> tuple[str, int]:
    rec_id = record_id(record)
    focus_texts = []
    region_count = 0
    for index, attachment in enumerate(attachment_tokens(fields.get(image_field_name)), start=1):
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
        try:
            result = focus_ocr_image(focus_helper, image_path)
        except Exception as exc:
            print(f"WARN focus OCR failed for {rec_id} image {index}: {exc}", file=sys.stderr)
            continue
        region_count += int(result.get("marked_region_count") or 0)
        focus_text = str(result.get("focus_text") or "").strip()
        if focus_text:
            focus_texts.append(focus_text)
    return "\n\n---\n\n".join(focus_texts).strip(), region_count


def update_record_fields(
    token: str,
    app_token: str,
    table_id: str,
    rec_id: str,
    fields: Dict[str, str],
) -> None:
    path = f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{rec_id}"
    http_json("PUT", path, token=token, body={"fields": fields})


def main() -> int:
    args = parse_args()

    app_id = os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        raise FeishuError("Please set FEISHU_APP_ID and FEISHU_APP_SECRET environment variables")

    wiki_token, direct_app_token, url_table_id, url_view_id = parse_feishu_url(args.url)
    table_id = args.table_id or url_table_id
    view_id = None if args.ignore_view else (args.view_id or url_view_id)
    if not table_id:
        raise FeishuError("Table id missing. Pass --table-id or use a URL with table=...")

    tenant_token = get_tenant_access_token(app_id, app_secret)
    app_token = resolve_app_token(tenant_token, wiki_token, direct_app_token)

    required_fields = [args.ocr_field, args.status_field, args.image_type_field, *MATERIAL_FIELDS]
    fields_by_name = ensure_fields(tenant_token, app_token, table_id, required_fields, args.dry_run)

    fields_by_name = list_fields(tenant_token, app_token, table_id)
    image_field = fields_by_name.get(args.image_field)
    if not image_field:
        raise FeishuError(f"Cannot find image field: {args.image_field}")

    image_field_id = image_field.get("field_id")
    if not image_field_id:
        raise FeishuError(f"Image field id missing for field: {args.image_field}")

    records = list_records(tenant_token, app_token, table_id, view_id)
    print(f"Loaded {len(records)} records from table {table_id}.")

    manual_tag_filter = (args.manual_tag or "").strip()
    status_filters = {
        value.strip()
        for value in args.status_values.replace("，", ",").split(",")
        if value.strip()
    }
    processed = 0
    failed = 0
    with tempfile.TemporaryDirectory(prefix="feishu-material-enrich-") as tmp:
        output_dir = Path(tmp)
        for record in records:
            if processed >= args.limit:
                break

            fields = record.get("fields") or {}
            rec_id = record_id(record)
            if manual_tag_filter:
                manual_value = cell_text(fields.get(args.manual_field))
                if manual_tag_filter not in manual_value and manual_value not in manual_tag_filter:
                    log_skip(args, f"SKIP {rec_id}: manual tag does not match")
                    continue

            if status_filters:
                status_value = cell_text(fields.get(args.status_field))
                if status_value not in status_filters:
                    log_skip(args, f"SKIP {rec_id}: status {status_value or '<empty>'} not in filter")
                    continue

            if args.skip_filled and has_value(fields.get("AI标题")):
                log_skip(args, f"SKIP {rec_id}: AI标题 already has value")
                continue

            target_field_names = [args.ocr_field, args.status_field, args.image_type_field, *MATERIAL_FIELDS]
            missing_target_fields = [
                name for name in target_field_names if not cell_text(fields.get(name))
            ]
            if cell_text(fields.get(args.status_field)) not in {"", "已完成"}:
                missing_target_fields.append(args.status_field)
            if args.only_missing and not missing_target_fields:
                log_skip(args, f"SKIP {rec_id}: no missing target fields")
                continue

            if not attachment_tokens(fields.get(args.image_field)) and not cell_text(fields.get(args.ocr_field)):
                log_skip(args, f"SKIP {rec_id}: no image and no OCR text")
                continue

            try:
                if not args.dry_run and (not args.only_missing or not cell_text(fields.get(args.status_field))):
                    update_record_fields(
                        tenant_token,
                        app_token,
                        table_id,
                        rec_id,
                        {args.status_field: "分析中"},
                    )

                ocr_text = get_or_build_ocr(
                    tenant_token,
                    record,
                    fields,
                    args.image_field,
                    image_field_id,
                    table_id,
                    output_dir,
                    args.ocr_helper,
                    args.ocr_field,
                )
                if args.only_missing and cell_text(fields.get("AI重点文字")):
                    focus_text = cell_text(fields.get("AI重点文字"))
                    marked_region_count = 0
                else:
                    focus_text, marked_region_count = get_marked_focus(
                        tenant_token,
                        record,
                        fields,
                        args.image_field,
                        image_field_id,
                        table_id,
                        output_dir,
                        args.focus_helper,
                    )
                enrichment = enrich_text(
                    ocr_text,
                    focus_text,
                    marked_region_count,
                    cell_text(fields.get(args.manual_field)),
                    cell_text(fields.get(args.note_field)),
                )
                image_type = enrichment.pop("_图片类型", "")
                write_fields = {args.ocr_field: ocr_text, args.status_field: "已完成", **enrichment}
                if image_type and not cell_text(fields.get(args.image_type_field)):
                    write_fields[args.image_type_field] = image_type
                if args.only_missing:
                    write_fields = {
                        name: value
                        for name, value in write_fields.items()
                        if (
                            (name == args.status_field and cell_text(fields.get(name)) != "已完成")
                            or not cell_text(fields.get(name))
                        )
                        and value
                    }
                    if not write_fields:
                        log_skip(args, f"SKIP {rec_id}: generated no missing field values")
                        continue

                if args.dry_run:
                    dry_run_fields = dict(write_fields)
                    dry_run_fields.pop(args.ocr_field, None)
                    print(f"DRY-RUN {rec_id}: {json.dumps(dry_run_fields, ensure_ascii=False)[:700]}")
                else:
                    update_record_fields(tenant_token, app_token, table_id, rec_id, write_fields)
                    print(f"UPDATED {rec_id}: {enrichment['AI标题']}")

                processed += 1
                time.sleep(0.2)
            except Exception as exc:
                failed += 1
                if not args.dry_run:
                    try:
                        update_record_fields(
                            tenant_token,
                            app_token,
                            table_id,
                            rec_id,
                            {args.status_field: "分析失败"},
                        )
                    except Exception:
                        pass
                if not args.continue_on_error:
                    raise
                print(f"ERROR {rec_id}: {exc}", file=sys.stderr)
                time.sleep(0.2)

    print(f"Done. Processed {processed} record(s). Failed {failed} record(s).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FeishuError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
