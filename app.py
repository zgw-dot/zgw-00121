import os
import csv
import io
import secrets
import sqlite3
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import (
    Flask, request, jsonify, g, send_file,
    render_template_string, make_response, session
)

APP_DIR = Path(__file__).parent
DATA_DIR = APP_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "shift_diff.db"

ROLE_CASHIER = "cashier"
ROLE_MANAGER = "manager"
ROLE_ADMIN = "admin"

STATUS_DRAFT = "draft"
STATUS_PENDING = "pending"
STATUS_REVIEWED = "reviewed"
STATUS_RETURNED = "returned"
STATUS_CLOSED = "closed"
STATUS_REVOKED = "revoked"

DISP_UNPROCESSED = "unprocessed"
DISP_CONFIRMED = "confirmed"
DISP_FOLLOW_UP = "follow_up"
DISP_IGNORED = "ignored"

DISP_STATUS_LABELS = {
    DISP_UNPROCESSED: "未处理",
    DISP_CONFIRMED: "已确认",
    DISP_FOLLOW_UP: "需跟进",
    DISP_IGNORED: "已忽略",
}

DEFAULT_USERS = [
    {"username": "admin",   "password": "admin123",  "role": ROLE_ADMIN,   "display_name": "系统管理员"},
    {"username": "manager", "password": "manager123","role": ROLE_MANAGER, "display_name": "值班长"},
    {"username": "cashier", "password": "cashier123","role": ROLE_CASHIER, "display_name": "收银员小张"},
    {"username": "cashier2","password": "cashier123","role": ROLE_CASHIER, "display_name": "收银员小李"},
]

app = Flask(__name__, static_folder="static")
app.config["SECRET_KEY"] = "shift-diff-app-secret-key-change-me"
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


# ---------- DB helpers ---------- #

def get_db():
    if "db" not in g:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def add_operation_log(cur, action, operator, operator_role, detail=None):
    cur.execute("""
        INSERT INTO operation_log (action, operator, operator_role, detail)
        VALUES (?, ?, ?, ?)
    """, (action, operator, operator_role, detail))


def init_db():
    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        password TEXT NOT NULL,
        role TEXT NOT NULL,
        display_name TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    );

    CREATE TABLE IF NOT EXISTS vouchers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        voucher_no TEXT UNIQUE NOT NULL,
        shift_code TEXT NOT NULL,
        shift_date TEXT NOT NULL,
        cashier TEXT NOT NULL,
        diff_amount REAL NOT NULL DEFAULT 0,
        reason TEXT,
        remark TEXT,
        status TEXT NOT NULL DEFAULT 'draft',
        current_handler TEXT,
        created_by TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT DEFAULT (datetime('now','localtime')),
        return_note TEXT,
        closed_note TEXT,
        reviewed_by TEXT,
        closed_by TEXT,
        revoked_by TEXT,
        revoked_at TEXT,
        parent_voucher_no TEXT,
        import_source TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_vouchers_shift ON vouchers(shift_code);
    CREATE INDEX IF NOT EXISTS idx_vouchers_status ON vouchers(status);
    CREATE INDEX IF NOT EXISTS idx_vouchers_cashier ON vouchers(cashier);

    CREATE TABLE IF NOT EXISTS timeline (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        voucher_no TEXT NOT NULL,
        action TEXT NOT NULL,
        actor TEXT NOT NULL,
        actor_role TEXT,
        detail TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    );
    CREATE INDEX IF NOT EXISTS idx_timeline_voucher ON timeline(voucher_no);

    CREATE TABLE IF NOT EXISTS shift_codes (
        code TEXT PRIMARY KEY,
        description TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    );

    CREATE TABLE IF NOT EXISTS import_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT,
        total_count INTEGER,
        success_count INTEGER,
        failed_count INTEGER,
        error_detail TEXT,
        imported_by TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    );

    CREATE TABLE IF NOT EXISTS alert_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        rule_type TEXT NOT NULL,
        threshold REAL NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        description TEXT,
        created_by TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT DEFAULT (datetime('now','localtime'))
    );

    CREATE TABLE IF NOT EXISTS alert_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        voucher_no TEXT NOT NULL,
        voucher_id INTEGER,
        rule_id INTEGER,
        rule_name TEXT NOT NULL,
        rule_type TEXT NOT NULL,
        alert_reason TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        disposition_status TEXT NOT NULL DEFAULT 'unprocessed',
        disposition_note TEXT,
        disposition_handler TEXT,
        disposition_time TEXT,
        disposition_version INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_alert_logs_voucher ON alert_logs(voucher_no);
    CREATE INDEX IF NOT EXISTS idx_alert_logs_rule ON alert_logs(rule_id);
    CREATE INDEX IF NOT EXISTS idx_alert_logs_disp ON alert_logs(disposition_status);

    CREATE TABLE IF NOT EXISTS operation_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action TEXT NOT NULL,
        operator TEXT NOT NULL,
        operator_role TEXT,
        detail TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    );
    CREATE INDEX IF NOT EXISTS idx_oplog_action ON operation_log(action);
    """)

    cur.execute("PRAGMA table_info(alert_logs)")
    existing_cols = {row[1] for row in cur.fetchall()}
    migration_cols = [
        ("disposition_status", "TEXT NOT NULL DEFAULT 'unprocessed'"),
        ("disposition_note", "TEXT"),
        ("disposition_handler", "TEXT"),
        ("disposition_time", "TEXT"),
        ("disposition_version", "INTEGER NOT NULL DEFAULT 0"),
    ]
    for col_name, col_def in migration_cols:
        if col_name not in existing_cols:
            cur.execute(f"ALTER TABLE alert_logs ADD COLUMN {col_name} {col_def}")
    if "disposition_status" in existing_cols:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_alert_logs_disp ON alert_logs(disposition_status)")

    for u in DEFAULT_USERS:
        cur.execute("""
            INSERT OR IGNORE INTO users (username, password, role, display_name)
            VALUES (?, ?, ?, ?)
        """, (u["username"], u["password"], u["role"], u["display_name"]))

    default_shifts = [
        ("早班", "06:00-14:00"),
        ("中班", "14:00-22:00"),
        ("晚班", "22:00-06:00"),
    ]
    for code, desc in default_shifts:
        cur.execute("INSERT OR IGNORE INTO shift_codes (code, description) VALUES (?, ?)",
                    (code, desc))

    con.commit()
    con.close()


# ---------- Auth helpers ---------- #

def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        user = session.get("user")
        if not user:
            return jsonify({"error": "未登录，请先登录"}), 401
        return fn(*a, **kw)
    return wrapper


def role_required(*roles):
    def deco(fn):
        @wraps(fn)
        @login_required
        def wrapper(*a, **kw):
            user = session.get("user")
            if user["role"] not in roles:
                return jsonify({"error": "无权限执行该操作"}), 403
            return fn(*a, **kw)
        return wrapper
    return deco


def current_user():
    return session.get("user")


def add_timeline(cur, voucher_no, action, actor, role, detail=None):
    cur.execute("""
        INSERT INTO timeline (voucher_no, action, actor, actor_role, detail)
        VALUES (?, ?, ?, ?, ?)
    """, (voucher_no, action, actor, role, detail))


def row_to_dict(row):
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


# ---------- HTML ---------- #

@app.route("/")
def index():
    user = session.get("user")
    with open(APP_DIR / "static" / "index.html", "r", encoding="utf-8") as f:
        html = f.read()
    return render_template_string(html, user=user)


# ---------- Auth API ---------- #

@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(force=True, silent=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "")
    if not username or not password:
        return jsonify({"error": "用户名和密码不能为空"}), 400
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if not row or row["password"] != password:
        return jsonify({"error": "用户名或密码错误"}), 401
    user = {
        "username": row["username"],
        "role": row["role"],
        "display_name": row["display_name"] or row["username"]
    }
    session["user"] = user
    return jsonify({"user": user})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/me")
@login_required
def api_me():
    return jsonify({"user": current_user()})


@app.route("/api/users")
@login_required
def api_users():
    db = get_db()
    rows = db.execute("SELECT username, role, display_name FROM users ORDER BY username").fetchall()
    return jsonify({"users": [row_to_dict(r) for r in rows]})


# ---------- Shift / Codes ---------- #

@app.route("/api/shifts")
@login_required
def api_shifts():
    db = get_db()
    rows = db.execute("SELECT code, description FROM shift_codes ORDER BY code").fetchall()
    return jsonify({"shifts": [row_to_dict(r) for r in rows]})


@app.route("/api/shifts", methods=["POST"])
@role_required(ROLE_ADMIN, ROLE_MANAGER)
def api_add_shift():
    data = request.get_json(force=True, silent=True) or {}
    code = (data.get("code") or "").strip()
    description = (data.get("description") or "").strip()
    if not code:
        return jsonify({"error": "班次代码不能为空"}), 400
    db = get_db()
    try:
        db.execute("INSERT INTO shift_codes (code, description) VALUES (?, ?)", (code, description))
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "班次代码已存在"}), 400
    return jsonify({"ok": True})


# ---------- Vouchers ---------- #

def validate_voucher_payload(data, is_import=False):
    errors = []
    voucher_no = (data.get("voucher_no") or "").strip()
    shift_code = (data.get("shift_code") or "").strip()
    shift_date = (data.get("shift_date") or "").strip()
    cashier = (data.get("cashier") or "").strip()
    diff_amount = data.get("diff_amount")
    reason = (data.get("reason") or "").strip()
    remark = (data.get("remark") or "").strip()

    if not voucher_no and not is_import:
        errors.append("单据编号不能为空")
    if not shift_code:
        errors.append("班次不能为空")
    if not shift_date:
        errors.append("班次日期不能为空")
    if not cashier:
        errors.append("收银员不能为空")

    if diff_amount is None or diff_amount == "":
        errors.append("差异金额不能为空")
    else:
        try:
            diff_amount = float(diff_amount)
        except (TypeError, ValueError):
            errors.append("差异金额必须是数字")
            diff_amount = 0

        if diff_amount < 0 and not reason:
            errors.append("负金额（短款）必须填写原因")

    return {
        "voucher_no": voucher_no,
        "shift_code": shift_code,
        "shift_date": shift_date,
        "cashier": cashier,
        "diff_amount": diff_amount if isinstance(diff_amount, (int, float)) else 0,
        "reason": reason,
        "remark": remark,
        "_errors": errors
    }


def gen_voucher_no(db, shift_code, shift_date):
    date_str = shift_date.replace("-", "")
    prefix = f"SD{date_str}"
    row = db.execute(
        "SELECT COUNT(*) AS c FROM vouchers WHERE voucher_no LIKE ?", (prefix + "%",)
    ).fetchone()
    return f"{prefix}{row['c'] + 1:04d}"


@app.route("/api/vouchers", methods=["GET"])
@login_required
def api_list_vouchers():
    shift_code = request.args.get("shift_code") or ""
    handler = request.args.get("handler") or ""
    status = request.args.get("status") or ""
    keyword = (request.args.get("keyword") or "").strip()
    alert_disp = request.args.get("alert_disposition") or ""

    sql = "SELECT * FROM vouchers WHERE 1=1"
    params = []
    if shift_code:
        sql += " AND shift_code = ?"
        params.append(shift_code)
    if handler:
        sql += (" AND (created_by = ? OR reviewed_by = ? OR closed_by = ? "
                "OR current_handler = ?)")
        params += [handler, handler, handler, handler]
    if status:
        sql += " AND status = ?"
        params.append(status)
    if keyword:
        sql += " AND (voucher_no LIKE ? OR cashier LIKE ? OR remark LIKE ? OR reason LIKE ?)"
        k = f"%{keyword}%"
        params += [k, k, k, k]
    sql += " ORDER BY id DESC"

    db = get_db()
    rows = db.execute(sql, params).fetchall()
    result = [row_to_dict(r) for r in rows]

    alert_map = {}
    if result:
        vnos = [v["voucher_no"] for v in result]
        placeholders = ",".join("?" * len(vnos))
        alert_sql = f"""
            SELECT id, voucher_no, rule_name, rule_type, alert_reason, created_at,
                   disposition_status, disposition_note, disposition_handler,
                   disposition_time, disposition_version
            FROM alert_logs WHERE voucher_no IN ({placeholders})
        """
        alert_params = list(vnos)
        if alert_disp:
            alert_sql += " AND disposition_status = ?"
            alert_params.append(alert_disp)
        alert_rows = db.execute(alert_sql, alert_params).fetchall()
        for ar in alert_rows:
            alert_map.setdefault(ar["voucher_no"], []).append({
                "id": ar["id"],
                "rule_name": ar["rule_name"],
                "rule_type": ar["rule_type"],
                "reason": ar["alert_reason"],
                "created_at": ar["created_at"],
                "disposition_status": ar["disposition_status"],
                "disposition_note": ar["disposition_note"],
                "disposition_handler": ar["disposition_handler"],
                "disposition_time": ar["disposition_time"],
                "disposition_version": ar["disposition_version"],
            })

    filtered_result = []
    for v in result:
        alerts = alert_map.get(v["voucher_no"], [])
        if alert_disp and not alerts:
            continue
        v["warning_reasons"] = alerts
        if alerts:
            disp_statuses = [a["disposition_status"] for a in alerts]
            if DISP_UNPROCESSED in disp_statuses:
                v["alert_disp_summary"] = DISP_UNPROCESSED
            elif DISP_FOLLOW_UP in disp_statuses:
                v["alert_disp_summary"] = DISP_FOLLOW_UP
            elif DISP_CONFIRMED in disp_statuses:
                v["alert_disp_summary"] = DISP_CONFIRMED
            else:
                v["alert_disp_summary"] = DISP_IGNORED
        else:
            v["alert_disp_summary"] = None
        filtered_result.append(v)

    handler_rows = db.execute("""
        SELECT DISTINCT username, display_name FROM users
        WHERE role IN ('manager','admin','cashier')
        ORDER BY username
    """).fetchall()

    return jsonify({
        "vouchers": filtered_result,
        "handlers": [row_to_dict(r) for r in handler_rows]
    })


@app.route("/api/summary")
@login_required
def api_summary():
    db = get_db()
    rows = db.execute("""
        SELECT status, COUNT(*) AS cnt,
               COALESCE(SUM(diff_amount),0) AS total_amount
        FROM vouchers
        GROUP BY status
    """).fetchall()
    status_map = {r["status"]: {"count": r["cnt"], "total": r["total_amount"]} for r in rows}

    pending_rows = db.execute("""
        SELECT shift_code, COUNT(*) AS cnt
        FROM vouchers WHERE status = ? GROUP BY shift_code
    """, (STATUS_PENDING,)).fetchall()

    return jsonify({
        "status_map": status_map,
        "pending_count": status_map.get(STATUS_PENDING, {}).get("count", 0),
        "pending_total": status_map.get(STATUS_PENDING, {}).get("total", 0),
        "open_count": sum(
            status_map.get(s, {}).get("count", 0)
            for s in (STATUS_DRAFT, STATUS_PENDING, STATUS_RETURNED, STATUS_REVIEWED)
        ),
        "pending_by_shift": {r["shift_code"]: r["cnt"] for r in pending_rows}
    })


@app.route("/api/vouchers/<int:vid>")
@login_required
def api_get_voucher(vid):
    db = get_db()
    v = db.execute("SELECT * FROM vouchers WHERE id = ?", (vid,)).fetchone()
    if not v:
        return jsonify({"error": "单据不存在"}), 404
    tl = db.execute(
        "SELECT * FROM timeline WHERE voucher_no = ? ORDER BY id ASC",
        (v["voucher_no"],)
    ).fetchall()
    alerts = get_voucher_alerts(db, v["voucher_no"])
    return jsonify({
        "voucher": row_to_dict(v),
        "timeline": [row_to_dict(t) for t in tl],
        "alerts": alerts
    })


@app.route("/api/vouchers", methods=["POST"])
@role_required(ROLE_CASHIER, ROLE_ADMIN, ROLE_MANAGER)
def api_create_voucher():
    data = request.get_json(force=True, silent=True) or {}
    payload = validate_voucher_payload(data)
    if payload["_errors"]:
        return jsonify({"error": payload["_errors"][0]}), 400

    user = current_user()
    db = get_db()

    voucher_no = payload["voucher_no"] or gen_voucher_no(db, payload["shift_code"], payload["shift_date"])

    existing = db.execute("SELECT * FROM vouchers WHERE voucher_no = ?", (voucher_no,)).fetchone()
    if existing:
        return jsonify({"error": "单据编号已存在"}), 400

    cur = db.cursor()
    cur.execute("""
        INSERT INTO vouchers
        (voucher_no, shift_code, shift_date, cashier, diff_amount, reason, remark,
         status, current_handler, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        voucher_no, payload["shift_code"], payload["shift_date"], payload["cashier"],
        payload["diff_amount"], payload["reason"], payload["remark"],
        STATUS_DRAFT, user["username"], user["username"]
    ))
    vid = cur.lastrowid
    add_timeline(cur, voucher_no, "创建草稿", user["username"], user["role"],
                 f"创建草稿：{voucher_no}")
    alerts = check_alert_rules(db, voucher_no, vid, payload["cashier"],
                               payload["diff_amount"], payload["shift_date"], cur)
    db.commit()

    return jsonify({"id": vid, "voucher_no": voucher_no, "alerts": alerts})


@app.route("/api/vouchers/<int:vid>/submit", methods=["POST"])
@role_required(ROLE_CASHIER, ROLE_ADMIN, ROLE_MANAGER)
def api_submit_voucher(vid):
    data = request.get_json(force=True, silent=True) or {}
    remark = (data.get("remark") or "").strip()
    reason = (data.get("reason") or "").strip()
    diff_amount = data.get("diff_amount")

    user = current_user()
    db = get_db()
    v = db.execute("SELECT * FROM vouchers WHERE id = ?", (vid,)).fetchone()
    if not v:
        return jsonify({"error": "单据不存在"}), 404

    if v["status"] not in (STATUS_DRAFT, STATUS_RETURNED):
        return jsonify({"error": f"当前状态「{v['status']}」不允许提交"}), 400

    if v["created_by"] != user["username"] and user["role"] not in (ROLE_ADMIN, ROLE_MANAGER):
        return jsonify({"error": "只能提交自己创建的单据"}), 403

    if v["status"] == STATUS_RETURNED:
        prev_remark = v["remark"] or ""
        prev_reason = v["reason"] or ""
        if remark.strip() and remark.strip() == prev_remark.strip():
            pass
        if (not remark.strip() or remark.strip() == prev_remark.strip()) and \
           (not reason.strip() or reason.strip() == prev_reason.strip()):
            return jsonify({"error": "被退回后必须补充备注或原因才能重新提交"}), 400

    amount = float(diff_amount) if diff_amount not in (None, "") else v["diff_amount"]
    if amount < 0 and not (reason or v["reason"]):
        return jsonify({"error": "负金额（短款）必须填写原因"}), 400

    cur = db.cursor()
    cur.execute("""
        UPDATE vouchers
        SET status = ?, updated_at = datetime('now','localtime'),
            diff_amount = ?, reason = ?, remark = ?, current_handler = ?,
            shift_code = COALESCE(?, shift_code),
            shift_date = COALESCE(?, shift_date),
            cashier = COALESCE(?, cashier)
        WHERE id = ?
    """, (
        STATUS_PENDING,
        amount,
        reason or v["reason"],
        remark or v["remark"],
        None,
        data.get("shift_code") or None,
        data.get("shift_date") or None,
        data.get("cashier") or None,
        vid
    ))
    add_timeline(cur, v["voucher_no"], "提交复核", user["username"], user["role"],
                 f"收银员提交复核，金额：{amount}，备注：{remark or v['remark'] or '无'}")
    final_cashier = data.get("cashier") or v["cashier"]
    final_date = data.get("shift_date") or v["shift_date"]
    alerts = check_alert_rules(db, v["voucher_no"], vid, final_cashier,
                               amount, final_date, cur)
    db.commit()
    return jsonify({"ok": True, "status": STATUS_PENDING, "alerts": alerts})


@app.route("/api/vouchers/<int:vid>/review", methods=["POST"])
@role_required(ROLE_MANAGER, ROLE_ADMIN)
def api_review_voucher(vid):
    data = request.get_json(force=True, silent=True) or {}
    action = (data.get("action") or "").strip()
    note = (data.get("note") or "").strip()
    user = current_user()
    db = get_db()
    v = db.execute("SELECT * FROM vouchers WHERE id = ?", (vid,)).fetchone()
    if not v:
        return jsonify({"error": "单据不存在"}), 404
    if v["status"] != STATUS_PENDING:
        return jsonify({"error": f"当前状态「{v['status']}」不允许复核"}), 400

    cur = db.cursor()
    if action == "approve":
        cur.execute("""
            UPDATE vouchers SET status = ?, reviewed_by = ?, updated_at = datetime('now','localtime')
            WHERE id = ?
        """, (STATUS_REVIEWED, user["username"], vid))
        add_timeline(cur, v["voucher_no"], "复核通过", user["username"], user["role"],
                     f"值班长复核通过：{note or '无备注'}")
        new_status = STATUS_REVIEWED
    elif action == "return":
        if not note:
            return jsonify({"error": "退回必须填写退回说明"}), 400
        cur.execute("""
            UPDATE vouchers SET status = ?, return_note = ?, current_handler = ?,
                updated_at = datetime('now','localtime')
            WHERE id = ?
        """, (STATUS_RETURNED, note, v["created_by"], vid))
        add_timeline(cur, v["voucher_no"], "退回", user["username"], user["role"],
                     f"退回说明：{note}")
        new_status = STATUS_RETURNED
    else:
        return jsonify({"error": "未知操作"}), 400
    db.commit()
    return jsonify({"ok": True, "status": new_status})


@app.route("/api/vouchers/<int:vid>/close", methods=["POST"])
@role_required(ROLE_MANAGER, ROLE_ADMIN)
def api_close_voucher(vid):
    data = request.get_json(force=True, silent=True) or {}
    note = (data.get("note") or "").strip()
    user = current_user()
    db = get_db()
    v = db.execute("SELECT * FROM vouchers WHERE id = ?", (vid,)).fetchone()
    if not v:
        return jsonify({"error": "单据不存在"}), 404
    if v["status"] not in (STATUS_REVIEWED, STATUS_PENDING):
        return jsonify({"error": f"当前状态「{v['status']}」不允许关闭"}), 400
    if v["created_by"] == user["username"]:
        return jsonify({"error": "不允许关闭自己创建的单据"}), 403

    cur = db.cursor()
    cur.execute("""
        UPDATE vouchers SET status = ?, closed_by = ?, closed_note = ?,
            updated_at = datetime('now','localtime')
        WHERE id = ?
    """, (STATUS_CLOSED, user["username"], note, vid))
    add_timeline(cur, v["voucher_no"], "关闭", user["username"], user["role"],
                 f"关闭说明：{note or '无'}")
    db.commit()
    return jsonify({"ok": True, "status": STATUS_CLOSED})


@app.route("/api/vouchers/<int:vid>/revoke", methods=["POST"])
@role_required(ROLE_ADMIN, ROLE_MANAGER)
def api_revoke_voucher(vid):
    data = request.get_json(force=True, silent=True) or {}
    reason_r = (data.get("reason") or "").strip()
    if not reason_r:
        return jsonify({"error": "撤销必须填写原因"}), 400

    user = current_user()
    db = get_db()
    v = db.execute("SELECT * FROM vouchers WHERE id = ?", (vid,)).fetchone()
    if not v:
        return jsonify({"error": "单据不存在"}), 404
    if v["status"] == STATUS_REVOKED:
        return jsonify({"error": "单据已撤销"}), 400
    if v["status"] not in (STATUS_REVIEWED, STATUS_CLOSED, STATUS_PENDING, STATUS_RETURNED, STATUS_DRAFT):
        return jsonify({"error": f"当前状态「{v['status']}」不允许撤销"}), 400

    cur = db.cursor()

    new_no = v["voucher_no"] + "-R" + datetime.now().strftime("%H%M%S")
    cur.execute("""
        UPDATE vouchers SET status = ?, revoked_by = ?, revoked_at = datetime('now','localtime'),
            updated_at = datetime('now','localtime')
        WHERE id = ?
    """, (STATUS_REVOKED, user["username"], vid))

    cur.execute("""
        INSERT INTO vouchers
        (voucher_no, shift_code, shift_date, cashier, diff_amount, reason, remark,
         status, current_handler, created_by, parent_voucher_no)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        new_no, v["shift_code"], v["shift_date"], v["cashier"],
        v["diff_amount"], v["reason"], v["remark"],
        STATUS_DRAFT, user["username"], v["created_by"], v["voucher_no"]
    ))
    new_vid = cur.lastrowid
    add_timeline(cur, v["voucher_no"], "撤销", user["username"], user["role"],
                 f"撤销原因：{reason_r}，已生成新单据 {new_no} 用于更正")
    add_timeline(cur, new_no, "创建草稿（撤销更正）", user["username"], user["role"],
                 f"由原单据 {v['voucher_no']} 撤销后生成，撤销原因：{reason_r}")
    check_alert_rules(db, new_no, new_vid, v["cashier"],
                      v["diff_amount"], v["shift_date"], cur)
    db.commit()
    return jsonify({"ok": True, "new_id": new_vid, "new_voucher_no": new_no,
                    "status": STATUS_REVOKED})


# ---------- CSV Import / Export ---------- #

@app.route("/api/vouchers/export.csv")
@login_required
def api_export_csv():
    db = get_db()
    rows = db.execute("SELECT * FROM vouchers ORDER BY id DESC").fetchall()

    vnos = [r["voucher_no"] for r in rows] if rows else []
    alert_map = {}
    if vnos:
        placeholders = ",".join("?" * len(vnos))
        alert_rows = db.execute(f"""
            SELECT voucher_no, rule_name, alert_reason, disposition_status,
                   disposition_note, disposition_handler, disposition_time
            FROM alert_logs WHERE voucher_no IN ({placeholders})
            ORDER BY id ASC
        """, vnos).fetchall()
        for ar in alert_rows:
            alert_map.setdefault(ar["voucher_no"], []).append({
                "rule_name": ar["rule_name"],
                "alert_reason": ar["alert_reason"],
                "disposition_status": ar["disposition_status"],
                "disposition_note": ar["disposition_note"],
                "disposition_handler": ar["disposition_handler"],
                "disposition_time": ar["disposition_time"],
            })

    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf)
    writer.writerow([
        "单据编号", "状态", "班次", "班次日期", "收银员", "差异金额",
        "原因", "备注", "创建人", "创建时间", "复核人", "关闭人",
        "退回说明", "关闭说明", "撤销人", "撤销时间", "关联原单", "导入来源",
        "预警规则", "预警原因", "处置状态", "处置备注", "处理人", "处理时间"
    ])
    status_text = {
        STATUS_DRAFT: "草稿", STATUS_PENDING: "待复核", STATUS_REVIEWED: "复核通过",
        STATUS_RETURNED: "已退回", STATUS_CLOSED: "已关闭", STATUS_REVOKED: "已撤销"
    }
    for r in rows:
        alerts = alert_map.get(r["voucher_no"], [])
        if not alerts:
            writer.writerow([
                r["voucher_no"], status_text.get(r["status"], r["status"]),
                r["shift_code"], r["shift_date"], r["cashier"], r["diff_amount"],
                r["reason"] or "", r["remark"] or "", r["created_by"],
                r["created_at"], r["reviewed_by"] or "", r["closed_by"] or "",
                r["return_note"] or "", r["closed_note"] or "", r["revoked_by"] or "",
                r["revoked_at"] or "", r["parent_voucher_no"] or "", r["import_source"] or "",
                "", "", "", "", "", ""
            ])
        else:
            for i, a in enumerate(alerts):
                writer.writerow([
                    r["voucher_no"] if i == 0 else "",
                    status_text.get(r["status"], r["status"]) if i == 0 else "",
                    r["shift_code"] if i == 0 else "",
                    r["shift_date"] if i == 0 else "",
                    r["cashier"] if i == 0 else "",
                    r["diff_amount"] if i == 0 else "",
                    r["reason"] or "" if i == 0 else "",
                    r["remark"] or "" if i == 0 else "",
                    r["created_by"] if i == 0 else "",
                    r["created_at"] if i == 0 else "",
                    r["reviewed_by"] or "" if i == 0 else "",
                    r["closed_by"] or "" if i == 0 else "",
                    r["return_note"] or "" if i == 0 else "",
                    r["closed_note"] or "" if i == 0 else "",
                    r["revoked_by"] or "" if i == 0 else "",
                    r["revoked_at"] or "" if i == 0 else "",
                    r["parent_voucher_no"] or "" if i == 0 else "",
                    r["import_source"] or "" if i == 0 else "",
                    a["rule_name"],
                    a["alert_reason"],
                    DISP_STATUS_LABELS.get(a["disposition_status"], a["disposition_status"]),
                    a["disposition_note"] or "",
                    a["disposition_handler"] or "",
                    a["disposition_time"] or ""
                ])
    output = buf.getvalue().encode("utf-8")
    resp = make_response(output)
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    fn = f"vouchers_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    resp.headers["Content-Disposition"] = f'attachment; filename="{fn}"'
    return resp


@app.route("/api/vouchers/import", methods=["POST"])
@role_required(ROLE_ADMIN, ROLE_MANAGER, ROLE_CASHIER)
def api_import_csv():
    if "file" not in request.files:
        return jsonify({"error": "未上传文件"}), 400
    f = request.files["file"]
    user = current_user()
    filename = f.filename or "import.csv"

    raw = f.stream.read()
    # try utf-8 then gbk
    text = None
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return jsonify({"error": "无法识别文件编码"}), 400

    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []
    def col(row, *names):
        for n in names:
            if n in row and row[n] is not None:
                val = str(row[n]).strip()
                if val:
                    return val
        return ""

    total = 0
    success = 0
    failed = 0
    errors = []
    db = get_db()
    cur = db.cursor()

    # Chinese -> English status map
    status_map_cn = {
        "草稿": STATUS_DRAFT,
        "待复核": STATUS_PENDING,
        "复核通过": STATUS_REVIEWED,
        "已退回": STATUS_RETURNED,
        "已关闭": STATUS_CLOSED,
        "已撤销": STATUS_REVOKED
    }

    try:
        for i, row in enumerate(reader, start=1):
            total += 1
            try:
                vno = col(row, "单据编号", "voucher_no")
                status_raw = col(row, "状态", "status")
                shift_code = col(row, "班次", "shift_code")
                shift_date = col(row, "班次日期", "shift_date")
                cashier = col(row, "收银员", "cashier")
                diff = col(row, "差异金额", "diff_amount")
                reason = col(row, "原因", "reason")
                remark = col(row, "备注", "remark")
                created_by = col(row, "创建人", "created_by") or user["username"]

                if not vno:
                    raise ValueError("缺少单据编号")
                if not shift_code:
                    raise ValueError("缺少班次")
                if not shift_date:
                    raise ValueError("缺少班次日期")
                if not cashier:
                    raise ValueError("缺少收银员")
                try:
                    diff_f = float(diff) if diff else 0
                except ValueError:
                    raise ValueError(f"差异金额无效：{diff}")
                if diff_f < 0 and not reason:
                    raise ValueError("负金额必须填写原因")

                existing = cur.execute(
                    "SELECT * FROM vouchers WHERE voucher_no = ?", (vno,)
                ).fetchone()
                if existing:
                    if existing["status"] == STATUS_CLOSED:
                        raise ValueError(f"单据已关闭，不允许重复导入")
                    elif existing["status"] == STATUS_REVOKED:
                        raise ValueError(f"单据已撤销，不允许重复导入")
                    else:
                        raise ValueError(f"单据编号已存在（状态：{existing['status']}）")

                status = status_map_cn.get(status_raw, STATUS_DRAFT)
                if status in (STATUS_CLOSED, STATUS_REVOKED):
                    raise ValueError("不允许导入已关闭或已撤销状态的单据")

                cur.execute("""
                    INSERT INTO vouchers
                    (voucher_no, shift_code, shift_date, cashier, diff_amount, reason, remark,
                     status, current_handler, created_by, import_source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    vno, shift_code, shift_date, cashier, diff_f, reason, remark,
                    status, created_by, created_by, filename
                ))
                imported_vid = cur.lastrowid
                add_timeline(cur, vno, "导入", user["username"], user["role"],
                             f"从文件 {filename} 导入，导入状态：{status}")
                check_alert_rules(db, vno, imported_vid, cashier, diff_f, shift_date, cur)
                success += 1
            except Exception as e:
                failed += 1
                errors.append(f"第{i}行：{str(e)}")
                if len(errors) > 50:
                    errors.append("...其余错误省略...")
                    break

        cur.execute("""
            INSERT INTO import_log (filename, total_count, success_count, failed_count,
                                    error_detail, imported_by)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (filename, total, success, failed,
              "\n".join(errors[:200])[:2000], user["username"]))
        db.commit()
    except Exception as e:
        db.rollback()
        return jsonify({"error": f"导入异常：{e}"}), 500

    return jsonify({
        "ok": True,
        "total": total,
        "success": success,
        "failed": failed,
        "errors": errors
    })


RULE_TYPE_SINGLE = "single_amount"
RULE_TYPE_CUMULATIVE = "cumulative_amount"
RULE_TYPE_CONSECUTIVE_RETURN = "consecutive_return"

RULE_TYPE_LABELS = {
    RULE_TYPE_SINGLE: "单笔差异金额阈值",
    RULE_TYPE_CUMULATIVE: "同一收银员当天累计差异阈值",
    RULE_TYPE_CONSECUTIVE_RETURN: "连续退回次数",
}


def check_alert_rules(db, voucher_no, voucher_id, cashier, diff_amount, shift_date, cur):
    rules = db.execute(
        "SELECT * FROM alert_rules WHERE enabled = 1"
    ).fetchall()

    triggered = []

    for rule in rules:
        hit = False
        reason = ""

        if rule["rule_type"] == RULE_TYPE_SINGLE:
            if abs(diff_amount) >= rule["threshold"]:
                hit = True
                reason = f"单笔差异金额 ¥{abs(diff_amount):.2f} ≥ 阈值 ¥{rule['threshold']:.2f}"

        elif rule["rule_type"] == RULE_TYPE_CUMULATIVE:
            row = db.execute("""
                SELECT COALESCE(SUM(ABS(diff_amount)), 0) AS total
                FROM vouchers
                WHERE cashier = ? AND shift_date = ? AND status != 'revoked'
            """, (cashier, shift_date)).fetchone()
            cumulative = row["total"]
            if cumulative >= rule["threshold"]:
                hit = True
                reason = f"收银员 {cashier} 当天累计差异 ¥{cumulative:.2f} ≥ 阈值 ¥{rule['threshold']:.2f}"

        elif rule["rule_type"] == RULE_TYPE_CONSECUTIVE_RETURN:
            returned_count = db.execute("""
                SELECT COUNT(*) AS cnt FROM vouchers
                WHERE cashier = ? AND status = 'returned'
            """, (cashier,)).fetchone()["cnt"]
            if returned_count >= int(rule["threshold"]):
                hit = True
                reason = f"收银员 {cashier} 退回次数 {returned_count} ≥ 阈值 {int(rule['threshold'])}"

        if hit:
            existing = db.execute("""
                SELECT id FROM alert_logs WHERE voucher_no = ? AND rule_id = ?
            """, (voucher_no, rule["id"])).fetchone()
            if existing:
                triggered.append({"rule_name": rule["name"], "rule_type": rule["rule_type"], "reason": reason})
                continue
            if cur is None:
                c = db.cursor()
            else:
                c = cur
            c.execute("""
                INSERT INTO alert_logs (voucher_no, voucher_id, rule_id, rule_name, rule_type, alert_reason)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (voucher_no, voucher_id, rule["id"], rule["name"], rule["rule_type"], reason))
            triggered.append({"rule_name": rule["name"], "rule_type": rule["rule_type"], "reason": reason})

    return triggered


def get_voucher_alerts(db, voucher_no):
    rows = db.execute(
        "SELECT * FROM alert_logs WHERE voucher_no = ? ORDER BY id ASC",
        (voucher_no,)
    ).fetchall()
    return [row_to_dict(r) for r in rows]


@app.route("/api/import_logs")
@login_required
def api_import_logs():
    db = get_db()
    rows = db.execute("SELECT * FROM import_log ORDER BY id DESC LIMIT 100").fetchall()
    return jsonify({"logs": [row_to_dict(r) for r in rows]})


# ---------- Alert Rules ---------- #

@app.route("/api/alert-rules", methods=["GET"])
@role_required(ROLE_ADMIN, ROLE_MANAGER)
def api_list_alert_rules():
    db = get_db()
    rows = db.execute("SELECT * FROM alert_rules ORDER BY id ASC").fetchall()
    return jsonify({"rules": [row_to_dict(r) for r in rows]})


@app.route("/api/alert-rules", methods=["POST"])
@role_required(ROLE_ADMIN, ROLE_MANAGER)
def api_create_alert_rule():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    rule_type = (data.get("rule_type") or "").strip()
    threshold = data.get("threshold")
    description = (data.get("description") or "").strip()
    enabled = 1 if data.get("enabled", True) else 0

    if not name:
        return jsonify({"error": "规则名称不能为空"}), 400
    if rule_type not in (RULE_TYPE_SINGLE, RULE_TYPE_CUMULATIVE, RULE_TYPE_CONSECUTIVE_RETURN):
        return jsonify({"error": f"规则类型无效，可选：{','.join(RULE_TYPE_LABELS.keys())}"}), 400
    if threshold is None:
        return jsonify({"error": "阈值不能为空"}), 400
    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        return jsonify({"error": "阈值必须是数字"}), 400
    if threshold <= 0:
        return jsonify({"error": "阈值必须大于0"}), 400

    user = current_user()
    db = get_db()
    try:
        cur = db.cursor()
        cur.execute("""
            INSERT INTO alert_rules (name, rule_type, threshold, enabled, description, created_by)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (name, rule_type, threshold, enabled, description, user["username"]))
        rule_id = cur.lastrowid
        add_operation_log(cur, "创建预警规则", user["username"], user["role"],
                         f"规则名={name}, 类型={rule_type}, 阈值={threshold}")
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "规则名称已存在"}), 400

    return jsonify({"ok": True, "id": rule_id})


@app.route("/api/alert-rules/<int:rule_id>", methods=["PUT"])
@role_required(ROLE_ADMIN, ROLE_MANAGER)
def api_update_alert_rule(rule_id):
    data = request.get_json(force=True, silent=True) or {}
    db = get_db()
    existing = db.execute("SELECT * FROM alert_rules WHERE id = ?", (rule_id,)).fetchone()
    if not existing:
        return jsonify({"error": "规则不存在"}), 404

    name = (data.get("name") or existing["name"]).strip()
    rule_type = (data.get("rule_type") or existing["rule_type"]).strip()
    threshold = data.get("threshold")
    if threshold is not None:
        try:
            threshold = float(threshold)
        except (TypeError, ValueError):
            return jsonify({"error": "阈值必须是数字"}), 400
        if threshold <= 0:
            return jsonify({"error": "阈值必须大于0"}), 400
    else:
        threshold = existing["threshold"]
    description = (data.get("description") or existing["description"]).strip()
    enabled = 1 if data.get("enabled", bool(existing["enabled"])) else 0

    if rule_type not in (RULE_TYPE_SINGLE, RULE_TYPE_CUMULATIVE, RULE_TYPE_CONSECUTIVE_RETURN):
        return jsonify({"error": "规则类型无效"}), 400

    user = current_user()
    cur = db.cursor()
    try:
        cur.execute("""
            UPDATE alert_rules SET name=?, rule_type=?, threshold=?, enabled=?,
                description=?, updated_at=datetime('now','localtime')
            WHERE id=?
        """, (name, rule_type, threshold, enabled, description, rule_id))
        add_operation_log(cur, "更新预警规则", user["username"], user["role"],
                         f"规则ID={rule_id}, 名称={name}, 类型={rule_type}, 阈值={threshold}")
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "规则名称已存在"}), 400

    return jsonify({"ok": True})


@app.route("/api/alert-rules/<int:rule_id>", methods=["DELETE"])
@role_required(ROLE_ADMIN, ROLE_MANAGER)
def api_delete_alert_rule(rule_id):
    db = get_db()
    existing = db.execute("SELECT * FROM alert_rules WHERE id = ?", (rule_id,)).fetchone()
    if not existing:
        return jsonify({"error": "规则不存在"}), 404

    user = current_user()
    cur = db.cursor()
    cur.execute("DELETE FROM alert_rules WHERE id = ?", (rule_id,))
    add_operation_log(cur, "删除预警规则", user["username"], user["role"],
                     f"规则ID={rule_id}, 名称={existing['name']}")
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/alert-rules/export.csv")
@role_required(ROLE_ADMIN, ROLE_MANAGER)
def api_export_alert_rules_csv():
    db = get_db()
    rows = db.execute("SELECT * FROM alert_rules ORDER BY id ASC").fetchall()

    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf)
    writer.writerow(["规则名称", "规则类型", "阈值", "是否启用", "描述"])
    for r in rows:
        writer.writerow([
            r["name"], r["rule_type"], r["threshold"],
            "是" if r["enabled"] else "否", r["description"] or ""
        ])
    output = buf.getvalue().encode("utf-8")
    resp = make_response(output)
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    fn = f"alert_rules_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    resp.headers["Content-Disposition"] = f'attachment; filename="{fn}"'
    return resp


@app.route("/api/alert-rules/import", methods=["POST"])
@role_required(ROLE_ADMIN, ROLE_MANAGER)
def api_import_alert_rules_csv():
    if "file" not in request.files:
        return jsonify({"error": "未上传文件"}), 400
    f = request.files["file"]
    user = current_user()
    filename = f.filename or "import_rules.csv"

    raw = f.stream.read()
    text = None
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return jsonify({"error": "无法识别文件编码"}), 400

    reader = csv.DictReader(io.StringIO(text))

    def col(row, *names):
        for n in names:
            if n in row and row[n] is not None:
                val = str(row[n]).strip()
                if val:
                    return val
        return ""

    db = get_db()
    cur = db.cursor()
    total = 0
    success = 0
    skipped = 0
    failed = 0
    details = []

    for i, row in enumerate(reader, start=1):
        total += 1
        try:
            name = col(row, "规则名称", "name")
            rule_type = col(row, "规则类型", "rule_type")
            threshold_str = col(row, "阈值", "threshold")
            enabled_str = col(row, "是否启用", "enabled")
            description = col(row, "描述", "description")

            if not name:
                raise ValueError("规则名称不能为空")
            if rule_type not in (RULE_TYPE_SINGLE, RULE_TYPE_CUMULATIVE, RULE_TYPE_CONSECUTIVE_RETURN):
                raise ValueError(f"规则类型无效：{rule_type}")
            if not threshold_str:
                raise ValueError("阈值不能为空")
            try:
                threshold = float(threshold_str)
            except (TypeError, ValueError):
                raise ValueError(f"阈值必须是数字：{threshold_str}")
            if threshold <= 0:
                raise ValueError("阈值必须大于0")

            enabled = 1
            if enabled_str:
                enabled = 0 if enabled_str in ("否", "0", "false", "False") else 1

            existing = cur.execute("SELECT id FROM alert_rules WHERE name = ?", (name,)).fetchone()
            if existing:
                skipped += 1
                details.append(f"第{i}行：规则名称「{name}」已存在，跳过")
                continue

            cur.execute("""
                INSERT INTO alert_rules (name, rule_type, threshold, enabled, description, created_by)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (name, rule_type, threshold, enabled, description, user["username"]))
            success += 1
        except ValueError as e:
            failed += 1
            details.append(f"第{i}行：{str(e)}")

    add_operation_log(cur, "导入预警规则", user["username"], user["role"],
                     f"文件={filename}, 总数={total}, 成功={success}, 跳过={skipped}, 失败={failed}")
    db.commit()

    return jsonify({
        "ok": True,
        "total": total,
        "success": success,
        "skipped": skipped,
        "failed": failed,
        "details": details
    })


# ---------- Alert Logs ---------- #

@app.route("/api/alert-logs", methods=["GET"])
@login_required
def api_list_alert_logs():
    voucher_no = request.args.get("voucher_no") or ""
    disp_status = request.args.get("disposition_status") or ""
    db = get_db()
    sql = "SELECT * FROM alert_logs WHERE 1=1"
    params = []
    if voucher_no:
        sql += " AND voucher_no = ?"
        params.append(voucher_no)
    if disp_status:
        sql += " AND disposition_status = ?"
        params.append(disp_status)
    sql += " ORDER BY id DESC LIMIT 200"
    rows = db.execute(sql, params).fetchall()
    return jsonify({"logs": [row_to_dict(r) for r in rows]})


@app.route("/api/alert-logs/<int:alert_id>/disposition", methods=["POST"])
@role_required(ROLE_ADMIN, ROLE_MANAGER)
def api_update_alert_disposition(alert_id):
    data = request.get_json(force=True, silent=True) or {}
    status = (data.get("disposition_status") or "").strip()
    note = (data.get("disposition_note") or "").strip()
    version = data.get("disposition_version")

    if status not in (DISP_UNPROCESSED, DISP_CONFIRMED, DISP_FOLLOW_UP, DISP_IGNORED):
        return jsonify({"error": f"无效的处置状态，可选：{','.join(DISP_STATUS_LABELS.keys())}"}), 400

    user = current_user()
    db = get_db()
    cur = db.cursor()

    cur.execute("SELECT * FROM alert_logs WHERE id = ?", (alert_id,))
    alert = cur.fetchone()
    if not alert:
        return jsonify({"error": "预警记录不存在"}), 404

    if version is not None and int(version) != int(alert["disposition_version"]):
        return jsonify({
            "error": "处置冲突：该预警已被其他用户处理过，请刷新后重新查看最新状态再提交",
            "current": row_to_dict(alert)
        }), 409

    new_version = int(alert["disposition_version"]) + 1
    cur.execute("""
        UPDATE alert_logs
        SET disposition_status = ?, disposition_note = ?, disposition_handler = ?,
            disposition_time = datetime('now','localtime'), disposition_version = ?
        WHERE id = ? AND disposition_version = ?
    """, (status, note, user["username"], new_version, alert_id, alert["disposition_version"]))

    if cur.rowcount == 0:
        cur.execute("SELECT * FROM alert_logs WHERE id = ?", (alert_id,))
        latest = cur.fetchone()
        return jsonify({
            "error": "处置冲突：该预警已被其他用户处理过，请刷新后重新查看最新状态再提交",
            "current": row_to_dict(latest)
        }), 409

    add_operation_log(cur, "更新预警处置", user["username"], user["role"],
                     f"预警ID={alert_id}, 状态={DISP_STATUS_LABELS.get(status, status)}, "
                     f"单据={alert['voucher_no']}, 备注={note[:100] if note else '无'}")
    db.commit()

    cur.execute("SELECT * FROM alert_logs WHERE id = ?", (alert_id,))
    updated = cur.fetchone()
    return jsonify({"ok": True, "alert": row_to_dict(updated)})


@app.route("/api/alert-logs/batch-disposition", methods=["POST"])
@role_required(ROLE_ADMIN, ROLE_MANAGER)
def api_batch_alert_disposition():
    data = request.get_json(force=True, silent=True) or {}
    items = data.get("items") or []
    status = (data.get("disposition_status") or "").strip()
    note = (data.get("disposition_note") or "").strip()

    if status not in (DISP_UNPROCESSED, DISP_CONFIRMED, DISP_FOLLOW_UP, DISP_IGNORED):
        return jsonify({"error": f"无效的处置状态，可选：{','.join(DISP_STATUS_LABELS.keys())}"}), 400

    if not items:
        return jsonify({"error": "未选择任何预警记录"}), 400

    user = current_user()
    db = get_db()
    cur = db.cursor()

    results = {
        "success": [],
        "conflict": [],
        "not_found": [],
        "no_permission": []
    }

    for item in items:
        alert_id = item.get("id")
        version = item.get("disposition_version")

        if alert_id is None:
            results["not_found"].append({"id": alert_id, "error": "缺少预警ID"})
            continue

        try:
            alert_id = int(alert_id)
        except (TypeError, ValueError):
            results["not_found"].append({"id": alert_id, "error": "预警ID无效"})
            continue

        cur.execute("SELECT * FROM alert_logs WHERE id = ?", (alert_id,))
        alert = cur.fetchone()
        if not alert:
            results["not_found"].append({"id": alert_id, "error": "预警记录不存在"})
            continue

        if version is not None and int(version) != int(alert["disposition_version"]):
            results["conflict"].append({
                "id": alert_id,
                "voucher_no": alert["voucher_no"],
                "rule_name": alert["rule_name"],
                "error": "处置冲突：该预警已被其他用户处理过",
                "current": row_to_dict(alert)
            })
            continue

        new_version = int(alert["disposition_version"]) + 1
        cur.execute("""
            UPDATE alert_logs
            SET disposition_status = ?, disposition_note = ?, disposition_handler = ?,
                disposition_time = datetime('now','localtime'), disposition_version = ?
            WHERE id = ? AND disposition_version = ?
        """, (status, note, user["username"], new_version, alert_id, alert["disposition_version"]))

        if cur.rowcount == 0:
            cur.execute("SELECT * FROM alert_logs WHERE id = ?", (alert_id,))
            latest = cur.fetchone()
            results["conflict"].append({
                "id": alert_id,
                "voucher_no": alert["voucher_no"],
                "rule_name": alert["rule_name"],
                "error": "处置冲突：该预警已被其他用户处理过",
                "current": row_to_dict(latest)
            })
            continue

        cur.execute("SELECT * FROM alert_logs WHERE id = ?", (alert_id,))
        updated = cur.fetchone()
        results["success"].append({
            "id": alert_id,
            "voucher_no": alert["voucher_no"],
            "rule_name": alert["rule_name"],
            "alert": row_to_dict(updated)
        })

        add_operation_log(cur, "批量更新预警处置", user["username"], user["role"],
                         f"预警ID={alert_id}, 状态={DISP_STATUS_LABELS.get(status, status)}, "
                         f"单据={alert['voucher_no']}, 备注={note[:100] if note else '无'}")

    db.commit()

    summary = {k: len(v) for k, v in results.items()}
    return jsonify({
        "ok": True,
        "summary": summary,
        "results": results
    })


# ---------- Operation Logs ---------- #

@app.route("/api/operation-logs", methods=["GET"])
@role_required(ROLE_ADMIN, ROLE_MANAGER)
def api_list_operation_logs():
    action = request.args.get("action") or ""
    db = get_db()
    if action:
        rows = db.execute(
            "SELECT * FROM operation_log WHERE action LIKE ? ORDER BY id DESC LIMIT 200",
            (f"%{action}%",)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM operation_log ORDER BY id DESC LIMIT 200"
        ).fetchall()
    return jsonify({"logs": [row_to_dict(r) for r in rows]})


# ---------- Init & Run ---------- #

init_db()

if __name__ == "__main__":
    print("=" * 60)
    print(" 门店交接班差异登记工具")
    print(f" 数据文件: {DB_PATH}")
    print(" 默认账号:")
    for u in DEFAULT_USERS:
        print(f"   {u['username']:<10s} / {u['password']:<12s} ({u['display_name']})")
    print(" 访问地址: http://127.0.0.1:5000/")
    print("=" * 60)
    app.run(host="127.0.0.1", port=5000, debug=False)
