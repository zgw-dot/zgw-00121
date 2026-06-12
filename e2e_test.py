"""End-to-end test script for Shift Difference Registration Tool"""
import os
import sys
import json
import time
import csv
import io
import subprocess
import urllib.request
import urllib.parse
import sqlite3
from pathlib import Path

if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

APP_DIR = Path(__file__).parent
DB_PATH = APP_DIR / "data" / "shift_diff.db"
BASE_URL = "http://127.0.0.1:5000"

OK = "[PASS]"
FAIL = "[FAIL]"

def clean_db():
    if DB_PATH.exists():
        DB_PATH.unlink()
    print(f"{OK} Old database cleaned")

def start_server():
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.Popen(
        [sys.executable, "app.py"],
        cwd=str(APP_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    for i in range(90):
        try:
            with urllib.request.urlopen(BASE_URL + "/", timeout=2) as r:
                print(f"{OK} Server started (attempt {i+1})")
                return p
        except Exception:
            time.sleep(1.0)
    raise RuntimeError("Server startup timeout")

class Session:
    def __init__(self):
        self.cookies = {}

    def _cookie_header(self):
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    def _extract_cookies(self, resp):
        sc = resp.headers.get_all("Set-Cookie") or []
        for s in sc:
            # extract name=value before the first ;
            nv = s.split(";")[0]
            if "=" in nv:
                name, val = nv.split("=", 1)
                self.cookies[name] = val

    def post(self, path, body=None, form_data=None, raw=False):
        url = BASE_URL + path
        headers = {}
        data = b""
        if form_data:
            boundary = "----boundary123xyz"
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
            body_parts = []
            for k, v in form_data.items():
                if isinstance(v, tuple):
                    filename, content, mime = v
                    body_parts.append(f"--{boundary}\r\n".encode())
                    body_parts.append(f'Content-Disposition: form-data; name="{k}"; filename="{filename}"\r\n'.encode())
                    body_parts.append(f"Content-Type: {mime}\r\n\r\n".encode())
                    body_parts.append(content if isinstance(content, bytes) else content.encode())
                    body_parts.append(b"\r\n")
                else:
                    body_parts.append(f"--{boundary}\r\n".encode())
                    body_parts.append(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
                    body_parts.append(str(v).encode())
                    body_parts.append(b"\r\n")
            body_parts.append(f"--{boundary}--\r\n".encode())
            data = b"".join(body_parts)
        else:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body or {}).encode()
        ch = self._cookie_header()
        if ch:
            headers["Cookie"] = ch
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                self._extract_cookies(resp)
                c = resp.read().decode()
                return resp.status, json.loads(c) if c and not raw else c
        except urllib.error.HTTPError as e:
            self._extract_cookies(e)
            c = e.read().decode()
            try:
                return e.code, json.loads(c)
            except Exception:
                return e.code, {"error": c or "empty"}

    def get(self, path):
        import urllib.parse as uparse
        # split path into base and query, encode query values
        if "?" in path:
            base, qs = path.split("?", 1)
            parts = uparse.parse_qsl(qs, keep_blank_values=True)
            qs_encoded = uparse.urlencode([(uparse.quote(k), uparse.quote(v)) for k, v in parts])
            path = base + "?" + qs_encoded
        url = BASE_URL + path
        req = urllib.request.Request(url, method="GET")
        ch = self._cookie_header()
        if ch:
            req.add_header("Cookie", ch)
        try:
            with urllib.request.urlopen(req) as resp:
                self._extract_cookies(resp)
                c = resp.read().decode()
                try:
                    return resp.status, json.loads(c)
                except Exception:
                    return resp.status, {"_raw": c}
        except urllib.error.HTTPError as e:
            self._extract_cookies(e)
            c = e.read().decode()
            try:
                return e.code, json.loads(c)
            except Exception:
                return e.code, {"error": c or "empty"}

def expect(label, cond, detail=""):
    if cond:
        print(f"  {OK} {label}  {detail}")
        return True
    else:
        print(f"  {FAIL} {label}  {detail}")
        return False

def assert_eq(label, actual, expected):
    return expect(label, actual == expected, f"got={actual!r}")

def run():
    print("=" * 70)
    print(" Shift Difference Registration - E2E Acceptance Test")
    print("=" * 70)

    clean_db()
    server = start_server()
    passed = 0
    failed = 0
    try:
        print("\n--- Test 1: Authentication ---")
        cash = Session()
        s, d = cash.post("/api/login", {"username":"cashier","password":"cashier123"})
        if assert_eq("Cashier login HTTP 200", s, 200): passed += 1
        else: failed += 1
        if assert_eq("Cashier role", d["user"]["role"], "cashier"): passed += 1
        else: failed += 1

        man = Session()
        s, d = man.post("/api/login", {"username":"manager","password":"wrongpass"})
        if assert_eq("Wrong password rejected", s, 401): passed += 1
        else: failed += 1
        man.post("/api/login", {"username":"manager","password":"manager123"})

        adm = Session()
        adm.post("/api/login", {"username":"admin","password":"admin123"})

        print("\n--- Test 2: Main flow create -> submit -> review -> close ---")
        s, d = cash.post("/api/vouchers", {
            "voucher_no":"T-MAIN-001",
            "shift_code":"早班",
            "shift_date":"2026-06-12",
            "cashier":"小张",
            "diff_amount": 15.50,
            "reason":"",
            "remark":"零钱袋长款15.5元",
        })
        vid1 = d.get("id")
        if expect("Create voucher OK", s == 200 and vid1 is not None, f"status={s}"): passed += 1
        else: failed += 1

        s, d = cash.post(f"/api/vouchers/{vid1}/submit", {"remark":"零钱袋长款15.5元"})
        if assert_eq("Submit for review", s, 200): passed += 1
        else: failed += 1
        s, d = cash.get(f"/api/vouchers/{vid1}")
        if assert_eq("After submit status=pending", d["voucher"]["status"], "pending"): passed += 1
        else: failed += 1

        s, d = man.post(f"/api/vouchers/{vid1}/review", {"action":"approve","note":"OK"})
        if assert_eq("Manager approves", s, 200): passed += 1
        else: failed += 1
        s, d = man.get(f"/api/vouchers/{vid1}")
        if assert_eq("After approve status=reviewed", d["voucher"]["status"], "reviewed"): passed += 1
        else: failed += 1

        s, d = man.post(f"/api/vouchers/{vid1}/close", {"note":"done"})
        if assert_eq("Manager closes", s, 200): passed += 1
        else: failed += 1
        s, d = man.get(f"/api/vouchers/{vid1}")
        if assert_eq("After close status=closed", d["voucher"]["status"], "closed"): passed += 1
        else: failed += 1

        tl = d["timeline"]
        actions = [t["action"] for t in tl]
        if expect("Timeline contains key actions",
            {"创建草稿","提交复核","复核通过","关闭"} <= set(actions), str(actions)): passed += 1
        else: failed += 1

        print("\n--- Test 3: Negative amount without reason rejected ---")
        s, d = cash.post("/api/vouchers", {
            "voucher_no":"T-NEG-001",
            "shift_code":"中班",
            "shift_date":"2026-06-12",
            "cashier":"小李",
            "diff_amount": -50.00,
            "reason":"",
            "remark":"",
        })
        if assert_eq("Create negative no reason rejected", s, 400): passed += 1
        else: failed += 1

        s, d = cash.post("/api/vouchers", {
            "voucher_no":"T-NEG-001",
            "shift_code":"中班",
            "shift_date":"2026-06-12",
            "cashier":"小李",
            "diff_amount": -50.00,
            "reason":"找零错误",
            "remark":"待核实",
        })
        vid2 = d.get("id")
        if expect("With reason created OK", s == 200 and vid2): passed += 1
        else: failed += 1
        cash.post(f"/api/vouchers/{vid2}/submit", {"reason":"找零错误"})

        print("\n--- Test 4: Cashier cannot close own voucher ---")
        man.post(f"/api/vouchers/{vid2}/review", {"action":"approve"})
        s, d = cash.post(f"/api/vouchers/{vid2}/close", {"note":"self close"})
        if assert_eq("Cashier closing own rejected", s, 403): passed += 1
        else: failed += 1

        print("\n--- Test 5: Returned voucher without new remark resubmission rejected ---")
        s, d = cash.post("/api/vouchers", {
            "voucher_no":"T-RET-001",
            "shift_code":"晚班",
            "shift_date":"2026-06-12",
            "cashier":"小张",
            "diff_amount": 30.00,
            "reason":"",
            "remark":"交班差异30元",
        })
        vid3 = d["id"]
        cash.post(f"/api/vouchers/{vid3}/submit", {"remark":"交班差异30元"})

        s, d = man.post(f"/api/vouchers/{vid3}/review", {"action":"return","note":"Attach photo ID"})
        if assert_eq("Manager returns OK", s, 200): passed += 1
        else: failed += 1

        s, d = cash.post(f"/api/vouchers/{vid3}/submit", {"remark":"交班差异30元"})
        if assert_eq("Resubmit without new remark rejected", s, 400): passed += 1
        else: failed += 1
        err = d.get("error","")
        if expect("Error mentions remark supplement", "补充备注" in err or "补充" in err, err): passed += 1
        else: failed += 1

        s, d = cash.post(f"/api/vouchers/{vid3}/submit", {
            "remark":"交班差异30元；凭证照片编号 P0923",
        })
        if assert_eq("Resubmit with new remark OK", s, 200): passed += 1
        else: failed += 1

        print("\n--- Test 6: CSV import - duplicate closed/no reason negative rejected ---")
        csv_text = (
            "单据编号,状态,班次,班次日期,收银员,差异金额,原因,备注,创建人\n"
            "T-MAIN-001,草稿,早班,2026-06-12,小张,15.5,,收银员导入,cashier\n"
            "T-NEW-IMP,草稿,晚班,2026-06-12,小李,200,,新单导入,cashier\n"
            "T-NEG-IMP,待复核,中班,2026-06-12,小李,-20,,无原因短款,cashier\n"
        ).encode("utf-8-sig")

        s, d = cash.post("/api/vouchers/import", form_data={
            "file": ("test.csv", csv_text, "text/csv")
        })
        print(f"    import result: total={d.get('total')} success={d.get('success')} failed={d.get('failed')}")
        errors = d.get("errors", [])
        has_closed_dup = any("已关闭" in e for e in errors)
        has_no_reason = any("负金额" in e and "原因" in e for e in errors)
        if expect("Closed voucher duplicate import rejected", has_closed_dup, str(errors[:3])): passed += 1
        else: failed += 1
        if expect("Negative no-reason import rejected", has_no_reason, str(errors[:3])): passed += 1
        else: failed += 1
        if expect("Valid voucher imported", d.get("success") >= 1): passed += 1
        else: failed += 1

        csv2 = ("单据编号,状态,班次,班次日期,收银员,差异金额,原因,备注,创建人\n"
                "T-NEW-IMP,草稿,晚班,2026-06-12,小李,200,,重复,cashier\n").encode("utf-8-sig")
        s, d = cash.post("/api/vouchers/import", form_data={
            "file": ("dup.csv", csv2, "text/csv")
        })
        if expect("Duplicate same voucher rejected", d.get("success") == 0 and d.get("failed") >= 1,
                f"success={d.get('success')} errors={d.get('errors',[])[:2]}"): passed += 1
        else: failed += 1

        print("\n--- Test 7: Revoke mechanism (history preserved) ---")
        man.post(f"/api/vouchers/{vid3}/review", {"action":"approve"})
        man.post(f"/api/vouchers/{vid3}/close", {"note":"end"})
        s, d = man.get(f"/api/vouchers/{vid3}")
        old_no = d["voucher"]["voucher_no"]
        old_amount = d["voucher"]["diff_amount"]

        s, d = man.post(f"/api/vouchers/{vid3}/revoke", {"reason":""})
        if assert_eq("Revoke without reason rejected", s, 400): passed += 1
        else: failed += 1

        s, d = man.post(f"/api/vouchers/{vid3}/revoke", {"reason":"原金额录入有误，应为35元"})
        if expect("Revoke OK and returns new voucher", s == 200 and d.get("new_voucher_no")): passed += 1
        else: failed += 1
        new_no = d.get("new_voucher_no")
        new_vid = d.get("new_id")

        s, d = man.get(f"/api/vouchers/{vid3}")
        if assert_eq("Old voucher status=revoked", d["voucher"]["status"], "revoked"): passed += 1
        else: failed += 1

        s, d = man.get(f"/api/vouchers/{new_vid}")
        v = d["voucher"]
        if assert_eq("New voucher status=draft", v["status"], "draft"): passed += 1
        else: failed += 1
        if assert_eq("New voucher parent = old voucher no", v["parent_voucher_no"], old_no): passed += 1
        else: failed += 1
        if assert_eq("New voucher inherits old amount", v["diff_amount"], old_amount): passed += 1
        else: failed += 1

        cash.post(f"/api/vouchers/{new_vid}/submit", {"diff_amount":35.00,"remark":"原30元更正为35元"})
        s, d = cash.get(f"/api/vouchers/{new_vid}")
        if assert_eq("Corrected voucher submitted OK", d["voucher"]["status"], "pending"): passed += 1
        else: failed += 1

        print("\n--- Test 8: Summary shows open count ---")
        s, d = man.get("/api/summary")
        print(f"    summary: open_count={d.get('open_count')} pending_count={d.get('pending_count')}")
        if expect("open_count is int >=0", isinstance(d.get("open_count"), int)): passed += 1
        else: failed += 1
        if expect("pending_count is int", isinstance(d.get("pending_count"), int)): passed += 1
        else: failed += 1
        if expect("pending_by_shift is dict", isinstance(d.get("pending_by_shift"), dict)): passed += 1
        else: failed += 1

        print("\n--- Test 9: List filtering by shift and handler ---")
        s, d = cash.get("/api/vouchers?shift_code=早班")
        if expect("Filter by shift returns list", isinstance(d.get("vouchers"), list)): passed += 1
        else: failed += 1

        s, d = man.get("/api/vouchers?handler=manager")
        if expect("Filter by handler returns list", isinstance(d.get("vouchers"), list)): passed += 1
        else: failed += 1

        print("\n--- Test 10: CSV export ---")
        url = BASE_URL + "/api/vouchers/export.csv"
        req = urllib.request.Request(url)
        cookie_header = man._cookie_header()
        if cookie_header:
            req.add_header("Cookie", cookie_header)
        with urllib.request.urlopen(req) as resp:
            content = resp.read().decode("utf-8")
        lines = content.splitlines()
        if expect("Export CSV >= header+2 rows", len(lines) >= 3, f"lines={len(lines)}"): passed += 1
        else: failed += 1
        header = lines[0]
        if expect("Header has required fields", all(k in header for k in ["单据编号","状态","班次","差异金额"]), header): passed += 1
        else: failed += 1
        if expect("Contains closed T-MAIN-001", "T-MAIN-001" in content): passed += 1
        else: failed += 1

        print("\n--- Test 11: Persistence - after restart pending/revoked survive ---")
        server.terminate()
        try:
            server.wait(timeout=5)
        except Exception:
            try: server.kill()
            except: pass
        time.sleep(2)

        con = sqlite3.connect(str(DB_PATH))
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT status FROM vouchers WHERE id = ?", (new_vid,)).fetchone()
        if assert_eq("Pending state persisted in DB", row["status"], "pending"): passed += 1
        else: failed += 1

        row = con.execute("SELECT status, revoked_by FROM vouchers WHERE id = ?", (vid3,)).fetchone()
        if assert_eq("Revoked state persisted in DB", row["status"], "revoked"): passed += 1
        else: failed += 1
        if expect("Revoked_by recorded", row["revoked_by"] is not None): passed += 1
        else: failed += 1

        tl = con.execute("SELECT action FROM timeline WHERE voucher_no = ? ORDER BY id", (old_no,)).fetchall()
        if expect("Timeline persisted >=4 entries", len(tl) >= 4, f"tl_count={len(tl)}"): passed += 1
        else: failed += 1
        con.close()

        server = start_server()
        time.sleep(0.5)
        man2 = Session()
        man2.post("/api/login", {"username":"manager","password":"manager123"})
        s, d = man2.get(f"/api/vouchers/{new_vid}")
        if assert_eq("After restart pending status visible", d["voucher"]["status"], "pending"): passed += 1
        else: failed += 1
        s, d = man2.get(f"/api/vouchers/{vid3}")
        if assert_eq("After restart revoked status visible", d["voucher"]["status"], "revoked"): passed += 1
        else: failed += 1

        print("\n" + "=" * 70)
        total = passed + failed
        print(f" COMPLETED: Total={total}  Passed={passed}  Failed={failed}")
        print("=" * 70)
        return failed == 0
    finally:
        try:
            server.terminate()
            try: server.wait(timeout=3)
            except: pass
        except Exception:
            try: server.kill()
            except: pass

if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
