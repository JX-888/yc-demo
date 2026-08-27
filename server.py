#!/usr/bin/env python3
from __future__ import annotations

import cgi
import json
import mimetypes
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
import uuid
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent / "scripts"))

from feishu_bitable_ocr import (  # noqa: E402
    FeishuError,
    get_tenant_access_token,
    http_json,
    list_fields,
    parse_feishu_url,
    resolve_app_token,
)


DEFAULT_FEISHU_URL = ""
BASE_DIR = Path(__file__).parent
WEB_DIR = BASE_DIR / "web"
SCRIPTS_DIR = BASE_DIR / "scripts"
API_BASE = "https://open.feishu.cn/open-apis"


class AppConfig:
    def __init__(self) -> None:
        self.feishu_url = os.environ.get("FEISHU_BITABLE_URL", DEFAULT_FEISHU_URL)
        self.app_id = os.environ.get("FEISHU_APP_ID", "")
        self.app_secret = os.environ.get("FEISHU_APP_SECRET", "")
        self.image_field = os.environ.get("FEISHU_IMAGE_FIELD", "图片")
        self.manual_field = os.environ.get("FEISHU_MANUAL_FIELD", "人工标签")
        self.stage_field = os.environ.get("FEISHU_STAGE_FIELD", "所属学段")
        self.grade_field = os.environ.get("FEISHU_GRADE_FIELD", "具体年级")
        self.image_type_field = os.environ.get("FEISHU_IMAGE_TYPE_FIELD", "图片类型")
        self.note_field = os.environ.get("FEISHU_NOTE_FIELD", "补充说明")
        self.status_field = os.environ.get("FEISHU_STATUS_FIELD", "AI分析状态")

        self.wiki_token: Optional[str] = None
        self.direct_app_token: Optional[str] = None
        self.table_id: Optional[str] = None
        self.view_id: Optional[str] = None
        if self.feishu_url:
            wiki_token, direct_app_token, table_id, view_id = parse_feishu_url(self.feishu_url)
            self.wiki_token = wiki_token
            self.direct_app_token = direct_app_token
            self.table_id = table_id
            self.view_id = view_id
        self.tenant_token: Optional[str] = None
        self.app_token: Optional[str] = None
        self.fields_by_name: Dict[str, Dict[str, Any]] = {}

    def ensure_ready(self) -> None:
        if not self.feishu_url:
            raise FeishuError("Please set FEISHU_BITABLE_URL before using Feishu APIs")
        if not self.table_id:
            raise FeishuError("FEISHU_BITABLE_URL must include table=...")
        if not self.app_id or not self.app_secret:
            raise FeishuError("Please set FEISHU_APP_ID and FEISHU_APP_SECRET before using Feishu APIs")
        if not self.tenant_token:
            self.tenant_token = get_tenant_access_token(self.app_id, self.app_secret)
        if not self.app_token:
            self.app_token = resolve_app_token(self.tenant_token, self.wiki_token, self.direct_app_token)
        self.fields_by_name = list_fields(self.tenant_token, self.app_token, self.table_id)
        if self.image_field not in self.fields_by_name:
            raise FeishuError(f"Cannot find image field: {self.image_field}")
        ensure_text_fields(
            self.tenant_token,
            self.app_token,
            self.table_id,
            self.fields_by_name,
            [
                self.manual_field,
                self.stage_field,
                self.grade_field,
                self.image_type_field,
                self.note_field,
                self.status_field,
            ],
        )
        self.fields_by_name = list_fields(self.tenant_token, self.app_token, self.table_id)


CONFIG: Optional[AppConfig] = None
SYNC_LOCK = threading.Lock()
SYNC_LAST_AT = 0.0


def ensure_text_fields(
    token: str,
    app_token: str,
    table_id: str,
    fields_by_name: Dict[str, Dict[str, Any]],
    field_names: List[str],
) -> None:
    for field_name in field_names:
        if field_name in fields_by_name:
            continue
        path = f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
        http_json("POST", path, token=token, body={"field_name": field_name, "type": 1})


def multipart_form_data(fields: Dict[str, str], files: Dict[str, tuple[str, bytes, str]]) -> tuple[bytes, str]:
    boundary = f"----codex-feishu-{uuid.uuid4().hex}"
    chunks: List[bytes] = []

    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )

    for name, (filename, content, content_type) in files.items():
        safe_name = filename.replace('"', "")
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                (
                    f'Content-Disposition: form-data; name="{name}"; '
                    f'filename="{safe_name}"\r\n'
                ).encode(),
                f"Content-Type: {content_type}\r\n\r\n".encode(),
                content,
                b"\r\n",
            ]
        )

    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def upload_media(token: str, app_token: str, file_path: Path, filename: str, content_type: str) -> str:
    content = file_path.read_bytes()
    body, content_type_header = multipart_form_data(
        {
            "file_name": filename,
            "parent_type": "bitable_file",
            "parent_node": app_token,
            "size": str(len(content)),
        },
        {"file": (filename, content, content_type)},
    )
    request = urllib.request.Request(
        f"{API_BASE}/drive/v1/medias/upload_all",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type_header,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise FeishuError(f"Upload media failed: HTTP {exc.code}: {details}") from exc
    if result.get("code") != 0:
        raise FeishuError(f"Upload media failed: code={result.get('code')} msg={result.get('msg')}")
    file_token = (result.get("data") or {}).get("file_token")
    if not file_token:
        raise FeishuError(f"Upload media response missing file_token: {result}")
    return file_token


def create_bitable_record(config: AppConfig, file_tokens: List[str], form: Dict[str, str]) -> Dict[str, Any]:
    if not config.tenant_token or not config.app_token:
        raise FeishuError("Feishu config not initialized")

    fields: Dict[str, Any] = {
        config.image_field: [{"file_token": token} for token in file_tokens],
        config.manual_field: form.get("manualTag", "").strip(),
        config.status_field: "待分析",
    }
    if form.get("stage", "").strip():
        fields[config.stage_field] = form["stage"].strip()
    if form.get("grade", "").strip():
        fields[config.grade_field] = form["grade"].strip()
    if form.get("imageType", "").strip():
        fields[config.image_type_field] = form["imageType"].strip()
    if form.get("note", "").strip():
        fields[config.note_field] = form["note"].strip()

    path = f"/bitable/v1/apps/{config.app_token}/tables/{config.table_id}/records"
    result = http_json("POST", path, token=config.tenant_token, body={"fields": fields})
    record = (result.get("data") or {}).get("record") or result.get("data") or {}
    return record


class MaterialHandler(SimpleHTTPRequestHandler):
    server_version = "MaterialDemo/0.1"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def do_GET(self) -> None:
        if self.path.startswith("/api/health"):
            self.write_json({"ok": True})
            return
        if self.path.startswith("/api/materials"):
            self.handle_get_materials()
            return
        super().do_GET()

    def do_POST(self) -> None:
        if self.path.startswith("/api/materials"):
            self.handle_create_material()
            return
        if self.path.startswith("/api/analyze"):
            self.handle_analyze()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def handle_get_materials(self) -> None:
        sync_error = ""
        try:
            config = CONFIG
            if config is not None and should_sync_on_materials_get():
                export_materials(config, timeout=900)
        except Exception as exc:
            sync_error = str(exc)

        materials = WEB_DIR / "assets" / "materials.json"
        if materials.exists():
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            if sync_error:
                self.send_header("X-Material-Sync-Error", urllib.parse.quote(sync_error[:500]))
            self.end_headers()
            self.wfile.write(materials.read_bytes())
            return
        if sync_error:
            self.write_json({"ok": False, "error": sync_error}, status=HTTPStatus.BAD_REQUEST)
            return
        self.write_json([])

    def handle_create_material(self) -> None:
        try:
            config = CONFIG
            if config is None:
                raise FeishuError("Server config missing")
            config.ensure_ready()
            if not config.tenant_token or not config.app_token:
                raise FeishuError("Feishu config not initialized")

            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                raise FeishuError("Content-Type must be multipart/form-data")

            form_data = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={
                    "REQUEST_METHOD": "POST",
                    "CONTENT_TYPE": content_type,
                    "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
                },
            )

            form = {
                "manualTag": field_value(form_data, "manualTag"),
                "stage": field_value(form_data, "stage"),
                "grade": field_value(form_data, "grade"),
                "imageType": field_value(form_data, "imageType"),
                "note": field_value(form_data, "note"),
            }
            if not form["manualTag"].strip():
                raise FeishuError("人工标签不能为空")

            image_items = field_items(form_data, "images")
            if not image_items:
                raise FeishuError("请至少上传一张图片")

            file_tokens: List[str] = []
            with tempfile.TemporaryDirectory(prefix="material-upload-") as tmp:
                temp_dir = Path(tmp)
                for index, item in enumerate(image_items, start=1):
                    filename = Path(item.filename or f"image-{index}.png").name
                    content_type = item.type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
                    target = temp_dir / f"{index}-{filename}"
                    with target.open("wb") as output:
                        shutil.copyfileobj(item.file, output)
                    file_tokens.append(upload_media(config.tenant_token, config.app_token, target, filename, content_type))

            record = create_bitable_record(config, file_tokens, form)
            self.write_json(
                {
                    "ok": True,
                    "record": record,
                    "file_tokens": file_tokens,
                    "status": "待分析",
                    "message": "已写入飞书多维表",
                }
            )
        except Exception as exc:
            self.write_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def handle_analyze(self) -> None:
        try:
            config = CONFIG
            if config is None:
                raise FeishuError("Server config missing")
            config.ensure_ready()

            body = read_json_body(self)
            manual_tag = str(body.get("manualTag") or "").strip()
            limit = clamp_int(body.get("limit"), default=3, minimum=1, maximum=20)

            env = build_feishu_env(config)

            enrich_cmd = [
                sys.executable,
                str(SCRIPTS_DIR / "feishu_material_enrich.py"),
                "--url",
                config.feishu_url,
                "--image-field",
                config.image_field,
                "--manual-field",
                config.manual_field,
                "--note-field",
                config.note_field,
                "--ocr-field",
                "AI提取文字",
                "--status-field",
                config.status_field,
                "--image-type-field",
                config.image_type_field,
                "--ignore-view",
                "--limit",
                str(limit),
                "--only-missing",
                "--continue-on-error",
                "--quiet-skips",
            ]
            if manual_tag:
                enrich_cmd.extend(["--manual-field", config.manual_field, "--manual-tag", manual_tag])
            else:
                enrich_cmd.extend(["--status-values", "待分析,分析失败"])

            enrich_result = run_local_command(enrich_cmd, env=env, timeout=900)
            export_result = export_materials(config, timeout=900, force=True)

            processed, failed = parse_done_line(enrich_result["output"])
            self.write_json(
                {
                    "ok": True,
                    "message": "分析任务完成，已同步网页素材数据",
                    "manualTag": manual_tag,
                    "processed": processed,
                    "failed": failed,
                    "enrichLog": tail_text(enrich_result["output"], 18),
                    "exportLog": tail_text(export_result["output"], 8),
                }
            )
        except Exception as exc:
            self.write_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def write_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def field_value(form_data: cgi.FieldStorage, name: str) -> str:
    item = form_data[name] if name in form_data else None
    if item is None or isinstance(item, list):
        return ""
    value = item.value
    return value if isinstance(value, str) else ""


def field_items(form_data: cgi.FieldStorage, name: str) -> List[cgi.FieldStorage]:
    if name not in form_data:
        return []
    item = form_data[name]
    if isinstance(item, list):
        return [entry for entry in item if getattr(entry, "filename", None)]
    return [item] if getattr(item, "filename", None) else []


def read_json_body(handler: MaterialHandler) -> Dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8")
    if not raw.strip():
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise FeishuError("JSON body must be an object")
    return parsed


def clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def clamp_float(value: Any, default: float, minimum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, number)


def run_local_command(command: List[str], env: Dict[str, str], timeout: int) -> Dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=str(BASE_DIR),
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    output = "\n".join(part for part in [completed.stdout, completed.stderr] if part)
    if completed.returncode != 0:
        raise FeishuError(tail_text(output, 24) or f"Command failed with exit code {completed.returncode}")
    return {"returncode": completed.returncode, "output": output}


def build_feishu_env(config: AppConfig) -> Dict[str, str]:
    env = os.environ.copy()
    env["FEISHU_APP_ID"] = config.app_id
    env["FEISHU_APP_SECRET"] = config.app_secret
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONPYCACHEPREFIX", "/tmp/python-pycache")
    env.setdefault("CLANG_MODULE_CACHE_PATH", "/tmp/clang-module-cache")
    return env


def should_sync_on_materials_get() -> bool:
    value = os.environ.get("FEISHU_SYNC_ON_GET", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def export_materials(config: AppConfig, timeout: int = 900, force: bool = False) -> Dict[str, Any]:
    global SYNC_LAST_AT
    if not force and not should_sync_on_materials_get():
        return {"returncode": 0, "output": "SKIP sync disabled"}

    interval = clamp_float(os.environ.get("FEISHU_SYNC_INTERVAL_SECONDS"), default=0.0, minimum=0.0)
    with SYNC_LOCK:
        now = time.time()
        if not force and interval and now - SYNC_LAST_AT < interval:
            return {"returncode": 0, "output": "SKIP sync interval"}

        config.ensure_ready()
        export_cmd = [
            sys.executable,
            str(SCRIPTS_DIR / "export_demo_assets.py"),
            "--url",
            config.feishu_url,
            "--image-field",
            config.image_field,
            "--limit",
            "0",
            "--max-images-per-record",
            "0",
            "--continue-on-error",
            "--reuse-existing-images",
            "--ignore-view",
        ]
        result = run_local_command(export_cmd, env=build_feishu_env(config), timeout=timeout)
        SYNC_LAST_AT = time.time()
        return result


def parse_done_line(output: str) -> tuple[int, int]:
    for line in reversed(output.splitlines()):
        if line.startswith("Done. Processed"):
            parts = line.replace(".", "").split()
            try:
                return int(parts[2]), int(parts[5])
            except (IndexError, ValueError):
                return 0, 0
    return 0, 0


def tail_text(text: str, line_count: int) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return "\n".join(lines[-line_count:])


def main() -> int:
    global CONFIG
    CONFIG = AppConfig()
    port = int(os.environ.get("PORT", "8787"))
    host = os.environ.get("HOST", "0.0.0.0")
    server = ThreadingHTTPServer((host, port), MaterialHandler)
    cert_file = os.environ.get("HTTPS_CERT_FILE", "")
    key_file = os.environ.get("HTTPS_KEY_FILE", "")
    scheme = "http"
    if cert_file and key_file:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=cert_file, keyfile=key_file)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = "https"
    print(f"Material demo server: {scheme}://{host}:{port}/", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
