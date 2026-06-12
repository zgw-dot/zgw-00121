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

        print("\n--- Test 28: Revoke New Draft Has Immediate Alerts (Bug Fix) ---")
        s, d = cash2.post("/api/vouchers", {
            "voucher_no":"T-REV-IMM-001",
            "shift_code":"早班",
            "shift_date":"2026-06-12",
            "cashier":"测试员",
            "diff_amount": 300.00,
            "reason":"系统差异",
            "remark":"测试撤销后草稿立即可见预警",
        })
        vid_imm = d.get("id")
        cash2.post(f"/api/vouchers/{vid_imm}/submit", {"remark":"提交"})
        man4.post(f"/api/vouchers/{vid_imm}/review", {"action":"approve"})

        s, d = man4.get(f"/api/vouchers/{vid_imm}")
        old_alerts_imm = d.get("alerts", [])
        old_alert_id_imm = old_alerts_imm[0]["id"] if old_alerts_imm else None
        if expect("Original voucher has alerts before revoke", old_alert_id_imm is not None,
                  f"old_alerts={len(old_alerts_imm)}"): passed += 1
        else: failed += 1

        s, d = man4.post(f"/api/alert-logs/{old_alert_id_imm}/disposition", {
            "disposition_status": "confirmed",
            "disposition_note": "原单已确认处理完毕",
            "disposition_version": old_alerts_imm[0]["disposition_version"]
        })
        if expect("Original alert disposition set to confirmed", s == 200): passed += 1
        else: failed += 1

        s, d = man4.post(f"/api/vouchers/{vid_imm}/revoke", {"reason":"金额有误需更正"})
        new_vid_imm = d.get("new_id")
        new_no_imm = d.get("new_voucher_no")
        if expect("Revoke succeeds and returns new_id", s == 200 and new_vid_imm and new_no_imm,
                  f"new_id={new_vid_imm} new_no={new_no_imm}"): passed += 1
        else: failed += 1

        s, d = man4.get(f"/api/vouchers/{new_vid_imm}")
        new_alerts_immediate = d.get("alerts", [])
        if expect("BUG FIX: New draft has alerts IMMEDIATELY after revoke (before submit)",
                  len(new_alerts_immediate) > 0, f"alerts_count={len(new_alerts_immediate)}"): passed += 1
        else: failed += 1

        new_alert_ids_imm = [a["id"] for a in new_alerts_immediate]
        if expect("New alert IDs are different from original (independent records)",
                  old_alert_id_imm not in new_alert_ids_imm,
                  f"old_id={old_alert_id_imm} new_ids={new_alert_ids_imm}"): passed += 1
        else: failed += 1

        for na in new_alerts_immediate:
            if expect("New alert disposition is unprocessed (NOT copied from original)",
                      na["disposition_status"] == "unprocessed",
                      f"status={na['disposition_status']}"): passed += 1
            else: failed += 1
            if expect("New alert disposition note is empty (NOT copied)",
                      na.get("disposition_note") in (None, ""),
                      f"note={na.get('disposition_note')}"): passed += 1
            else: failed += 1
            if expect("New alert disposition handler is None (NOT copied)",
                      na.get("disposition_handler") is None,
                      f"handler={na.get('disposition_handler')}"): passed += 1
            else: failed += 1
            if expect("New alert disposition version is 0 (NOT copied)",
                      na["disposition_version"] == 0,
                      f"version={na['disposition_version']}"): passed += 1
            else: failed += 1
            if expect("New alert has voucher_id matching new voucher",
                      na.get("voucher_id") == new_vid_imm or na.get("voucher_no") == new_no_imm,
                      f"voucher_id={na.get('voucher_id')} voucher_no={na.get('voucher_no')}"): passed += 1
            else: failed += 1

        s, d = man4.get(f"/api/vouchers/{vid_imm}")
        old_alerts_after_revoke = d.get("alerts", [])
        old_disp_imm = old_alerts_after_revoke[0] if old_alerts_after_revoke else None
        if expect("Original voucher disposition unchanged after revoke (isolation)",
                  old_disp_imm and old_disp_imm["disposition_status"] == "confirmed",
                  f"status={old_disp_imm['disposition_status'] if old_disp_imm else 'N/A'}"): passed += 1
        else: failed += 1
        if expect("Original handler remains manager after revoke",
                  old_disp_imm and old_disp_imm["disposition_handler"] == "manager",
                  f"handler={old_disp_imm['disposition_handler'] if old_disp_imm else 'N/A'}"): passed += 1
        else: failed += 1

        print("\n--- Test 28b: Alert Log Filtering & CSV for Revoked New Draft ---")
        s, d = man4.get("/api/alert-logs?disposition_status=unprocessed")
        unproc_for_new = [l for l in d.get("logs", []) if l["voucher_no"] == new_no_imm]
        if expect("Alert logs filter by unprocessed returns new draft alerts",
                  len(unproc_for_new) >= 1, f"count={len(unproc_for_new)}"): passed += 1
        else: failed += 1

        s, d = man4.get(f"/api/alert-logs?voucher_no={new_no_imm}")
        new_draft_logs = d.get("logs", [])
        if expect("Alert logs by voucher_no for new draft shows disposition fields",
                  len(new_draft_logs) >= 1 and new_draft_logs[0]["disposition_status"] == "unprocessed",
                  f"logs={len(new_draft_logs)} status={new_draft_logs[0].get('disposition_status') if new_draft_logs else 'N/A'}"): passed += 1
        else: failed += 1

        s, d = man4.get("/api/vouchers?alert_disposition=unprocessed")
        v_with_unproc = [v for v in d.get("vouchers", []) if v["voucher_no"] == new_no_imm]
        if expect("Voucher list filter by alert_disposition=unprocessed includes new draft",
                  len(v_with_unproc) == 1, f"count={len(v_with_unproc)}"): passed += 1
        else: failed += 1

        s, d = man4.get("/api/vouchers?alert_disposition=confirmed")
        v_with_conf = [v for v in d.get("vouchers", []) if v["voucher_no"] == new_no_imm]
        if expect("Voucher list filter by alert_disposition=confirmed excludes new draft",
                  len(v_with_conf) == 0, f"count={len(v_with_conf)}"): passed += 1
        else: failed += 1

        url2 = BASE_URL + "/api/vouchers/export.csv"
        req2 = urllib.request.Request(url2)
        cookie2 = man4._cookie_header()
        if cookie2:
            req2.add_header("Cookie", cookie2)
        with urllib.request.urlopen(req2) as resp2:
            csv2 = resp2.read().decode("utf-8")
        lines2 = csv2.splitlines()
        new_voucher_rows = [ln for ln in lines2 if new_no_imm in ln]
        if expect("CSV export includes new draft voucher alert rows",
                  len(new_voucher_rows) >= 1, f"rows={len(new_voucher_rows)}"): passed += 1
        else: failed += 1
        has_unproc_in_csv = any("未处理" in ln for ln in new_voucher_rows)
        if expect("CSV export shows '未处理' disposition for new draft alerts",
                  has_unproc_in_csv, f"has_unproc={has_unproc_in_csv}"): passed += 1
        else: failed += 1
        csv_headers = lines2[0]
        if expect("CSV export headers include all disposition columns",
                  all(k in csv_headers for k in ["处置状态","处置备注","处理人","处理时间","预警规则","预警原因"]),
                  f"header={csv_headers}"): passed += 1
        else: failed += 1

        print("\n--- Test 28c: Disposition New & Old Independent After Submit ---")
        s, d = man4.post(f"/api/alert-logs/{new_alert_ids_imm[0]}/disposition", {
            "disposition_status": "follow_up",
            "disposition_note": "新单需财务复核金额",
            "disposition_version": 0
        })
        if expect("Can disposition new draft alert independently", s == 200): passed += 1
        else: failed += 1

        s, d = man4.get(f"/api/vouchers/{vid_imm}")
        old_check = d.get("alerts", [])[0] if d.get("alerts") else None
        if expect("Original alert disposition still confirmed after new alert changed",
                  old_check and old_check["disposition_status"] == "confirmed",
                  f"status={old_check['disposition_status'] if old_check else 'N/A'}"): passed += 1
        else: failed += 1

        s, d = cash2.post(f"/api/vouchers/{new_vid_imm}/submit",
                          {"diff_amount": 350.00, "remark":"更正后金额350元，财务已复核"})
        if expect("Submit new draft after revoke still works (no regression)",
                  s == 200, f"status={s}"): passed += 1
        else: failed += 1

        s, d = man4.get(f"/api/vouchers/{new_vid_imm}")
        alerts_after_submit = d.get("alerts", [])
        if expect("Alerts still exist after submit (no duplicate explosion)",
                  len(alerts_after_submit) >= 1, f"count={len(alerts_after_submit)}"): passed += 1
        else: failed += 1
        submit_alert_ids = {a["id"] for a in alerts_after_submit}
        if expect("Alert IDs remain stable after submit (same records, not recreated)",
                  new_alert_ids_imm[0] in submit_alert_ids,
                  f"orig={new_alert_ids_imm[0]} after_submit={submit_alert_ids}"): passed += 1
        else: failed += 1

        print("\n--- Test 28d: Cashier Sees New Draft Alerts & Disposition (Read-Only) ---")
        s, d = cash2.get(f"/api/vouchers/{new_vid_imm}")
        cash_new_alerts = d.get("alerts", [])
        if expect("Cashier can see new draft alert disposition results",
                  len(cash_new_alerts) >= 1 and cash_new_alerts[0].get("disposition_status") == "follow_up",
                  f"status={cash_new_alerts[0].get('disposition_status') if cash_new_alerts else 'N/A'}"): passed += 1
        else: failed += 1
        if expect("Cashier can see disposition handler on new draft alerts",
                  cash_new_alerts[0].get("disposition_handler") == "manager",
                  f"handler={cash_new_alerts[0].get('disposition_handler') if cash_new_alerts else 'N/A'}"): passed += 1
        else: failed += 1

        s, d = cash2.post(f"/api/alert-logs/{new_alert_ids_imm[0]}/disposition", {
            "disposition_status": "ignored",
            "disposition_note": "收银员尝试改",
            "disposition_version": 1
        })
        if assert_eq("Cashier still cannot modify disposition on new draft alerts (403)", s, 403): passed += 1
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

        print("\n--- Test 31: Batch Disposition - Permission Denied for Cashier ---")
        s, d = cash2.get(f"/api/vouchers/{vid_disp}")
        batch_alerts = d.get("alerts", [])
        batch_test_ids = [{"id": a["id"], "disposition_version": a["disposition_version"]} for a in batch_alerts]
        s, d = cash2.post("/api/alert-logs/batch-disposition", {
            "disposition_status": "confirmed",
            "disposition_note": "收银员尝试批量处置",
            "items": batch_test_ids
        })
        if assert_eq("Cashier cannot batch disposition (403)", s, 403): passed += 1
        else: failed += 1

        print("\n--- Test 32: Batch Disposition - Empty Items Validation ---")
        s, d = man4.post("/api/alert-logs/batch-disposition", {
            "disposition_status": "confirmed",
            "disposition_note": "空列表",
            "items": []
        })
        if assert_eq("Batch with empty items returns 400", s, 400): passed += 1
        else: failed += 1

        s, d = man4.post("/api/alert-logs/batch-disposition", {
            "disposition_status": "invalid_status",
            "disposition_note": "无效状态",
            "items": batch_test_ids
        })
        if assert_eq("Batch with invalid status returns 400", s, 400): passed += 1
        else: failed += 1

        print("\n--- Test 33: Batch Disposition - Not Found and Success Mixed ---")
        s, d = man4.get(f"/api/vouchers/{vid_disp}")
        real_alerts = d.get("alerts", [])
        real_alert_ids = [{"id": a["id"], "disposition_version": a["disposition_version"]} for a in real_alerts]
        fake_ids = [{"id": 99999, "disposition_version": 0}, {"id": 99998, "disposition_version": 0}]
        mixed_items = real_alert_ids + fake_ids

        s, d = man4.post("/api/alert-logs/batch-disposition", {
            "disposition_status": "confirmed",
            "disposition_note": "批量处置：已核实确认",
            "items": mixed_items
        })
        if expect("Batch disposition returns 200 with mixed results", s == 200 and d.get("ok"),
                  f"status={s} ok={d.get('ok')}"): passed += 1
        else: failed += 1

        summary = d.get("summary", {})
        results = d.get("results", {})
        if expect("Summary has success count >= real alerts count",
                  summary.get("success", 0) >= len(real_alerts),
                  f"summary={summary} real={len(real_alerts)}"): passed += 1
        else: failed += 1
        if assert_eq("Summary has not_found=2", summary.get("not_found", 0), 2): passed += 1
        else: failed += 1
        if expect("Success results have updated disposition",
                  len(results.get("success", [])) > 0 and
                  all(r.get("alert", {}).get("disposition_status") == "confirmed" for r in results.get("success", [])),
                  f"first_success={results.get('success', [{}])[0].get('alert', {}).get('disposition_status') if results.get('success') else 'N/A'}"): passed += 1
        else: failed += 1
        if expect("Success results have handler=manager",
                  all(r.get("alert", {}).get("disposition_handler") == "manager" for r in results.get("success", [])),
                  f"handlers={[r.get('alert',{}).get('disposition_handler') for r in results.get('success',[])]}"): passed += 1
        else: failed += 1
        if expect("Success results have version incremented",
                  all(int(r.get("alert", {}).get("disposition_version", -1)) > 0 for r in results.get("success", [])),
                  f"versions={[r.get('alert',{}).get('disposition_version') for r in results.get('success',[])]}"): passed += 1
        else: failed += 1
        if expect("Not found results have correct IDs",
                  [r.get("id") for r in results.get("not_found", [])] == [99999, 99998],
                  f"not_found_ids={[r.get('id') for r in results.get('not_found',[])]}"): passed += 1
        else: failed += 1

        print("\n--- Test 34: Batch Disposition - Conflict Detection (Partial Success) ---")
        s, d = man4.get(f"/api/vouchers/{vid_disp}")
        current_alerts = d.get("alerts", [])
        stale_items = []
        fresh_items = []
        for a in current_alerts:
            stale_items.append({"id": a["id"], "disposition_version": 0})
            fresh_items.append({"id": a["id"], "disposition_version": a["disposition_version"]})

        one_stale = [stale_items[0]]
        if len(fresh_items) > 1:
            one_stale.append(fresh_items[1])

        s, d = man4.post("/api/alert-logs/batch-disposition", {
            "disposition_status": "follow_up",
            "disposition_note": "批量冲突测试",
            "items": one_stale
        })
        if expect("Batch with stale version: conflict >= 1", s == 200,
                  f"status={s}"): passed += 1
        else: failed += 1
        summary2 = d.get("summary", {})
        results2 = d.get("results", {})
        if expect("Stale item detected as conflict", summary2.get("conflict", 0) >= 1,
                  f"summary2={summary2}"): passed += 1
        else: failed += 1
        if expect("Conflict result includes current state",
                  len(results2.get("conflict", [])) > 0 and results2["conflict"][0].get("current") is not None,
                  f"conflict0={results2.get('conflict',[{}])[0]}"): passed += 1
        else: failed += 1
        if expect("Conflict result includes voucher_no and rule_name",
                  len(results2.get("conflict", [])) > 0 and
                  results2["conflict"][0].get("voucher_no") and
                  results2["conflict"][0].get("rule_name"),
                  f"conflict0_keys={list(results2.get('conflict',[{}])[0].keys())}"): passed += 1
        else: failed += 1

        print("\n--- Test 35: Batch Disposition - State Sync in Voucher Detail, Alert List, Filters ---")
        s, d = cash2.post("/api/vouchers", {
            "voucher_no":"T-BATCH-SYNC",
            "shift_code":"早班",
            "shift_date":"2026-06-12",
            "cashier":"测试员",
            "diff_amount": 600.00,
            "reason":"系统差异",
            "remark":"批量处置状态同步测试",
        })
        vid_sync = d.get("id")
        s, d = man4.get(f"/api/vouchers/{vid_sync}")
        sync_alerts = d.get("alerts", [])
        sync_items = [{"id": a["id"], "disposition_version": a["disposition_version"]} for a in sync_alerts]
        if expect("Sync test voucher has alerts", len(sync_items) > 0,
                  f"alerts={len(sync_items)}"): passed += 1
        else: failed += 1

        s, d = man4.post("/api/alert-logs/batch-disposition", {
            "disposition_status": "ignored",
            "disposition_note": "批量处置：确认误报，已忽略",
            "items": sync_items
        })
        if assert_eq("Batch disposition for sync test all success",
                     d.get("summary", {}).get("success", 0), len(sync_items)): passed += 1
        else: failed += 1

        s, d = man4.get(f"/api/vouchers/{vid_sync}")
        detail_alerts = d.get("alerts", [])
        if expect("Voucher detail shows updated disposition=ignored",
                  len(detail_alerts) > 0 and all(a["disposition_status"] == "ignored" for a in detail_alerts),
                  f"detail_statuses={[a['disposition_status'] for a in detail_alerts]}"): passed += 1
        else: failed += 1
        if expect("Voucher detail shows disposition_note",
                  all("确认误报" in (a.get("disposition_note") or "") for a in detail_alerts),
                  f"notes={[a.get('disposition_note') for a in detail_alerts]}"): passed += 1
        else: failed += 1

        s, d = man4.get("/api/alert-logs?disposition_status=ignored")
        ignored_logs = [l for l in d.get("logs", []) if l["voucher_no"] == "T-BATCH-SYNC"]
        if expect("Alert list filter by ignored returns batch-processed alerts",
                  len(ignored_logs) == len(sync_items),
                  f"ignored_count={len(ignored_logs)} expected={len(sync_items)}"): passed += 1
        else: failed += 1

        s, d = man4.get("/api/vouchers?alert_disposition=ignored")
        v_ignored = [v for v in d.get("vouchers", []) if v["voucher_no"] == "T-BATCH-SYNC"]
        if expect("Voucher filter by alert_disposition=ignored includes batch-processed voucher",
                  len(v_ignored) == 1, f"count={len(v_ignored)}"): passed += 1
        else: failed += 1

        s, d = man4.get("/api/vouchers?alert_disposition=unprocessed")
        v_unproc = [v for v in d.get("vouchers", []) if v["voucher_no"] == "T-BATCH-SYNC"]
        if expect("Voucher filter by unprocessed excludes batch-processed voucher",
                  len(v_unproc) == 0, f"count={len(v_unproc)}"): passed += 1
        else: failed += 1

        print("\n--- Test 36: Batch Disposition - CSV Export Shows Updated Status ---")
        url3 = BASE_URL + "/api/vouchers/export.csv"
        req3 = urllib.request.Request(url3)
        cookie3 = man4._cookie_header()
        if cookie3:
            req3.add_header("Cookie", cookie3)
        with urllib.request.urlopen(req3) as resp3:
            csv3 = resp3.read().decode("utf-8")
        lines3 = csv3.splitlines()
        sync_rows = [ln for ln in lines3 if "T-BATCH-SYNC" in ln]
        if expect("CSV includes T-BATCH-SYNC rows", len(sync_rows) > 0,
                  f"rows={len(sync_rows)}"): passed += 1
        else: failed += 1
        has_ignored = any("已忽略" in ln for ln in sync_rows)
        if expect("CSV shows '已忽略' disposition for batch-processed alerts", has_ignored,
                  f"has_ignored={has_ignored} rows={sync_rows[:2]}"): passed += 1
        else: failed += 1
        has_note = any("确认误报" in ln for ln in sync_rows)
        if expect("CSV shows disposition note from batch", has_note,
                  f"has_note={has_note}"): passed += 1
        else: failed += 1
        has_handler = any("manager" in ln for ln in sync_rows)
        if expect("CSV shows disposition handler from batch", has_handler,
                  f"has_handler={has_handler}"): passed += 1
        else: failed += 1

        print("\n--- Test 37: Batch Disposition - Persistence Across Restart ---")
        s, d = man4.get(f"/api/vouchers/{vid_sync}")
        alerts_before_restart = d.get("alerts", [])

        server.terminate()
        try: server.wait(timeout=5)
        except Exception:
            try: server.kill()
            except: pass
        time.sleep(2)

        con2 = sqlite3.connect(str(DB_PATH))
        con2.row_factory = sqlite3.Row
        db_rows = con2.execute(
            "SELECT * FROM alert_logs WHERE voucher_no = ? ORDER BY id",
            ("T-BATCH-SYNC",)
        ).fetchall()
        if expect("Batch disposition persisted in DB (ignored)",
                  len(db_rows) > 0 and all(r["disposition_status"] == "ignored" for r in db_rows),
                  f"db_statuses={[r['disposition_status'] for r in db_rows]}"): passed += 1
        else: failed += 1
        if expect("Batch disposition note persisted in DB",
                  all("确认误报" in (r["disposition_note"] or "") for r in db_rows),
                  f"db_notes={[r['disposition_note'] for r in db_rows]}"): passed += 1
        else: failed += 1
        if expect("Batch disposition handler persisted in DB",
                  all(r["disposition_handler"] == "manager" for r in db_rows),
                  f"db_handlers={[r['disposition_handler'] for r in db_rows]}"): passed += 1
        else: failed += 1
        con2.close()

        server = start_server()
        time.sleep(0.5)
        man5 = Session()
        man5.post("/api/login", {"username":"manager","password":"manager123"})

        s, d = man5.get(f"/api/vouchers/{vid_sync}")
        alerts_after_restart = d.get("alerts", [])
        if expect("After restart: batch disposition status still ignored",
                  len(alerts_after_restart) > 0 and
                  all(a["disposition_status"] == "ignored" for a in alerts_after_restart),
                  f"after_restart_statuses={[a['disposition_status'] for a in alerts_after_restart]}"): passed += 1
        else: failed += 1
        if expect("After restart: disposition handler still manager",
                  all(a.get("disposition_handler") == "manager" for a in alerts_after_restart),
                  f"after_restart_handlers={[a.get('disposition_handler') for a in alerts_after_restart]}"): passed += 1
        else: failed += 1

        s, d = man5.get("/api/operation-logs")
        op_logs = d.get("logs", [])
        has_batch_op = any("批量更新预警处置" in l.get("action", "") for l in op_logs)
        if expect("Batch disposition logged in operation log", has_batch_op,
                  f"op_actions={[l.get('action') for l in op_logs[:10]]}"): passed += 1
        else: failed += 1

        print("\n--- Test 38: Batch Disposition - Single Disposition Still Works (No Regression) ---")
        s, d = cash2.post("/api/vouchers", {
            "voucher_no":"T-SINGLE-REG",
            "shift_code":"中班",
            "shift_date":"2026-06-12",
            "cashier":"测试员",
            "diff_amount": 700.00,
            "reason":"系统差异",
            "remark":"单条处置回归测试",
        })
        vid_single_reg = d.get("id")
        s, d = man5.get(f"/api/vouchers/{vid_single_reg}")
        single_alert = d.get("alerts", [])[0]
        s, d = man5.post(f"/api/alert-logs/{single_alert['id']}/disposition", {
            "disposition_status": "follow_up",
            "disposition_note": "单条处置：跟进调查",
            "disposition_version": single_alert["disposition_version"]
        })
        if expect("Single disposition still works after batch feature added", s == 200 and d.get("ok"),
                  f"status={s} ok={d.get('ok')}"): passed += 1
        else: failed += 1
        if expect("Single disposition updates handler correctly",
                  d.get("alert", {}).get("disposition_handler") == "manager",
                  f"handler={d.get('alert',{}).get('disposition_handler')}"): passed += 1
        else: failed += 1
        if expect("Single disposition version incremented",
                  d.get("alert", {}).get("disposition_version") == single_alert["disposition_version"] + 1,
                  f"version={d.get('alert',{}).get('disposition_version')} expected={single_alert['disposition_version']+1}"): passed += 1
        else: failed += 1

        s, d = man5.post(f"/api/alert-logs/{single_alert['id']}/disposition", {
            "disposition_status": "ignored",
            "disposition_note": "旧版本冲突测试",
            "disposition_version": single_alert["disposition_version"]
        })
        if assert_eq("Single disposition conflict detection still works (409)", s, 409): passed += 1
        else: failed += 1

        print("\n--- Test 39: Batch Disposition - Import/Export/Revoke Still Work (No Regression) ---")
        import_csv = (
            "单据编号,状态,班次,班次日期,收银员,差异金额,原因,备注,创建人\n"
            "T-BATCH-IMP-01,待复核,晚班,2026-06-12,测试员,800,系统差异,批量处置导入测试,manager\n"
        ).encode("utf-8-sig")
        s, d = man5.post("/api/vouchers/import", form_data={
            "file": ("batch_import.csv", import_csv, "text/csv")
        })
        if expect("CSV import still works after batch feature", s == 200 and d.get("success", 0) >= 1,
                  f"status={s} success={d.get('success')}"): passed += 1
        else: failed += 1

        s, d = man5.get("/api/vouchers?voucher_no=T-BATCH-IMP-01")
        imported_v = d.get("vouchers", [{}])[0]
        imp_id = imported_v.get("id")
        if expect("Imported voucher exists and has alerts", imp_id is not None,
                  f"imp_id={imp_id}"): passed += 1
        else: failed += 1

        url4 = BASE_URL + "/api/vouchers/export.csv"
        req4 = urllib.request.Request(url4)
        cookie4 = man5._cookie_header()
        if cookie4:
            req4.add_header("Cookie", cookie4)
        with urllib.request.urlopen(req4) as resp4:
            csv4 = resp4.read().decode("utf-8")
        if expect("CSV export still works after batch feature",
                  "T-BATCH-IMP-01" in csv4,
                  f"has_imported={'T-BATCH-IMP-01' in csv4}"): passed += 1
        else: failed += 1

        s, d = man5.post(f"/api/vouchers/{imp_id}/revoke", {"reason":"批量功能下撤销测试"})
        if expect("Revoke still works after batch feature", s == 200 and d.get("new_voucher_no"),
                  f"status={s} new_no={d.get('new_voucher_no')}"): passed += 1
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
