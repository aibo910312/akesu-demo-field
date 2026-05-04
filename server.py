"""区域作物示范田一览图 - 后端服务"""
import hashlib
import json
import os
import sqlite3
import time
import uuid
import urllib.parse
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn
import email.parser
import io

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "demo_fields.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
ARCHIVE_PATH = os.path.join(BASE_DIR, "data", "deleted_archive.jsonl")

# 飞书配置（复用现有凭证）
FEISHU_APP_ID = "cli_a930f89c8e7a1cc2"
FEISHU_APP_SECRET = "g9MrlJfE0s0VKSSdK41JRgbUXWjM7aJA"
FEISHU_APP_TOKEN = ""  # 需要创建新的多维表格后填入
FEISHU_TABLE_ID = ""   # 需要创建新的表后填入
FEISHU_ENABLED = False  # 飞书表格配置好后改为 True

_token = None
_token_expire = 0

os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

_sessions = {}


# ── 数据库初始化 ──
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS demo_fields (
            id TEXT PRIMARY KEY,
            township TEXT NOT NULL,
            village TEXT DEFAULT '',
            crop TEXT NOT NULL,
            farmer_name TEXT DEFAULT '',
            farmer_phone TEXT DEFAULT '',
            demo_product TEXT DEFAULT '',
            demo_date TEXT DEFAULT '',
            operator TEXT DEFAULT '',
            photo_before TEXT DEFAULT '',
            photo_after TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            deleted_at TEXT DEFAULT NULL
        )
    """)
    # 兼容旧表：自动添加缺失列
    for col, default in [
        ("deleted_at", "TEXT DEFAULT NULL"),
        ("plant_area", "TEXT DEFAULT ''"),
        ("demo_area", "TEXT DEFAULT ''"),
        ("gps_lng", "TEXT DEFAULT ''"),
        ("gps_lat", "TEXT DEFAULT ''"),
    ]:
        try:
            conn.execute(f"SELECT {col} FROM demo_fields LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute(f"ALTER TABLE demo_fields ADD COLUMN {col} {default}")
    # users 表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            phone TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            name TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    # 预设管理员账号
    admin = conn.execute("SELECT phone FROM users WHERE phone='admin'").fetchone()
    if not admin:
        pw_hash = hashlib.sha256("admin123".encode()).hexdigest()
        conn.execute("INSERT INTO users (phone, password_hash, name) VALUES (?, ?, ?)",
                     ("admin", pw_hash, "管理员"))
    # 预设游客账号
    guest = conn.execute("SELECT phone FROM users WHERE phone='guest'").fetchone()
    if not guest:
        pw_hash = hashlib.sha256("guest".encode()).hexdigest()
        conn.execute("INSERT INTO users (phone, password_hash, name) VALUES (?, ?, ?)",
                     ("guest", pw_hash, "游客"))
    conn.commit()
    conn.close()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()


def check_auth(handler):
    auth = handler.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        return _sessions.get(token)
    return None


# ── 飞书同步 ──
def get_feishu_token():
    global _token, _token_expire
    if _token and time.time() < _token_expire - 60:
        return _token
    r = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
    )
    data = r.json()
    _token = data["tenant_access_token"]
    _token_expire = time.time() + data["expire"]
    return _token


def sync_to_feishu(record: dict):
    if not FEISHU_ENABLED:
        return
    try:
        token = get_feishu_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        fields = {
            "乡镇": record.get("township", ""),
            "村": record.get("village", ""),
            "种植作物": record.get("crop", ""),
            "农户姓名": record.get("farmer_name", ""),
            "农户电话": record.get("farmer_phone", ""),
            "示范产品": record.get("demo_product", ""),
            "示范时间": record.get("demo_date", ""),
            "操作人": record.get("operator", ""),
        }
        requests.post(
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}/tables/{FEISHU_TABLE_ID}/records",
            headers=headers,
            json={"fields": fields},
        )
    except Exception as e:
        print(f"[feishu sync error] {e}")


# ── HTTP 处理 ──
def parse_multipart(handler):
    """解析 multipart/form-data 请求"""
    content_type = handler.headers.get("Content-Type", "")
    length = int(handler.headers.get("Content-Length", 0))
    body = handler.rfile.read(length)

    if "multipart/form-data" not in content_type:
        return json.loads(body.decode("utf-8", errors="replace")), {}

    # 手动解析 multipart
    boundary = content_type.split("boundary=")[1].strip()
    if boundary.startswith('"') and boundary.endswith('"'):
        boundary = boundary[1:-1]

    fields = {}
    files = {}
    parts = body.split(b"--" + boundary.encode())
    for part in parts:
        if part in (b"", b"--\r\n", b"--"):
            continue
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        # Split headers from body
        header_end = part.find(b"\r\n\r\n")
        if header_end == -1:
            continue
        header_data = part[:header_end].decode("utf-8", errors="replace")
        body_data = part[header_end + 4:]
        if body_data.endswith(b"\r\n"):
            body_data = body_data[:-2]

        # Parse Content-Disposition
        name = None
        filename = None
        for line in header_data.split("\r\n"):
            if "Content-Disposition" in line:
                for param in line.split(";"):
                    param = param.strip()
                    if param.startswith("name="):
                        name = param.split("=", 1)[1].strip('"')
                    elif param.startswith("filename="):
                        filename = param.split("=", 1)[1].strip('"')

        if name is None:
            continue
        if filename:
            class FileItem:
                pass
            fi = FileItem()
            fi.filename = filename
            fi.file = io.BytesIO(body_data)
            files[name] = fi
        else:
            fields[name] = body_data.decode("utf-8", errors="replace")

    return fields, files


def build_search_query(query_params):
    """构建搜索 SQL WHERE 子句"""
    conditions = ["deleted_at IS NULL"]
    params = []

    q = query_params.get("q", [None])[0]
    if q:
        conditions.append(
            "(township LIKE ? OR village LIKE ? OR farmer_name LIKE ? OR demo_product LIKE ? OR operator LIKE ?)"
        )
        like = f"%{q}%"
        params.extend([like] * 5)

    crop = query_params.get("crop", [None])[0]
    if crop:
        conditions.append("crop=?")
        params.append(crop)

    township = query_params.get("township", [None])[0]
    if township:
        conditions.append("township=?")
        params.append(township)

    product = query_params.get("product", [None])[0]
    if product:
        conditions.append("demo_product LIKE ?")
        params.append(f"%{product}%")

    date_from = query_params.get("date_from", [None])[0]
    if date_from:
        conditions.append("demo_date>=?")
        params.append(date_from)

    date_to = query_params.get("date_to", [None])[0]
    if date_to:
        conditions.append("demo_date<=?")
        params.append(date_to)

    operator = query_params.get("operator", [None])[0]
    if operator:
        conditions.append("operator LIKE ?")
        params.append(f"%{operator}%")

    where = " AND ".join(conditions)
    return where, params


def save_uploaded_file(file_item, prefix=""):
    """保存上传的文件，返回文件名"""
    ext = os.path.splitext(file_item.filename)[1] or ".jpg"
    filename = f"{prefix}{uuid.uuid4().hex[:12]}{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(file_item.file.read())
    return filename


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _json_response(self, data, status=200):
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/api/fields", "/api/trash", "/api/export"):
            if not check_auth(self):
                self._json_response({"ok": False, "error": "未登录"}, 401)
                return

        if path == "/api/fields":
            query = urllib.parse.parse_qs(parsed.query)
            where, params = build_search_query(query)
            conn = get_db()
            rows = conn.execute(
                f"SELECT * FROM demo_fields WHERE {where} ORDER BY created_at DESC", params
            ).fetchall()
            conn.close()
            self._json_response([dict(r) for r in rows])

        elif path == "/api/trash":
            conn = get_db()
            rows = conn.execute("SELECT * FROM demo_fields WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC").fetchall()
            conn.close()
            self._json_response([dict(r) for r in rows])

        elif path == "/api/export":
            query = urllib.parse.parse_qs(parsed.query)
            where, params = build_search_query(query)
            conn = get_db()
            rows = conn.execute(
                f"SELECT * FROM demo_fields WHERE {where} ORDER BY created_at DESC", params
            ).fetchall()
            conn.close()
            # CSV export
            import csv
            import io
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["ID", "乡镇", "村", "种植作物", "农户姓名", "农户电话", "示范产品", "示范时间", "操作人", "创建时间"])
            for r in rows:
                writer.writerow([r["id"], r["township"], r["village"], r["crop"],
                                r["farmer_name"], r["farmer_phone"], r["demo_product"],
                                r["demo_date"], r["operator"], r["created_at"]])
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "text/csv; charset=utf-8-sig")
            self.send_header("Content-Disposition", "attachment; filename=demo_fields.csv")
            self.end_headers()
            self.wfile.write(("\ufeff" + output.getvalue()).encode("utf-8"))

        else:
            super().do_GET()

    def end_headers(self):
        if hasattr(self, 'path') and self.path and (self.path.endswith('.html') or self.path == '/'):
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def do_POST(self):
        path = self.path.split("?")[0]

        if path == "/api/login":
            fields, _ = parse_multipart(self)
            phone = fields.get("phone", "")
            password = fields.get("password", "")
            if not phone or not password:
                self._json_response({"ok": False, "error": "请输入账号和密码"}, 400)
                return
            conn = get_db()
            user = conn.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
            conn.close()
            if not user or user["password_hash"] != hash_password(password):
                self._json_response({"ok": False, "error": "账号或密码错误"}, 401)
                return
            token = uuid.uuid4().hex
            role = "guest" if phone == "guest" else "admin"
            _sessions[token] = {"phone": phone, "name": user["name"], "role": role, "login_at": time.time()}
            self._json_response({"ok": True, "token": token, "name": user["name"], "role": role})
            return

        # 需要登录的接口
        session = check_auth(self)
        if not session:
            self._json_response({"ok": False, "error": "未登录"}, 401)
            return
        if session.get("role") == "guest":
            self._json_response({"ok": False, "error": "游客无权操作"}, 403)
            return

        if self.path.startswith("/api/trash/") and self.path.endswith("/restore"):
            # 从回收站恢复
            record_id = self.path.split("/api/trash/")[1].replace("/restore", "")
            conn = get_db()
            conn.execute("UPDATE demo_fields SET deleted_at=NULL, updated_at=datetime('now','localtime') WHERE id=?", (record_id,))
            conn.commit()
            conn.close()
            self._json_response({"ok": True})
            return
        if self.path == "/api/fields":
            fields, files = parse_multipart(self)
            record_id = uuid.uuid4().hex[:16]

            photo_before = ""
            if "photo_before" in files:
                photo_before = save_uploaded_file(files["photo_before"], "before_")
            # 多张施用后照片
            after_list = []
            for key in sorted(files.keys()):
                if key.startswith("photo_after"):
                    after_list.append(save_uploaded_file(files[key], "after_"))
            photo_after = json.dumps(after_list) if after_list else ""

            conn = get_db()
            conn.execute("""
                INSERT INTO demo_fields (id, township, village, crop, farmer_name, farmer_phone,
                    demo_product, demo_date, operator, photo_before, photo_after,
                    plant_area, demo_area, gps_lng, gps_lat)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record_id,
                fields.get("township", ""),
                fields.get("village", ""),
                fields.get("crop", ""),
                fields.get("farmer_name", ""),
                fields.get("farmer_phone", ""),
                fields.get("demo_product", ""),
                fields.get("demo_date", ""),
                fields.get("operator", ""),
                photo_before,
                photo_after,
                fields.get("plant_area", ""),
                fields.get("demo_area", ""),
                fields.get("gps_lng", ""),
                fields.get("gps_lat", ""),
            ))
            conn.commit()
            row = conn.execute("SELECT * FROM demo_fields WHERE id=?", (record_id,)).fetchone()
            conn.close()

            record = dict(row)
            sync_to_feishu(record)
            self._json_response({"ok": True, "record": record})
        else:
            self.send_response(404)
            self.end_headers()

    def do_PUT(self):
        session = check_auth(self)
        if not session:
            self._json_response({"ok": False, "error": "未登录"}, 401)
            return
        if session.get("role") == "guest":
            self._json_response({"ok": False, "error": "游客无权操作"}, 403)
            return
        # PUT /api/fields/{id}
        if self.path.startswith("/api/fields/"):
            record_id = self.path.split("/api/fields/")[1]
            fields, files = parse_multipart(self)

            conn = get_db()
            existing = conn.execute("SELECT * FROM demo_fields WHERE id=?", (record_id,)).fetchone()
            if not existing:
                conn.close()
                self._json_response({"ok": False, "error": "not found"}, 404)
                return

            updates = []
            params = []
            for col in ["township", "village", "crop", "farmer_name", "farmer_phone",
                        "demo_product", "demo_date", "operator", "plant_area", "demo_area"]:
                if col in fields:
                    updates.append(f"{col}=?")
                    params.append(fields[col])

            if "photo_before" in files:
                filename = save_uploaded_file(files["photo_before"], "before_")
                updates.append("photo_before=?")
                params.append(filename)
            # 施用后照片：保留指定的已有照片 + 新上传的
            keep_json = fields.get("keep_after_photos", "[]")
            try:
                keep_list = json.loads(keep_json)
            except (json.JSONDecodeError, TypeError):
                keep_list = []
            after_keys = [k for k in sorted(files.keys()) if k.startswith("photo_after")]
            new_files = [save_uploaded_file(files[k], "after_") for k in after_keys]
            if keep_list or new_files or "keep_after_photos" in fields:
                merged = (keep_list + new_files)[:9]
                updates.append("photo_after=?")
                params.append(json.dumps(merged))

            if updates:
                updates.append("updated_at=datetime('now','localtime')")
                params.append(record_id)
                conn.execute(f"UPDATE demo_fields SET {', '.join(updates)} WHERE id=?", params)
                conn.commit()

            row = conn.execute("SELECT * FROM demo_fields WHERE id=?", (record_id,)).fetchone()
            conn.close()
            self._json_response({"ok": True, "record": dict(row)})
        else:
            self.send_response(404)
            self.end_headers()

    def do_DELETE(self):
        session = check_auth(self)
        if not session:
            self._json_response({"ok": False, "error": "未登录"}, 401)
            return
        if session.get("role") == "guest":
            self._json_response({"ok": False, "error": "游客无权操作"}, 403)
            return
        if self.path.startswith("/api/trash/"):
            # 永久删除（先归档到日志文件）
            record_id = self.path.split("/api/trash/")[1]
            conn = get_db()
            row = conn.execute("SELECT * FROM demo_fields WHERE id=? AND deleted_at IS NOT NULL", (record_id,)).fetchone()
            if row:
                import datetime as _dt
                archive = dict(row)
                archive["permanently_deleted_at"] = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with open(ARCHIVE_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps(archive, ensure_ascii=False) + "\n")
                conn.execute("DELETE FROM demo_fields WHERE id=?", (record_id,))
                conn.commit()
            conn.close()
            self._json_response({"ok": True})
        elif self.path.startswith("/api/fields/"):
            # 软删除（移入回收站）
            record_id = self.path.split("/api/fields/")[1]
            conn = get_db()
            conn.execute("UPDATE demo_fields SET deleted_at=datetime('now','localtime') WHERE id=?", (record_id,))
            conn.commit()
            conn.close()
            self._json_response({"ok": True})
        else:
            self.send_response(404)
            self.end_headers()

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_message(self, fmt, *args):
        print(f"[demo-field] {self.client_address[0]} {args[0] if args else ''}")


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    init_db()
    port = 8091
    print(f"阿克苏地区示范田一览图服务启动: http://0.0.0.0:{port}/")
    ThreadedHTTPServer(("0.0.0.0", port), Handler).serve_forever()
