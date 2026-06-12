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

    def put(self, path, body=None):
        url = BASE_URL + path
        headers = {"Content-Type": "application/json"}
        data = json.dumps(body or {}).encode()
        ch = self._cookie_header()
        if ch:
            headers["Cookie"] = ch
        req = urllib.request.Request(url, data=data, headers=headers, method="PUT")
        try:
            with urllib.request.urlopen(req) as resp:
                self._extract_cookies(resp)
                c = resp.read().decode()
                return resp.status, json.loads(c) if c else {}
        except urllib.error.HTTPError as e:
            self._extract_cookies(e)
            c = e.read().decode()
            try:
                return e.code, json.loads(c)
            except Exception:
                return e.code, {"error": c or "empty"}

    def delete(self, path):
        url = BASE_URL + path
        headers = {}
        ch = self._cookie_header()
        if ch:
            headers["Cookie"] = ch
        req = urllib.request.Request(url, headers=headers, method="DELETE")
        try:
            with urllib.request.urlopen(req) as resp:
                self._extract_cookies(resp)
                c = resp.read().decode()
                return resp.status, json.loads(c) if c else {}
        except urllib.error.HTTPError as e:
            self._extract_cookies(e)
            c = e.read().decode()
            try:
                return e.code, json.loads(c)
            except Exception:
                return e.code, {"error": c or "empty"}

    def get(self, path):
        import urllib.parse as uparse
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

        # ===================== ALERT RULE TESTS =====================

        print("\n--- Test 11: Alert rule CRUD ---")
        s, d = adm.post("/api/alert-rules", {
            "name": "单笔超500",
            "rule_type": "single_amount",
            "threshold": 500,
            "description": "单笔差异超500元预警",
            "enabled": True
        })
        if expect("Create single_amount rule OK", s == 200 and d.get("id"), f"status={s}"): passed += 1
        else: failed += 1
        rule_single_id = d.get("id")

        s, d = adm.post("/api/alert-rules", {
            "name": "累计超1000",
            "rule_type": "cumulative_amount",
            "threshold": 1000,
            "description": "收银员当天累计差异超1000元",
        })
        if expect("Create cumulative_amount rule OK", s == 200, f"status={s}"): passed += 1
        else: failed += 1

        s, d = adm.post("/api/alert-rules", {
            "name": "退回超2次",
            "rule_type": "consecutive_return",
            "threshold": 2,
            "description": "同一收银员退回2次以上",
        })
        if expect("Create consecutive_return rule OK", s == 200, f"status={s}"): passed += 1
        else: failed += 1
        rule_return_id = d.get("id")

        s, d = adm.post("/api/alert-rules", {
            "name": "单笔超500",
            "rule_type": "single_amount",
            "threshold": 999,
        })
        if assert_eq("Duplicate rule name rejected", s, 400): passed += 1
        else: failed += 1

        s, d = adm.post("/api/alert-rules", {
            "name": "",
            "rule_type": "single_amount",
            "threshold": 100,
        })
        if assert_eq("Empty name rejected", s, 400): passed += 1
        else: failed += 1

        s, d = adm.post("/api/alert-rules", {
            "name": "bad type",
            "rule_type": "invalid_type",
            "threshold": 100,
        })
        if assert_eq("Invalid type rejected", s, 400): passed += 1
        else: failed += 1

        s, d = adm.post("/api/alert-rules", {
            "name": "zero threshold",
            "rule_type": "single_amount",
            "threshold": 0,
        })
        if assert_eq("Zero threshold rejected", s, 400): passed += 1
        else: failed += 1

        s, d = adm.get("/api/alert-rules")
        if expect("List rules returns 3 rules", len(d.get("rules", [])) == 3, f"count={len(d.get('rules',[]))}"): passed += 1
        else: failed += 1

        s, d = adm.put(f"/api/alert-rules/{rule_single_id}", {"threshold": 300})
        if assert_eq("Update rule threshold OK", s, 200): passed += 1
        else: failed += 1

        s, d = adm.put(f"/api/alert-rules/{rule_single_id}", {"name": "累计超1000"})
        if assert_eq("Update to duplicate name rejected", s, 400): passed += 1
        else: failed += 1

        s, d = adm.put(f"/api/alert-rules/{rule_return_id}", {"enabled": False})
        if assert_eq("Disable rule OK", s, 200): passed += 1
        else: failed += 1

        print("\n--- Test 12: Cashier cannot manage alert rules ---")
        s, d = cash.post("/api/alert-rules", {
            "name": "cashier rule",
            "rule_type": "single_amount",
            "threshold": 100,
        })
        if assert_eq("Cashier create rule rejected (403)", s, 403): passed += 1
        else: failed += 1

        s, d = cash.put(f"/api/alert-rules/{rule_single_id}", {"threshold": 9999})
        if assert_eq("Cashier update rule rejected (403)", s, 403): passed += 1
        else: failed += 1

        s, d = cash.delete(f"/api/alert-rules/{rule_single_id}")
        if assert_eq("Cashier delete rule rejected (403)", s, 403): passed += 1
        else: failed += 1

        s, d = cash.get("/api/alert-rules")
        if assert_eq("Cashier cannot read rule config (403)", s, 403): passed += 1
        else: failed += 1

        s, d = cash.post("/api/alert-rules/import", form_data={
            "file": ("dummy.csv", "规则名称,规则类型,阈值\nx,single_amount,100\n".encode("utf-8-sig"), "text/csv")
        })
        if assert_eq("Cashier cannot import rules CSV (403)", s, 403): passed += 1
        else: failed += 1

        exp_req = urllib.request.Request(BASE_URL + "/api/alert-rules/export.csv")
        exp_ch = cash._cookie_header()
        if exp_ch: exp_req.add_header("Cookie", exp_ch)
        try:
            with urllib.request.urlopen(exp_req) as exp_resp:
                exp_status = exp_resp.status
        except urllib.error.HTTPError as e:
            exp_status = e.code
        if assert_eq("Cashier cannot export rules CSV (403)", exp_status, 403): passed += 1
        else: failed += 1

        print("\n--- Test 13: Alert triggered on voucher create/submit ---")
        s, d = cash.post("/api/vouchers", {
            "voucher_no":"T-ALERT-001",
            "shift_code":"早班",
            "shift_date":"2026-06-12",
            "cashier":"小王",
            "diff_amount": 600.00,
            "reason":"系统差异",
            "remark":"测试单笔预警",
        })
        vid_alert1 = d.get("id")
        if expect("Create voucher with alert trigger", s == 200 and vid_alert1, f"status={s}"): passed += 1
        else: failed += 1
        alerts1 = d.get("alerts", [])
        if expect("Create triggers single_amount alert", len(alerts1) > 0, f"alerts={alerts1}"): passed += 1
        else: failed += 1

        s, d = cash.post(f"/api/vouchers/{vid_alert1}/submit", {"remark":"测试单笔预警提交"})
        if assert_eq("Submit with alert still succeeds (200)", s, 200): passed += 1
        else: failed += 1
        submit_alerts = d.get("alerts", [])
        if expect("Submit triggers alert as well", len(submit_alerts) > 0, f"alerts={submit_alerts}"): passed += 1
        else: failed += 1

        s, d = cash.post("/api/vouchers", {
            "voucher_no":"T-NOALERT-001",
            "shift_code":"早班",
            "shift_date":"2026-06-12",
            "cashier":"小赵",
            "diff_amount": 50.00,
            "reason":"",
            "remark":"低于阈值不触发",
        })
        vid_noalert = d.get("id")
        if expect("Below threshold no alert", len(d.get("alerts", [])) == 0, f"alerts={d.get('alerts',[])}"): passed += 1
        else: failed += 1

        print("\n--- Test 14: Alert shown in voucher list and detail ---")
        s, d = cash.get("/api/vouchers")
        vouchers = d.get("vouchers", [])
        alert_voucher = next((v for v in vouchers if v["voucher_no"] == "T-ALERT-001"), None)
        if expect("Voucher list has warning_reasons", alert_voucher and len(alert_voucher.get("warning_reasons", [])) > 0,
                  f"reasons={alert_voucher.get('warning_reasons',[]) if alert_voucher else 'N/A'}"): passed += 1
        else: failed += 1

        no_alert_voucher = next((v for v in vouchers if v["voucher_no"] == "T-NOALERT-001"), None)
        if expect("Below threshold voucher has no warnings", no_alert_voucher and len(no_alert_voucher.get("warning_reasons", [])) == 0,
                  f"reasons={no_alert_voucher.get('warning_reasons',[]) if no_alert_voucher else 'N/A'}"): passed += 1
        else: failed += 1

        s, d = cash.get(f"/api/vouchers/{vid_alert1}")
        detail_alerts = d.get("alerts", [])
        if expect("Detail has alerts array", len(detail_alerts) > 0, f"alerts_count={len(detail_alerts)}"): passed += 1
        else: failed += 1
        if expect("Alert has rule_name and alert_reason",
                  detail_alerts[0].get("rule_name") and detail_alerts[0].get("alert_reason"),
                  f"alert={detail_alerts[0] if detail_alerts else 'N/A'}"): passed += 1
        else: failed += 1

        print("\n--- Test 15: Alert does not block existing flows ---")
        man.post(f"/api/vouchers/{vid_alert1}/review", {"action":"approve","note":"approved despite alert"})
        s, d = man.post(f"/api/vouchers/{vid_alert1}/close", {"note":"closed"})
        if assert_eq("Alerted voucher can be closed normally", s, 200): passed += 1
        else: failed += 1

        print("\n--- Test 16: Alert logs API ---")
        s, d = cash.get("/api/alert-logs")
        if expect("Alert logs returns list", isinstance(d.get("logs"), list) and len(d["logs"]) > 0,
                  f"count={len(d.get('logs',[]))}"): passed += 1
        else: failed += 1

        s, d = cash.get("/api/alert-logs?voucher_no=T-ALERT-001")
        if expect("Filter alert logs by voucher_no", len(d.get("logs", [])) > 0,
                  f"count={len(d.get('logs',[]))}"): passed += 1
        else: failed += 1

        print("\n--- Test 16b: Deduplication - same rule + same voucher = single alert log ---")
        con = sqlite3.connect(str(DB_PATH))
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT rule_id, COUNT(*) AS c FROM alert_logs WHERE voucher_no = ? GROUP BY rule_id",
            ("T-ALERT-001",)
        ).fetchall()
        con.close()
        all_single = all(r["c"] == 1 for r in rows)
        if expect("Each rule for T-ALERT-001 logged exactly once", all_single and len(rows) > 0,
                  f"counts={[(r['rule_id'], r['c']) for r in rows]}"): passed += 1
        else: failed += 1

        s, d = cash.get(f"/api/vouchers/{vid_alert1}")
        detail_alerts = d.get("alerts", [])
        rule_ids_in_detail = [a.get("rule_id") for a in detail_alerts]
        if expect("Detail alerts have no duplicates", len(rule_ids_in_detail) == len(set(rule_ids_in_detail)) and len(detail_alerts) > 0,
                  f"alerts={len(detail_alerts)} unique={len(set(rule_ids_in_detail))}"): passed += 1
        else: failed += 1

        s, d = cash.get("/api/vouchers")
        vouchers = d.get("vouchers", [])
        alert_voucher = next((v for v in vouchers if v["voucher_no"] == "T-ALERT-001"), None)
        wr = alert_voucher.get("warning_reasons", []) if alert_voucher else []
        if expect("Voucher list warning_reasons have no duplicates",
                  len(wr) == len(set(w.get("rule_name") for w in wr)) and len(wr) > 0,
                  f"wr_count={len(wr)}"): passed += 1
        else: failed += 1

        print("\n--- Test 16c: Cashier sees alert results but NOT rule configs ---")
        s, d = cash.get("/api/alert-rules")
        if expect("Cashier cannot fetch alert rules (403)", s == 403, f"status={s}"): passed += 1
        else: failed += 1

        s, d = cash.get("/api/alert-logs")
        if expect("Cashier CAN fetch alert logs (200)", s == 200 and isinstance(d.get("logs"), list),
                  f"status={s} has_logs={isinstance(d.get('logs'), list)}"): passed += 1
        else: failed += 1

        s, d = cash.get("/api/vouchers")
        vouchers = d.get("vouchers", [])
        alert_voucher = next((v for v in vouchers if v["voucher_no"] == "T-ALERT-001"), None)
        if expect("Cashier sees warning_reasons in voucher list",
                  alert_voucher and len(alert_voucher.get("warning_reasons", [])) > 0,
                  f"reasons={alert_voucher.get('warning_reasons',[]) if alert_voucher else 'N/A'}"): passed += 1
        else: failed += 1

        s, d = cash.get(f"/api/vouchers/{vid_alert1}")
        if expect("Cashier sees alerts in voucher detail",
                  len(d.get("alerts", [])) > 0 and d["alerts"][0].get("alert_reason"),
                  f"alerts={d.get('alerts', [])}"): passed += 1
        else: failed += 1

        print("\n--- Test 16d: Original flows still work (review/close/revoke regression) ---")
        s, d = cash.post("/api/vouchers", {
            "voucher_no":"T-REG-001",
            "shift_code":"晚班",
            "shift_date":"2026-06-12",
            "cashier":"小钱",
            "diff_amount": 10.00,
            "reason":"",
            "remark":"回归测试正常单据",
        })
        vid_reg = d.get("id")
        s, d = cash.post(f"/api/vouchers/{vid_reg}/submit", {"remark":"回归测试正常提交"})
        if assert_eq("Normal submit still works (200)", s, 200): passed += 1
        else: failed += 1

        s, d = man.post(f"/api/vouchers/{vid_reg}/review", {"action":"approve","note":"正常复核"})
        if assert_eq("Normal review still works (200)", s, 200): passed += 1
        else: failed += 1

        s, d = man.post(f"/api/vouchers/{vid_reg}/close", {"note":"正常关闭"})
        if assert_eq("Normal close still works (200)", s, 200): passed += 1
        else: failed += 1

        s, d = man.post(f"/api/vouchers/{vid_reg}/revoke", {"reason":"回归测试撤销"})
        if assert_eq("Normal revoke still works (200)", s, 200): passed += 1
        else: failed += 1
        if expect("Revoke returns new voucher_no", bool(d.get("new_voucher_no")),
                  f"new_no={d.get('new_voucher_no')}"): passed += 1
        else: failed += 1

        print("\n--- Test 17: Alert rules CSV import/export ---")
        url = BASE_URL + "/api/alert-rules/export.csv"
        req = urllib.request.Request(url)
        cookie_header = adm._cookie_header()
        if cookie_header:
            req.add_header("Cookie", cookie_header)
        with urllib.request.urlopen(req) as resp:
            rules_csv = resp.read().decode("utf-8")
        if expect("Export rules CSV has header", "规则名称" in rules_csv, f"csv_start={rules_csv[:100]}"): passed += 1
        else: failed += 1

        rules_csv_with_dup = (
            "规则名称,规则类型,阈值,是否启用,描述\n"
            "单笔超500,single_amount,300,是,同名规则\n"
            "导入新规则,single_amount,888,是,新导入的规则\n"
        ).encode("utf-8-sig")
        s, d = adm.post("/api/alert-rules/import", form_data={
            "file": ("rules.csv", rules_csv_with_dup, "text/csv")
        })
        if expect("Import rules: skip duplicate, add new",
                  d.get("skipped", -1) >= 1 and d.get("success", -1) >= 1,
                  f"success={d.get('success')} skipped={d.get('skipped')} failed={d.get('failed')} details={d.get('details',[])}"): passed += 1
        else: failed += 1
        has_skip_detail = any("已存在" in dt for dt in d.get("details", []))
        if expect("Skip detail mentions name conflict", has_skip_detail, f"details={d.get('details',[])}"): passed += 1
        else: failed += 1

        s, d = adm.get("/api/alert-rules")
        if expect("After import new rule exists", any(r["name"] == "导入新规则" for r in d["rules"]),
                  f"names={[r['name'] for r in d['rules']]}"): passed += 1
        else: failed += 1

        print("\n--- Test 18: Operation logs ---")
        s, d = adm.get("/api/operation-logs")
        if expect("Operation logs returns list", isinstance(d.get("logs"), list) and len(d["logs"]) > 0,
                  f"count={len(d.get('logs',[]))}"): passed += 1
        else: failed += 1
        has_rule_log = any("预警规则" in l.get("action", "") for l in d["logs"])
        if expect("Operation logs contain rule actions", has_rule_log,
                  f"actions={[l.get('action') for l in d['logs'][:5]]}"): passed += 1
        else: failed += 1

        s, d = cash.get("/api/operation-logs")
        if assert_eq("Cashier cannot read operation logs (403)", s, 403): passed += 1
        else: failed += 1

        print("\n--- Test 19: Delete alert rule ---")
        s, d = adm.delete(f"/api/alert-rules/{rule_single_id}")
        if assert_eq("Delete rule OK", s, 200): passed += 1
        else: failed += 1

        s, d = adm.delete(f"/api/alert-rules/{rule_single_id}")
        if assert_eq("Delete non-existent rule 404", s, 404): passed += 1
        else: failed += 1

        print("\n--- Test 20: Alert rule persistence after restart ---")
        server.terminate()
        try:
            server.wait(timeout=5)
        except Exception:
            try: server.kill()
            except: pass
        time.sleep(2)

        con = sqlite3.connect(str(DB_PATH))
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT COUNT(*) AS c FROM alert_rules").fetchone()
        rule_count = row["c"]
        if expect("Alert rules persisted in DB", rule_count >= 2, f"count={rule_count}"): passed += 1
        else: failed += 1

        row = con.execute("SELECT COUNT(*) AS c FROM alert_logs").fetchone()
        log_count = row["c"]
        if expect("Alert logs persisted in DB", log_count > 0, f"count={log_count}"): passed += 1
        else: failed += 1

        row = con.execute("SELECT COUNT(*) AS c FROM operation_log").fetchone()
        op_count = row["c"]
        if expect("Operation logs persisted in DB", op_count > 0, f"count={op_count}"): passed += 1
        else: failed += 1
        con.close()

        server = start_server()
        time.sleep(0.5)
        man2 = Session()
        man2.post("/api/login", {"username":"manager","password":"manager123"})
        s, d = man2.get("/api/alert-rules")
        if expect("Rules still present after restart", len(d.get("rules", [])) >= 2,
                  f"count={len(d.get('rules',[]))}"): passed += 1
        else: failed += 1

        s, d = man2.get("/api/alert-logs")
        if expect("Alert logs still present after restart", len(d.get("logs", [])) > 0,
                  f"count={len(d.get('logs',[]))}"): passed += 1
        else: failed += 1

        print("\n--- Test 21: Original flow still works after alert feature ---")
        s, d = man2.get(f"/api/vouchers/{new_vid}")
        if assert_eq("After restart pending status still visible", d["voucher"]["status"], "pending"): passed += 1
        else: failed += 1
        s, d = man2.get(f"/api/vouchers/{vid3}")
        if assert_eq("After restart revoked status still visible", d["voucher"]["status"], "revoked"): passed += 1
        else: failed += 1

        print("\n--- Test 22: Alert rule import conflict detail written to operation log ---")
        rules_csv_bad = (
            "规则名称,规则类型,阈值,是否启用,描述\n"
            "bad_rule,invalid_type,100,是,坏类型\n"
        ).encode("utf-8-sig")
        s, d = man2.post("/api/alert-rules/import", form_data={
            "file": ("bad.csv", rules_csv_bad, "text/csv")
        })
        if expect("Import with bad type: failed >= 1", d.get("failed", 0) >= 1,
                  f"failed={d.get('failed')} details={d.get('details',[])}"): passed += 1
        else: failed += 1

        s, d = man2.get("/api/operation-logs")
        has_import_log = any("导入预警规则" in l.get("action", "") for l in d.get("logs", []))
        if expect("Import attempt logged in operation log", has_import_log,
                  f"actions={[l.get('action') for l in d['logs'][:5]]}"): passed += 1
        else: failed += 1

        print("\n--- Test 23: Disposition Permission Tests ---")
        adm2 = Session()
        adm2.post("/api/login", {"username":"admin","password":"admin123"})
        man2 = Session()
        man2.post("/api/login", {"username":"manager","password":"manager123"})
        cash2 = Session()
        cash2.post("/api/login", {"username":"cashier","password":"cashier123"})

        s, d = adm2.post("/api/alert-rules", {
            "name": "单笔超100",
            "rule_type": "single_amount",
            "threshold": 100,
            "description": "测试处置权限",
            "enabled": True
        })

        s, d = cash2.post("/api/vouchers", {
            "voucher_no":"T-DISP-001",
            "shift_code":"早班",
            "shift_date":"2026-06-12",
            "cashier":"测试员",
            "diff_amount": 500.00,
            "reason":"系统差异",
            "remark":"测试处置",
        })
        vid_disp = d.get("id")

        s, d = cash2.get(f"/api/vouchers/{vid_disp}")
        alerts = d.get("alerts", [])
        if expect("Voucher has alerts for disposition test", len(alerts) > 0, f"alerts={len(alerts)}"): passed += 1
        else: failed += 1

        alert_id = alerts[0]["id"]
        alert_version = alerts[0]["disposition_version"]

        s, d = cash2.post(f"/api/alert-logs/{alert_id}/disposition", {
            "disposition_status": "confirmed",
            "disposition_note": "收银员尝试处置",
            "disposition_version": alert_version
        })
        if assert_eq("Cashier cannot update disposition (403)", s, 403): passed += 1
        else: failed += 1

        s, d = man2.post(f"/api/alert-logs/{alert_id}/disposition", {
            "disposition_status": "confirmed",
            "disposition_note": "值班长已核实，确认为系统差异",
            "disposition_version": alert_version
        })
        if expect("Manager can update disposition (200)", s == 200 and d.get("ok"), f"status={s}"): passed += 1
        else: failed += 1
        if expect("Disposition updated with handler", d.get("alert", {}).get("disposition_handler") == "manager",
                  f"handler={d.get('alert',{}).get('disposition_handler')}"): passed += 1
        else: failed += 1
        if expect("Disposition version incremented", d.get("alert", {}).get("disposition_version") == alert_version + 1,
                  f"version={d.get('alert',{}).get('disposition_version')}"): passed += 1
        else: failed += 1

        print("\n--- Test 24: Disposition Conflict Detection ---")
        s, d = man2.get(f"/api/vouchers/{vid_disp}")
        current_alert = d.get("alerts", [])[0]
        current_version = current_alert["disposition_version"]

        man3 = Session()
        man3.post("/api/login", {"username":"manager","password":"manager123"})
        s2, d2 = man3.post(f"/api/alert-logs/{alert_id}/disposition", {
            "disposition_status": "follow_up",
            "disposition_note": "经理B处置：需要转财务核实",
            "disposition_version": current_version
        })
        if expect("Manager B first disposition succeeds", s2 == 200, f"status={s2}"): passed += 1
        else: failed += 1

        s, d = man2.post(f"/api/alert-logs/{alert_id}/disposition", {
            "disposition_status": "ignored",
            "disposition_note": "经理A处置：误报，忽略",
            "disposition_version": current_version
        })
        if assert_eq("Stale disposition rejected with 409 conflict", s, 409): passed += 1
        else: failed += 1
        if expect("Conflict error mentions other user processing",
                  "已被其他用户处理过" in (d.get("error","") or ""),
                  f"error={d.get('error','')}"): passed += 1
        else: failed += 1
        if expect("Conflict response includes current state", d.get("current") is not None): passed += 1
        else: failed += 1

        print("\n--- Test 25: Disposition Persistence Across Restart ---")
        s, d = man2.get(f"/api/vouchers/{vid_disp}")
        alerts_before = d.get("alerts", [])
        disp_before = alerts_before[0]

        server.terminate()
        try: server.wait(timeout=5)
        except Exception:
            try: server.kill()
            except: pass
        time.sleep(2)

        con = sqlite3.connect(str(DB_PATH))
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM alert_logs WHERE id = ?", (alert_id,)).fetchone()
        if expect("Disposition persisted in DB", row["disposition_status"] == "follow_up" and row["disposition_handler"] == "manager",
                  f"status={row['disposition_status']} handler={row['disposition_handler']}"): passed += 1
        else: failed += 1
        con.close()

        server = start_server()
        time.sleep(0.5)
        man4 = Session()
        man4.post("/api/login", {"username":"manager","password":"manager123"})

        s, d = man4.get(f"/api/vouchers/{vid_disp}")
        alerts_after = d.get("alerts", [])
        if expect("After restart disposition still present", len(alerts_after) > 0, f"alerts={len(alerts_after)}"): passed += 1
        else: failed += 1
        disp_after = alerts_after[0]
        if expect("After restart status=follow_up", disp_after["disposition_status"] == "follow_up",
                  f"status={disp_after['disposition_status']}"): passed += 1
        else: failed += 1
        if expect("After restart handler=manager", disp_after["disposition_handler"] == "manager",
                  f"handler={disp_after['disposition_handler']}"): passed += 1
        else: failed += 1
        if expect("After restart note preserved", "转财务核实" in (disp_after["disposition_note"] or ""),
                  f"note={disp_after['disposition_note']}"): passed += 1
        else: failed += 1

        print("\n--- Test 26: Disposition Filtering ---")
        s, d = man4.get("/api/alert-logs?disposition_status=unprocessed")
        unprocessed = [l for l in d.get("logs", []) if l["voucher_no"] == "T-DISP-001"]
        if expect("Filter unprocessed returns 0 for our voucher", len(unprocessed) == 0,
                  f"count={len(unprocessed)}"): passed += 1
        else: failed += 1

        s, d = man4.get("/api/alert-logs?disposition_status=follow_up")
        follow_up = [l for l in d.get("logs", []) if l["id"] == alert_id]
        if expect("Filter follow_up returns our alert", len(follow_up) == 1,
                  f"count={len(follow_up)}"): passed += 1
        else: failed += 1

        s, d = man4.get("/api/vouchers?alert_disposition=follow_up")
        vouchers = [v for v in d.get("vouchers", []) if v["voucher_no"] == "T-DISP-001"]
        if expect("Voucher filter by alert_disposition=follow_up works", len(vouchers) == 1,
                  f"count={len(vouchers)}"): passed += 1
        else: failed += 1

        s, d = man4.get("/api/vouchers?alert_disposition=unprocessed")
        vouchers2 = [v for v in d.get("vouchers", []) if v["voucher_no"] == "T-DISP-001"]
        if expect("Voucher filter by alert_disposition=unprocessed excludes our voucher", len(vouchers2) == 0,
                  f"count={len(vouchers2)}"): passed += 1
        else: failed += 1

        print("\n--- Test 27: CSV Export Includes Disposition Fields ---")
        url = BASE_URL + "/api/vouchers/export.csv"
        req = urllib.request.Request(url)
        cookie_header = man4._cookie_header()
        if cookie_header:
            req.add_header("Cookie", cookie_header)
        with urllib.request.urlopen(req) as resp:
            csv_content = resp.read().decode("utf-8")
        lines = csv_content.splitlines()
        header = lines[0]
        if expect("CSV header has disposition fields",
                  all(k in header for k in ["处置状态","处置备注","处理人","处理时间"]),
                  f"header={header}"): passed += 1
        else: failed += 1

        has_disp_row = any("T-DISP-001" in line and "需跟进" in line and "manager" in line for line in lines)
        if expect("CSV data includes disposition values", has_disp_row,
                  f"found_disp={has_disp_row}"): passed += 1
        else: failed += 1

        print("\n--- Test 28: Revoked/New Voucher Alert Isolation ---")
        s, d = cash2.post("/api/vouchers", {
            "voucher_no":"T-REV-DISP-001",
            "shift_code":"中班",
            "shift_date":"2026-06-12",
            "cashier":"测试员",
            "diff_amount": 200.00,
            "reason":"系统差异",
            "remark":"测试撤销后预警隔离",
        })
        vid_rev = d.get("id")
        cash2.post(f"/api/vouchers/{vid_rev}/submit", {"remark":"提交"})
        man4.post(f"/api/vouchers/{vid_rev}/review", {"action":"approve"})

        s, d = man4.get(f"/api/vouchers/{vid_rev}")
        old_alerts = d.get("alerts", [])
        old_alert_id = old_alerts[0]["id"] if old_alerts else None
        if expect("Original voucher has alerts", old_alert_id is not None): passed += 1
        else: failed += 1

        s, d = man4.post(f"/api/alert-logs/{old_alert_id}/disposition", {
            "disposition_status": "confirmed",
            "disposition_note": "原单已确认处理",
            "disposition_version": old_alerts[0]["disposition_version"]
        })
        if expect("Original alert disposition set", s == 200): passed += 1
        else: failed += 1

        s, d = man4.post(f"/api/vouchers/{vid_rev}/revoke", {"reason":"原金额有误需更正"})
        new_vid = d.get("new_id")
        new_no = d.get("new_voucher_no")
        if expect("Revoke successful", s == 200 and new_vid and new_no): passed += 1
        else: failed += 1

        s, d = cash2.post(f"/api/vouchers/{new_vid}/submit", {"diff_amount":250.00,"remark":"更正后金额250元"})

        s, d = man4.get(f"/api/vouchers/{new_vid}")
        new_alerts = d.get("alerts", [])
        if expect("New voucher has its own alerts", len(new_alerts) > 0, f"new_alerts={len(new_alerts)}"): passed += 1
        else: failed += 1

        new_alert_ids = [a["id"] for a in new_alerts]
        if expect("New alert IDs different from old", old_alert_id not in new_alert_ids,
                  f"old_id={old_alert_id} new_ids={new_alert_ids}"): passed += 1
        else: failed += 1

        for na in new_alerts:
            if expect("New alerts start as unprocessed", na["disposition_status"] == "unprocessed",
                      f"status={na['disposition_status']}"): passed += 1
            else: failed += 1
            if expect("New alerts have no handler", na.get("disposition_handler") is None,
                      f"handler={na.get('disposition_handler')}"): passed += 1
            else: failed += 1

        s, d = man4.get(f"/api/vouchers/{vid_rev}")
        old_alerts_after = d.get("alerts", [])
        old_disp = old_alerts_after[0] if old_alerts_after else None
        if expect("Original voucher disposition unchanged after revoke",
                  old_disp and old_disp["disposition_status"] == "confirmed",
                  f"status={old_disp['disposition_status'] if old_disp else 'N/A'}"): passed += 1
        else: failed += 1

        print("\n--- Test 29: Cashier Sees Disposition Results But Cannot Edit ---")
        s, d = cash2.get(f"/api/vouchers/{vid_disp}")
        alerts_cashier = d.get("alerts", [])
        if expect("Cashier can see disposition status", len(alerts_cashier) > 0 and alerts_cashier[0]["disposition_status"] == "follow_up",
                  f"status={alerts_cashier[0]['disposition_status'] if alerts_cashier else 'N/A'}"): passed += 1
        else: failed += 1
        if expect("Cashier can see disposition handler", alerts_cashier[0].get("disposition_handler") == "manager",
                  f"handler={alerts_cashier[0].get('disposition_handler')}"): passed += 1
        else: failed += 1
        if expect("Cashier can see disposition note", "转财务核实" in (alerts_cashier[0].get("disposition_note") or ""),
                  f"note={alerts_cashier[0].get('disposition_note')}"): passed += 1
        else: failed += 1

        s, d = cash2.get("/api/alert-logs")
        logs_cashier = d.get("logs", [])
        disp_logs = [l for l in logs_cashier if l["id"] == alert_id]
        if expect("Cashier can see disposition in alert logs", len(disp_logs) > 0 and disp_logs[0]["disposition_status"] == "follow_up",
                  f"status={disp_logs[0]['disposition_status'] if disp_logs else 'N/A'}"): passed += 1
        else: failed += 1

        s, d = cash2.get("/api/alert-rules")
        if assert_eq("Cashier still cannot see alert rules config (403)", s, 403): passed += 1
        else: failed += 1

        print("\n--- Test 30: Default disposition status is unprocessed ---")
        s, d = cash2.post("/api/vouchers", {
            "voucher_no":"T-DISP-DEFAULT",
            "shift_code":"晚班",
            "shift_date":"2026-06-12",
            "cashier":"测试员",
            "diff_amount": 150.00,
            "reason":"系统差异",
            "remark":"测试默认处置状态",
        })
        vid_default = d.get("id")
        s, d = cash2.get(f"/api/vouchers/{vid_default}")
        default_alerts = d.get("alerts", [])
        if expect("New alerts default to unprocessed",
                  len(default_alerts) > 0 and all(a["disposition_status"] == "unprocessed" for a in default_alerts),
                  f"statuses={[a['disposition_status'] for a in default_alerts]}"): passed += 1
        else: failed += 1
        if expect("New alerts have version 0",
                  all(a["disposition_version"] == 0 for a in default_alerts),
                  f"versions={[a['disposition_version'] for a in default_alerts]}"): passed += 1
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
