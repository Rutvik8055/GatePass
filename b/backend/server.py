#!/usr/bin/env python3
"""TourGuard AI zero-dependency application server.

It uses SQLite for durable relational storage and serves the responsive SPA from
frontend/. It intentionally relies only on Python's standard library so a new
operator can start a working demo without a package-install step.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import math
import mimetypes
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
DB_PATH = Path(os.getenv("TOURGUARD_DB", str(ROOT / "database" / "tourguard.db")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
HOST = os.getenv("TOURGUARD_HOST", "127.0.0.1")
PORT = int(os.getenv("TOURGUARD_PORT", "8080"))
SECRET = os.getenv("TOURGUARD_SECRET", "local-demo-secret-change-before-production").encode()
DEMO_MODE = os.getenv("TOURGUARD_DEMO_MODE", "true").lower() == "true"
LOCK = threading.RLock()
SUBSCRIBERS: set[queue.Queue] = set() if False else set()  # declared after import-safe setup
RATE_LIMITS: dict[str, deque] = defaultdict(deque)

# Queue is imported separately to make the type useful on supported Python versions.
import queue
SUBSCRIBERS: set[queue.Queue] = set()

VEHICLE_TYPES = {"Car", "Bike", "Bus", "Auto", "Van", "Other"}
ROLES = {"admin", "operator", "viewer"}
NUMBER_PATTERN = re.compile(r"^[A-Z0-9 -]{3,18}$")


def now() -> datetime:
    return datetime.now().astimezone()


def iso(value: datetime | None = None) -> str:
    return (value or now()).isoformat(timespec="seconds")


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def human_duration(start: str | None, end: str | None = None) -> str:
    initial = parse_time(start)
    final = parse_time(end) if end else now()
    if not initial or not final:
        return "—"
    minutes = max(0, int((final - initial).total_seconds() // 60))
    hours, mins = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts = ([f"{days}d"] if days else []) + ([f"{hours}h"] if hours else []) + [f"{mins}m"]
    return " ".join(parts)


def date_label(value: str | None) -> str:
    moment = parse_time(value)
    return moment.strftime("%d %b %Y") if moment else "—"


def time_label(value: str | None) -> str:
    moment = parse_time(value)
    return moment.strftime("%I:%M %p") if moment else "—"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 310000)
    return f"pbkdf2_sha256${salt}${base64.b64encode(derived).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$", 2)
        return hmac.compare_digest(password_hash(password, salt), stored)
    except ValueError:
        return False


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_token(user: dict) -> str:
    payload = {"sub": user["id"], "role": user["role"], "name": user["name"], "exp": int(time.time()) + 8 * 3600}
    body = b64(json.dumps(payload, separators=(",", ":")).encode())
    signature = b64(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())
    return f"{body}.{signature}"


def decode_token(token: str) -> dict | None:
    try:
        body, signature = token.split(".", 1)
        expected = b64(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(unb64(body))
        return payload if payload.get("exp", 0) > time.time() else None
    except (ValueError, json.JSONDecodeError, TypeError):
        return None


def bootstrap() -> None:
    with LOCK, connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
          password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','operator','viewer')),
          active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS gates (
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'Active', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS vehicles (
          id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT NOT NULL UNIQUE, vehicle_type TEXT NOT NULL,
          model TEXT, first_visit_at TEXT NOT NULL, last_visit_at TEXT NOT NULL, total_visits INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS visits (
          id INTEGER PRIMARY KEY AUTOINCREMENT, public_id TEXT NOT NULL UNIQUE, group_name TEXT NOT NULL,
          mobile TEXT, tourist_count INTEGER NOT NULL CHECK(tourist_count > 0), vehicle_id INTEGER NOT NULL,
          entry_gate TEXT NOT NULL, exit_gate TEXT, entry_operator TEXT NOT NULL, exit_operator TEXT,
          entered_at TEXT NOT NULL, exited_at TEXT, status TEXT NOT NULL CHECK(status IN ('INSIDE','EXITED')),
          qr_token TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          FOREIGN KEY(vehicle_id) REFERENCES vehicles(id)
        );
                CREATE TABLE IF NOT EXISTS visitor_passes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, visit_id INTEGER NOT NULL, visitor_number INTEGER NOT NULL CHECK(visitor_number > 0),
                    qr_token TEXT NOT NULL UNIQUE, status TEXT NOT NULL CHECK(status IN ('INSIDE','EXITED')),
                    exited_at TEXT, exit_gate TEXT, exit_operator TEXT, created_at TEXT NOT NULL,
                    UNIQUE(visit_id, visitor_number), FOREIGN KEY(visit_id) REFERENCES visits(id)
                );
        CREATE INDEX IF NOT EXISTS idx_vehicles_number ON vehicles(number);
        CREATE INDEX IF NOT EXISTS idx_visits_status ON visits(status);
        CREATE INDEX IF NOT EXISTS idx_visits_entered ON visits(entered_at);
        CREATE INDEX IF NOT EXISTS idx_visits_mobile ON visits(mobile);
                CREATE INDEX IF NOT EXISTS idx_visitor_passes_visit ON visitor_passes(visit_id, status);
        CREATE TABLE IF NOT EXISTS parking (
          vehicle_type TEXT PRIMARY KEY, capacity INTEGER NOT NULL CHECK(capacity >= 0), updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notifications (
          id INTEGER PRIMARY KEY AUTOINCREMENT, severity TEXT NOT NULL, title TEXT NOT NULL, message TEXT NOT NULL,
          read_at TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit_logs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL, action TEXT NOT NULL, entity TEXT NOT NULL,
          entity_id TEXT, detail TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
        """)
        if not db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            stamp = iso()
            for name, email, password, role in [
                ("Aarav Mehta", "admin@tourguard.local", "Admin@123", "admin"),
                ("Priya Nair", "gate@tourguard.local", "Gate@123", "operator"),
                ("Rohan Shah", "viewer@tourguard.local", "Viewer@123", "viewer"),
            ]:
                db.execute("INSERT INTO users(name,email,password_hash,role,created_at) VALUES(?,?,?,?,?)", (name, email, password_hash(password), role, stamp))
            for gate in ("Main Gate", "East Gate", "Museum Gate"):
                db.execute("INSERT INTO gates(name,created_at) VALUES(?,?)", (gate, stamp))
            for kind, capacity in [("Car", 100), ("Bike", 140), ("Bus", 20), ("Auto", 25), ("Van", 25), ("Other", 15)]:
                db.execute("INSERT INTO parking(vehicle_type,capacity,updated_at) VALUES(?,?,?)", (kind, capacity, stamp))
            db.execute("INSERT INTO settings(key,value,updated_at) VALUES('max_tourist_capacity','2000',?)", (stamp,))
            seed_data(db)
        unmigrated = db.execute("""SELECT v.* FROM visits v WHERE NOT EXISTS
            (SELECT 1 FROM visitor_passes p WHERE p.visit_id=v.id) ORDER BY v.id""").fetchall()
        for visit in unmigrated:
            for visitor_number in range(1, int(visit["tourist_count"]) + 1):
                token = visit["qr_token"] if visitor_number == 1 else secrets.token_urlsafe(14)
                db.execute("""INSERT INTO visitor_passes(visit_id,visitor_number,qr_token,status,exited_at,exit_gate,exit_operator,created_at)
                    VALUES(?,?,?,?,?,?,?,?)""", (visit["id"], visitor_number, token, visit["status"], visit["exited_at"], visit["exit_gate"], visit["exit_operator"], visit["created_at"]))
        db.commit()


def seed_data(db: sqlite3.Connection) -> None:
    """Clearly labelled demo history, generated as completed visits plus active records."""
    stamp = now()
    names = ["Mehta Family", "Coastal Explorers", "Heritage Walk Group", "Asha Travels", "Weekend Visitors", "Sunrise Tours"]
    prefixes = ["MH12AB", "MH14XY", "KA03MN", "GJ01RT", "DL08CP", "TN09QZ"]
    kinds = ["Car", "Bike", "Bus", "Auto", "Van", "Other"]
    for index in range(72):
        entered = stamp - timedelta(days=1 + (index % 48), hours=(index * 3) % 10 + 2, minutes=index * 7 % 55)
        exited = entered + timedelta(minutes=55 + (index * 19) % 260)
        number = f"{prefixes[index % len(prefixes)]}{1200 + index}"
        kind = kinds[index % len(kinds)]
        cursor = db.execute("INSERT INTO vehicles(number,vehicle_type,model,first_visit_at,last_visit_at,total_visits) VALUES(?,?,?,?,?,?)", (number, kind, ["Swift", "Activa", "Traveller", "Eeco", "City", "Tour Coach"][index % 6], iso(entered), iso(exited), 1))
        vehicle_id = cursor.lastrowid
        db.execute("""INSERT INTO visits(public_id,group_name,mobile,tourist_count,vehicle_id,entry_gate,exit_gate,entry_operator,exit_operator,entered_at,exited_at,status,qr_token,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (f"TG-{entered.year}-{index + 1001:06d}", names[index % len(names)], f"98{index:08d}", 1 + index % 7, vehicle_id, ["Main Gate", "East Gate", "Museum Gate"][index % 3], ["Main Gate", "East Gate"][index % 2], "Priya Nair", "Priya Nair", iso(entered), iso(exited), "EXITED", secrets.token_urlsafe(12), iso(entered), iso(exited)))
    for index, (number, kind, count) in enumerate([("MH12TG4242", "Car", 4), ("MH14AI0808", "Bike", 2), ("KA05BUS99", "Bus", 28), ("GJ01VAN77", "Van", 6), ("DL08CAR11", "Car", 3)]):
        entered = stamp - timedelta(minutes=35 + index * 42)
        vehicle_id = db.execute("INSERT INTO vehicles(number,vehicle_type,model,first_visit_at,last_visit_at,total_visits) VALUES(?,?,?,?,?,?)", (number, kind, None, iso(entered), iso(entered), 1)).lastrowid
        db.execute("""INSERT INTO visits(public_id,group_name,mobile,tourist_count,vehicle_id,entry_gate,entry_operator,entered_at,status,qr_token,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (f"TG-{stamp.year}-{2001 + index:06d}", ["Patel Family", "Morning Riders", "Konkan Tours", "Temple Group", "Sahara Visitors"][index], f"97{index:08d}", count, vehicle_id, "Main Gate", "Priya Nair", iso(entered), "INSIDE", secrets.token_urlsafe(12), iso(entered), iso(entered)))
    db.execute("INSERT INTO notifications(severity,title,message,created_at) VALUES(?,?,?,?)", ("info", "Demo workspace ready", "Demo records are installed. Start registering real entries when you deploy.", iso(stamp)))


def rows(items):
    return [dict(item) for item in items]


def log_action(db, actor: str, action: str, entity: str, entity_id: str | int | None = None, detail: str | None = None):
    db.execute("INSERT INTO audit_logs(actor,action,entity,entity_id,detail,created_at) VALUES(?,?,?,?,?,?)", (actor, action, entity, str(entity_id or ""), detail, iso()))


def broadcast(event: str, payload: dict):
    envelope = json.dumps({"event": event, "data": payload})
    for inbox in list(SUBSCRIBERS):
        try:
            inbox.put_nowait(envelope)
        except queue.Full:
            pass


def make_notification(db, severity: str, title: str, message: str):
    db.execute("INSERT INTO notifications(severity,title,message,created_at) VALUES(?,?,?,?)", (severity, title, message, iso()))


def visit_view(record: sqlite3.Row | dict) -> dict:
    visit = dict(record)
    visit["duration"] = human_duration(visit.get("entered_at"), visit.get("exited_at"))
    visit["in_date"] = date_label(visit.get("entered_at"))
    visit["in_time"] = time_label(visit.get("entered_at"))
    visit["out_date"] = date_label(visit.get("exited_at")) if visit.get("exited_at") else None
    visit["out_time"] = time_label(visit.get("exited_at")) if visit.get("exited_at") else None
    return visit


def query_visits(db: sqlite3.Connection, params: dict, inside_only: bool = False):
    where, values = [], []
    if inside_only:
        where.append("v.status='INSIDE'")
    elif params.get("status") in ("INSIDE", "EXITED"):
        where.append("v.status=?"); values.append(params["status"])
    term = params.get("search", "").strip()
    if term:
        needle = f"%{term.upper()}%"
        where.append("(UPPER(v.public_id) LIKE ? OR UPPER(ve.number) LIKE ? OR UPPER(v.group_name) LIKE ? OR UPPER(COALESCE(v.mobile,'')) LIKE ?)")
        values += [needle] * 4
    if params.get("vehicle_type") in VEHICLE_TYPES:
        where.append("ve.vehicle_type=?"); values.append(params["vehicle_type"])
    if params.get("gate"):
        where.append("v.entry_gate=?"); values.append(params["gate"])
    if params.get("date_from"):
        where.append("date(v.entered_at)>=date(?)"); values.append(params["date_from"])
    if params.get("date_to"):
        where.append("date(v.entered_at)<=date(?)"); values.append(params["date_to"])
    clause = " WHERE " + " AND ".join(where) if where else ""
    select = """SELECT v.*, ve.number AS vehicle_number, ve.vehicle_type, ve.model AS vehicle_model
              FROM visits v JOIN vehicles ve ON ve.id=v.vehicle_id"""
    total = db.execute("SELECT COUNT(*) FROM (" + select + clause + ")", values).fetchone()[0]
    page = max(1, int(params.get("page", 1) or 1)); limit = min(100, max(5, int(params.get("limit", 10) or 10)))
    data = db.execute(select + clause + " ORDER BY v.entered_at DESC LIMIT ? OFFSET ?", values + [limit, (page - 1) * limit]).fetchall()
    return {"items": [visit_view(item) for item in data], "total": total, "page": page, "limit": limit, "pages": max(1, math.ceil(total / limit))}


def occupancy(db: sqlite3.Connection):
    counts = {row["vehicle_type"]: row["count"] for row in db.execute("""SELECT ve.vehicle_type, COUNT(*) AS count FROM visits v JOIN vehicles ve ON v.vehicle_id=ve.id WHERE v.status='INSIDE' GROUP BY ve.vehicle_type""")}
    return [{"vehicle_type": row["vehicle_type"], "capacity": row["capacity"], "occupied": counts.get(row["vehicle_type"], 0), "percent": round((counts.get(row["vehicle_type"], 0) / row["capacity"] * 100) if row["capacity"] else 0)} for row in db.execute("SELECT * FROM parking ORDER BY CASE vehicle_type WHEN 'Car' THEN 1 WHEN 'Bike' THEN 2 WHEN 'Bus' THEN 3 WHEN 'Auto' THEN 4 WHEN 'Van' THEN 5 ELSE 6 END")]


def dashboard(db: sqlite3.Connection):
    local_today = now().date().isoformat()
    total_today = db.execute("SELECT COALESCE(SUM(tourist_count),0) FROM visits WHERE date(entered_at)=date(?)", (local_today,)).fetchone()[0]
    inside_people = db.execute("SELECT COALESCE(SUM(tourist_count),0) FROM visits WHERE status='INSIDE'").fetchone()[0]
    exited_today = db.execute("SELECT COALESCE(SUM(tourist_count),0) FROM visits WHERE status='EXITED' AND date(exited_at)=date(?)", (local_today,)).fetchone()[0]
    active_vehicle_count = db.execute("SELECT COUNT(*) FROM visits WHERE status='INSIDE'").fetchone()[0]
    total_vehicle_today = db.execute("SELECT COUNT(*) FROM visits WHERE date(entered_at)=date(?)", (local_today,)).fetchone()[0]
    types = {r["vehicle_type"]: r["count"] for r in db.execute("SELECT ve.vehicle_type, COUNT(*) AS count FROM visits v JOIN vehicles ve ON v.vehicle_id=ve.id WHERE v.status='INSIDE' GROUP BY ve.vehicle_type")}
    maximum = int(db.execute("SELECT value FROM settings WHERE key='max_tourist_capacity'").fetchone()[0])
    ratio = round(inside_people / maximum * 100) if maximum else 0
    level = "CRITICAL" if ratio >= 100 else "HIGH" if ratio >= 90 else "MEDIUM" if ratio >= 80 else "LOW"
    recent = db.execute("""SELECT v.*, ve.number AS vehicle_number, ve.vehicle_type FROM visits v JOIN vehicles ve ON ve.id=v.vehicle_id ORDER BY COALESCE(v.exited_at,v.entered_at) DESC LIMIT 8""").fetchall()
    return {"demo_mode": DEMO_MODE, "generated_at": iso(), "metrics": {"total_visitors_today": total_today, "currently_inside": inside_people, "exited_today": exited_today, "total_vehicles_today": total_vehicle_today, "vehicles_inside": active_vehicle_count, "cars_inside": types.get("Car", 0), "bikes_inside": types.get("Bike", 0), "buses_inside": types.get("Bus", 0), "other_vehicles_inside": sum(count for kind, count in types.items() if kind not in ("Car", "Bike", "Bus")), "max_capacity": maximum, "occupancy_percent": ratio, "crowd_level": level}, "parking": occupancy(db), "activity": [dict(id=item["id"], status=item["status"], vehicle_number=item["vehicle_number"], group_name=item["group_name"], at=item["exited_at"] or item["entered_at"]) for item in recent]}


def analytics(db: sqlite3.Connection):
    hourly = {str(hour).zfill(2): 0 for hour in range(8, 20)}
    daily = {str((now() - timedelta(days=n)).date()): 0 for n in reversed(range(7))}
    monthly = {str((now().replace(day=1) - timedelta(days=31 * n)).strftime("%Y-%m")): 0 for n in reversed(range(6))}
    distribution = {kind: 0 for kind in ["Car", "Bike", "Bus", "Auto", "Van", "Other"]}
    for row in db.execute("SELECT v.entered_at, v.tourist_count, ve.vehicle_type FROM visits v JOIN vehicles ve ON ve.id=v.vehicle_id"):
        moment = parse_time(row["entered_at"])
        if not moment: continue
        hour = f"{moment.hour:02d}"
        if hour in hourly: hourly[hour] += row["tourist_count"]
        day = str(moment.date())
        if day in daily: daily[day] += row["tourist_count"]
        month = moment.strftime("%Y-%m")
        if month in monthly: monthly[month] += row["tourist_count"]
        distribution[row["vehicle_type"]] = distribution.get(row["vehicle_type"], 0) + 1
    return {"hourly": [{"label": f"{int(k):02d}:00", "value": v} for k, v in hourly.items()], "daily": [{"label": k[5:], "value": v} for k, v in daily.items()], "monthly": [{"label": k, "value": v} for k, v in monthly.items()], "vehicles": [{"label": k, "value": v} for k, v in distribution.items()]}


class Handler(BaseHTTPRequestHandler):
    server_version = "TourGuard/1.0"

    def log_message(self, fmt, *args):
        return

    def json(self, status: int, data: dict | list):
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers(); self.wfile.write(body)

    def fail(self, status: int, message: str, code: str = "request_error"):
        self.json(status, {"error": {"code": code, "message": message}})

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1_000_000: raise ValueError
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self.fail(400, "Please send a valid request.", "invalid_json")
            return None

    def token_user(self):
        token = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        return decode_token(token)

    def require(self, *roles):
        user = self.token_user()
        if not user:
            self.fail(401, "Please sign in to continue.", "unauthorized"); return None
        if roles and user.get("role") not in roles:
            self.fail(403, "Your role does not have permission for this action.", "forbidden"); return None
        return user

    def path_parts(self):
        parsed = urlparse(self.path)
        return parsed.path, {key: values[-1] for key, values in parse_qs(parsed.query).items()}

    def static(self, request_path):
        path = "index.html" if request_path in ("/", "") else request_path.lstrip("/")
        target = (FRONTEND / path).resolve()
        if not str(target).startswith(str(FRONTEND.resolve())) or not target.is_file():
            self.send_error(404); return
        body = target.read_bytes(); content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(200); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(body))); self.send_header("X-Content-Type-Options", "nosniff"); self.end_headers(); self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204); self.send_header("Access-Control-Allow-Origin", "same-origin"); self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type"); self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS"); self.end_headers()

    def do_GET(self):
        path, params = self.path_parts()
        if not path.startswith("/api/"):
            return self.static(path)
        if path == "/api/health": return self.json(200, {"status": "ok", "database": "connected", "demo_mode": DEMO_MODE})
        if path == "/api/stream": return self.stream(params)
        user = self.require()
        if not user: return
        with LOCK, connect() as db:
            if path == "/api/auth/me": return self.json(200, {"user": user, "demo_mode": DEMO_MODE})
            if path == "/api/dashboard": return self.json(200, dashboard(db))
            if path == "/api/visits": return self.json(200, query_visits(db, params))
            if path == "/api/visits/inside": return self.json(200, query_visits(db, params, True))
            if path.startswith("/api/visits/lookup/"):
                key = unquote(path.rsplit("/", 1)[-1]).strip()
                record = db.execute("""SELECT v.*, ve.number AS vehicle_number, ve.vehicle_type, ve.model AS vehicle_model FROM visits v JOIN vehicles ve ON ve.id=v.vehicle_id WHERE v.status='INSIDE' AND (UPPER(v.public_id)=UPPER(?) OR UPPER(ve.number)=UPPER(?) OR v.mobile=? OR v.qr_token=?) ORDER BY v.entered_at DESC LIMIT 1""", (key, key, key, key)).fetchone()
                return self.json(200, {"item": visit_view(record) if record else None})
            if path.startswith("/api/vehicles/"):
                number = unquote(path.rsplit("/", 1)[-1]).upper()
                vehicle = db.execute("SELECT * FROM vehicles WHERE number=?", (number,)).fetchone()
                if not vehicle: return self.fail(404, "Vehicle not found.", "not_found")
                visits = db.execute("""SELECT v.*, ve.number AS vehicle_number, ve.vehicle_type FROM visits v JOIN vehicles ve ON ve.id=v.vehicle_id WHERE v.vehicle_id=? ORDER BY v.entered_at DESC""", (vehicle["id"],)).fetchall()
                result = dict(vehicle); result["current_status"] = "INSIDE" if any(v["status"] == "INSIDE" for v in visits) else "OUTSIDE"; result["visits"] = [visit_view(v) for v in visits]
                return self.json(200, result)
            if path == "/api/vehicles":
                items = db.execute("""SELECT ve.*, CASE WHEN EXISTS(SELECT 1 FROM visits v WHERE v.vehicle_id=ve.id AND v.status='INSIDE') THEN 'INSIDE' ELSE 'OUTSIDE' END AS current_status FROM vehicles ve ORDER BY ve.last_visit_at DESC LIMIT 250""").fetchall()
                return self.json(200, {"items": rows(items)})
            if path == "/api/parking": return self.json(200, {"items": occupancy(db)})
            if path == "/api/analytics": return self.json(200, analytics(db))
            if path == "/api/ai/insights": return self.json(200, self.insights(db))
            if path == "/api/reports": return self.json(200, self.report(db, params))
            if path == "/api/notifications": return self.json(200, {"items": rows(db.execute("SELECT * FROM notifications ORDER BY created_at DESC LIMIT 100").fetchall())})
            if path == "/api/users":
                if user["role"] != "admin": return self.fail(403, "Only administrators can manage users.", "forbidden")
                return self.json(200, {"items": rows(db.execute("SELECT id,name,email,role,active,created_at FROM users ORDER BY name").fetchall())})
            if path == "/api/gates": return self.json(200, {"items": rows(db.execute("SELECT * FROM gates ORDER BY name").fetchall())})
            if path == "/api/settings":
                if user["role"] != "admin": return self.fail(403, "Only administrators can view settings.", "forbidden")
                return self.json(200, {row["key"]: row["value"] for row in db.execute("SELECT * FROM settings")})
        return self.fail(404, "This endpoint does not exist.", "not_found")

    def do_POST(self):
        path, _ = self.path_parts()
        payload = self.read_json()
        if payload is None: return
        if path == "/api/auth/login": return self.login(payload)
        user = self.require()
        if not user: return
        with LOCK, connect() as db:
            if path == "/api/visits/entry": return self.entry(db, user, payload)
            if path.startswith("/api/visits/") and path.endswith("/exit"):
                return self.exit_visit(db, user, int(path.split("/")[3]), payload)
            if path == "/api/users": return self.add_user(db, user, payload)
            if path == "/api/gates": return self.add_gate(db, user, payload)
            if path.startswith("/api/notifications/") and path.endswith("/read"):
                db.execute("UPDATE notifications SET read_at=? WHERE id=?", (iso(), int(path.split("/")[3]))); db.commit(); return self.json(200, {"ok": True})
        return self.fail(404, "This endpoint does not exist.", "not_found")

    def do_PUT(self):
        path, _ = self.path_parts(); payload = self.read_json()
        if payload is None: return
        user = self.require("admin")
        if not user: return
        with LOCK, connect() as db:
            if path == "/api/parking":
                capacities = payload.get("capacities", {})
                for kind, capacity in capacities.items():
                    if kind in VEHICLE_TYPES and isinstance(capacity, int) and capacity >= 0:
                        db.execute("UPDATE parking SET capacity=?,updated_at=? WHERE vehicle_type=?", (capacity, iso(), kind))
                log_action(db, user["name"], "updated", "parking", detail="Updated parking capacities"); db.commit(); broadcast("refresh", {"source": "parking"}); return self.json(200, {"items": occupancy(db)})
            if path == "/api/settings":
                capacity = payload.get("max_tourist_capacity")
                if not isinstance(capacity, int) or not 1 <= capacity <= 1_000_000: return self.fail(422, "Capacity must be a positive whole number.", "validation_error")
                db.execute("UPDATE settings SET value=?,updated_at=? WHERE key='max_tourist_capacity'", (str(capacity), iso()))
                log_action(db, user["name"], "updated", "settings", detail="Updated tourist capacity"); db.commit(); broadcast("refresh", {"source": "settings"}); return self.json(200, {"max_tourist_capacity": str(capacity)})
        return self.fail(404, "This endpoint does not exist.", "not_found")

    def login(self, payload):
        ip = self.client_address[0]; recent = RATE_LIMITS[ip]; current = time.time()
        while recent and recent[0] < current - 60: recent.popleft()
        if len(recent) >= 10: return self.fail(429, "Too many sign-in attempts. Please wait a minute.", "rate_limited")
        email = str(payload.get("email", "")).lower().strip(); password = str(payload.get("password", ""))
        with LOCK, connect() as db:
            person = db.execute("SELECT * FROM users WHERE email=? AND active=1", (email,)).fetchone()
            if not person or not verify_password(password, person["password_hash"]):
                recent.append(current); return self.fail(401, "Email or password is incorrect.", "invalid_credentials")
            user = {"id": person["id"], "name": person["name"], "email": person["email"], "role": person["role"]}
            log_action(db, person["name"], "logged_in", "session"); db.commit()
        return self.json(200, {"token": create_token(user), "user": user, "demo_mode": DEMO_MODE})

    def entry(self, db, user, payload):
        if user["role"] not in ("admin", "operator"): return self.fail(403, "Your role cannot register entries.", "forbidden")
        group = str(payload.get("group_name", "")).strip(); mobile = str(payload.get("mobile", "")).strip(); kind = str(payload.get("vehicle_type", "")); number = re.sub(r"\s+", "", str(payload.get("vehicle_number", "")).upper()); model = str(payload.get("vehicle_model", "")).strip() or None; gate = str(payload.get("entry_gate", "")).strip()
        try: count = int(payload.get("tourist_count", 0))
        except (ValueError, TypeError): count = 0
        errors = {}
        if len(group) < 2: errors["group_name"] = "Enter a tourist or group name."
        if kind not in VEHICLE_TYPES: errors["vehicle_type"] = "Choose a vehicle type."
        if not NUMBER_PATTERN.match(number): errors["vehicle_number"] = "Use 3–18 letters, numbers, spaces or hyphens."
        if not 1 <= count <= 500: errors["tourist_count"] = "Tourist count must be between 1 and 500."
        if not db.execute("SELECT 1 FROM gates WHERE name=? AND status='Active'", (gate,)).fetchone(): errors["entry_gate"] = "Choose an active entry gate."
        if errors: return self.json(422, {"error": {"code": "validation_error", "message": "Please fix the highlighted fields.", "fields": errors}})
        active = db.execute("SELECT v.public_id FROM visits v JOIN vehicles ve ON ve.id=v.vehicle_id WHERE ve.number=? AND v.status='INSIDE'", (number,)).fetchone()
        if active:
            make_notification(db, "warning", "Duplicate active vehicle", f"{number} attempted another entry while {active['public_id']} is still inside."); db.commit(); return self.fail(409, f"{number} already has an active entry ({active['public_id']}). An administrator must resolve it first.", "duplicate_active_vehicle")
        stamp = iso(); vehicle = db.execute("SELECT * FROM vehicles WHERE number=?", (number,)).fetchone()
        if vehicle:
            vehicle_id = vehicle["id"]; db.execute("UPDATE vehicles SET vehicle_type=?,model=COALESCE(?,model),last_visit_at=?,total_visits=total_visits+1 WHERE id=?", (kind, model, stamp, vehicle_id))
        else:
            vehicle_id = db.execute("INSERT INTO vehicles(number,vehicle_type,model,first_visit_at,last_visit_at,total_visits) VALUES(?,?,?,?,?,1)", (number, kind, model, stamp, stamp)).lastrowid
        sequence = db.execute("SELECT COUNT(*) FROM visits WHERE strftime('%Y',entered_at)=?", (str(now().year),)).fetchone()[0] + 1
        public_id = f"TG-{now().year}-{sequence:06d}"; qr = secrets.token_urlsafe(14)
        visit_id = db.execute("""INSERT INTO visits(public_id,group_name,mobile,tourist_count,vehicle_id,entry_gate,entry_operator,entered_at,status,qr_token,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (public_id, group, mobile or None, count, vehicle_id, gate, user["name"], stamp, "INSIDE", qr, stamp, stamp)).lastrowid
        log_action(db, user["name"], "created", "visit", public_id, f"Entry: {number}"); self.crowd_alerts(db); db.commit()
        record = db.execute("SELECT v.*,ve.number AS vehicle_number,ve.vehicle_type,ve.model AS vehicle_model FROM visits v JOIN vehicles ve ON ve.id=v.vehicle_id WHERE v.id=?", (visit_id,)).fetchone()
        result = visit_view(record); broadcast("refresh", {"source": "entry", "visit": result}); return self.json(201, {"message": f"Entry registered for {number}.", "item": result, "qr_payload": qr})

    def exit_visit(self, db, user, visit_id, payload):
        if user["role"] not in ("admin", "operator"): return self.fail(403, "Your role cannot register exits.", "forbidden")
        gate = str(payload.get("exit_gate", "")).strip()
        if not db.execute("SELECT 1 FROM gates WHERE name=? AND status='Active'", (gate,)).fetchone(): return self.fail(422, "Choose an active exit gate.", "validation_error")
        visit = db.execute("SELECT * FROM visits WHERE id=?", (visit_id,)).fetchone()
        if not visit: return self.fail(404, "Visit not found.", "not_found")
        if visit["status"] == "EXITED": return self.fail(409, "This visit has already been exited.", "already_exited")
        stamp = iso(); db.execute("UPDATE visits SET status='EXITED',exited_at=?,exit_gate=?,exit_operator=?,updated_at=? WHERE id=?", (stamp, gate, user["name"], stamp, visit_id)); log_action(db, user["name"], "exited", "visit", visit["public_id"]); db.commit()
        record = db.execute("SELECT v.*,ve.number AS vehicle_number,ve.vehicle_type FROM visits v JOIN vehicles ve ON ve.id=v.vehicle_id WHERE v.id=?", (visit_id,)).fetchone(); result = visit_view(record); broadcast("refresh", {"source": "exit", "visit": result}); return self.json(200, {"message": f"Exit registered. Visit duration: {result['duration']}.", "item": result})

    def crowd_alerts(self, db):
        metrics = dashboard(db)["metrics"]
        ratio = metrics["occupancy_percent"]
        if ratio >= 100: make_notification(db, "critical", "Maximum capacity reached", "No additional tourist entries should be accepted until capacity reduces.")
        elif ratio >= 90: make_notification(db, "warning", "High crowd warning", f"Current occupancy is {ratio}%.")
        elif ratio >= 80: make_notification(db, "info", "Crowd is increasing", f"Current occupancy is {ratio}%.")
        for park in occupancy(db):
            if park["capacity"] and park["occupied"] >= park["capacity"]:
                make_notification(db, "warning", f"{park['vehicle_type']} parking full", "Direct incoming vehicles to the alternate parking area.")

    def insights(self, db):
        count = db.execute("SELECT COUNT(*) FROM visits WHERE status='EXITED'").fetchone()[0]
        if count < 15: return self.json(200, {"ready": False, "message": "Not enough historical data for reliable prediction.", "insights": []})
        stats = analytics(db); peak = max(stats["hourly"], key=lambda item: item["value"]); busiest = max(stats["daily"], key=lambda item: item["value"])
        totals = sum(x["value"] for x in stats["daily"]); expected = round(totals / max(1, len(stats["daily"])) * 1.08)
        top_vehicle = max(stats["vehicles"], key=lambda item: item["value"])
        level = "HIGH" if expected > 1200 else "MEDIUM" if expected > 500 else "LOW"
        return self.json(200, {"ready": True, "prediction": {"expected_visitors": expected, "peak_time": f"{peak['label']} – {int(peak['label'][:2])+2:02d}:00", "crowd_level": level, "parking_demand": "HIGH" if top_vehicle["value"] > 25 else "MEDIUM"}, "insights": [f"The busiest tracked hour is {peak['label']}, based on recorded entries.", f"{top_vehicle['label']} is the most common vehicle type in the current dataset.", f"The strongest day in the past week was {busiest['label']}, with {busiest['value']} recorded visitors."]})

    def report(self, db, params):
        start = params.get("date_from") or str(now().date()); end = params.get("date_to") or str(now().date())
        where = "date(entered_at) BETWEEN date(?) AND date(?)"; values = [start, end]
        total_visitors = db.execute(f"SELECT COALESCE(SUM(tourist_count),0) FROM visits WHERE {where}", values).fetchone()[0]
        total_vehicles = db.execute(f"SELECT COUNT(*) FROM visits WHERE {where}", values).fetchone()[0]
        total_exits = db.execute("SELECT COUNT(*) FROM visits WHERE date(exited_at) BETWEEN date(?) AND date(?)", values).fetchone()[0]
        mix = rows(db.execute("""SELECT ve.vehicle_type,COUNT(*) AS count FROM visits v JOIN vehicles ve ON ve.id=v.vehicle_id WHERE date(v.entered_at) BETWEEN date(?) AND date(?) GROUP BY ve.vehicle_type""", values).fetchall())
        hours = db.execute("SELECT strftime('%H',entered_at) AS hour,COUNT(*) AS count FROM visits WHERE " + where + " GROUP BY hour ORDER BY count DESC LIMIT 1", values).fetchone()
        return {"range": {"from": start, "to": end}, "total_visitors": total_visitors, "total_vehicles": total_vehicles, "total_entries": total_vehicles, "total_exits": total_exits, "currently_inside": db.execute("SELECT COUNT(*) FROM visits WHERE status='INSIDE'").fetchone()[0], "vehicle_distribution": mix, "peak_time": f"{hours['hour']}:00" if hours else "—"}

    def add_user(self, db, user, payload):
        if user["role"] != "admin": return self.fail(403, "Only administrators can create users.", "forbidden")
        name, email, password, role = (str(payload.get(k, "")).strip() for k in ("name", "email", "password", "role"))
        if len(name) < 2 or "@" not in email or len(password) < 8 or role not in ROLES: return self.fail(422, "Enter a name, valid email, password of 8+ characters and role.", "validation_error")
        try:
            new_id = db.execute("INSERT INTO users(name,email,password_hash,role,created_at) VALUES(?,?,?,?,?)", (name, email.lower(), password_hash(password), role, iso())).lastrowid; log_action(db, user["name"], "created", "user", new_id); db.commit(); return self.json(201, {"message": "User added."})
        except sqlite3.IntegrityError: return self.fail(409, "That email address already belongs to a user.", "duplicate_user")

    def add_gate(self, db, user, payload):
        if user["role"] != "admin": return self.fail(403, "Only administrators can add gates.", "forbidden")
        name = str(payload.get("name", "")).strip()
        if len(name) < 3: return self.fail(422, "Gate name must have at least 3 characters.", "validation_error")
        try:
            db.execute("INSERT INTO gates(name,created_at) VALUES(?,?)", (name, iso())); log_action(db, user["name"], "created", "gate", name); db.commit(); return self.json(201, {"message": "Gate added."})
        except sqlite3.IntegrityError: return self.fail(409, "A gate with this name already exists.", "duplicate_gate")

    def stream(self, params):
        user = decode_token(params.get("token", ""))
        if not user: return self.fail(401, "Please sign in to continue.", "unauthorized")
        inbox = queue.Queue(maxsize=20); SUBSCRIBERS.add(inbox)
        try:
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Cache-Control", "no-cache"); self.send_header("Connection", "keep-alive"); self.end_headers(); self.wfile.write(b"retry: 4000\n\n"); self.wfile.flush()
            while True:
                try: event = inbox.get(timeout=20); self.wfile.write(f"data: {event}\n\n".encode())
                except queue.Empty: self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            SUBSCRIBERS.discard(inbox)


if __name__ == "__main__":
    bootstrap()
    print(f"TourGuard AI running at http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
