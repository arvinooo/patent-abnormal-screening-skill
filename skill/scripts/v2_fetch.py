#!/usr/bin/env python3
"""v2 取数层：MCP / 企查查 / 浏览器 + 缓存。

原则：先查缓存；没命中才花额度；派生结论不缓存（每次现算）。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import time
import urllib.request

import bsk_helper as B

CACHE = os.environ.get("PATENT_CACHE_DIR", "cache")
CACHE_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache.py")
KEY_FILE = os.environ.get("MINDFLOW_KEY_FILE",
                          "/mnt/c/Users/Arvin/Desktop/yunjia/非正常/发明人/发明人key.txt")


# ---------------------------------------------------------------- 缓存（文件式，支持新命名空间）

def _ns_path(ns: str) -> str:
    os.makedirs(CACHE, exist_ok=True)
    return os.path.join(CACHE, ns + ".json")


def _load(ns: str) -> dict:
    p = _ns_path(ns)
    if not os.path.exists(p):
        return {}
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}


def _save(ns: str, data: dict):
    p = _ns_path(ns)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, p)


def _age_days(rec: dict) -> float:
    stamp = (rec or {}).get("_fetched") or ""
    try:
        d = dt.date.fromisoformat(stamp[:10])
        return (dt.date.today() - d).days
    except Exception:
        return 1e9


def cache_get(ns: str, key: str, max_age: int = 90):
    rec = _load(ns).get(key)
    if not rec:
        return None
    return rec if _age_days(rec) <= max_age else None


def cache_set(ns: str, key: str, obj: dict):
    data = _load(ns)
    data[key] = obj
    _save(ns, data)


# ---------------------------------------------------------------- mindflow MCP

def mcp(expr: str, max_results: int = 1000) -> dict:
    key = open(KEY_FILE, encoding="utf-8").read().strip()
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "patent_expression_search",
                                  "arguments": {"expression": expr, "max_results": max_results}}},
                      ensure_ascii=False).encode()
    req = urllib.request.Request("https://mcp.mindflow.cn/api/mcp", data=body, method="POST",
                                 headers={"Authorization": "Bearer " + key,
                                          "Content-Type": "application/json",
                                          "Accept": "application/json, text/event-stream"})
    for attempt in range(6):
        try:
            raw = urllib.request.urlopen(req, timeout=300).read().decode()
            res = json.loads(re.search(r"data: (\{.*\})", raw, re.S).group(1))["result"]
            if not res.get("isError"):
                return res["structuredContent"]
            txt = (res.get("content") or [{}])[0].get("text", "")
            if "mcp_rate_limited" in txt:
                time.sleep(4 + 2 * attempt)
                continue
            return {"error": txt[:300]}
        except Exception:
            time.sleep(5)
    return {"error": "retry exhausted"}


# ---------------------------------------------------------------- 发明人记录（12 个月）

def window(anchor: str | dt.date | None = None) -> tuple[str, str]:
    """窗口 = 锚点往前 12 个月。锚点默认"="本案申请日"，没有申请日则用查询日。"""
    if isinstance(anchor, str) and anchor:
        d = dt.date.fromisoformat(anchor[:10].replace("/", "-")) if "-" in anchor[:10] else \
            dt.date(int(anchor[:4]), int(anchor[4:6]), int(anchor[6:8]))
    elif isinstance(anchor, dt.date):
        d = anchor
    else:
        d = dt.date.today()
    start = d - dt.timedelta(days=365)
    return start.strftime("%Y%m%d"), d.strftime("%Y%m%d")


def inventor_records(name: str, anchor: str | dt.date | None = None) -> dict:
    """滚动 12 个月（锚=本案申请日）、不论位次、按申请号去重；records 补 ipc。"""
    s, e = window(anchor)
    key = f"{name}|{s}|{e}"
    hit = cache_get("inventor", key, max_age=7)
    if hit:
        return hit
    expr = f"IN:({name}) AND APD:[{s} TO {e}]"
    sc = mcp(expr, max_results=1000)
    recs, total = [], sc.get("total") or 0
    for r in sc.get("records") or []:
        recs.append({
            "application_number": r.get("application_number"),
            "application_date": r.get("application_date"),
            "title": r.get("title"),
            "inventors": r.get("inventors") or [],
            "applicants": list(dict.fromkeys((r.get("current_applicants") or [])
                                             + (r.get("original_applicants") or []))),
        })
    # 去重（一案双申/多公开文本）
    seen, uniq = set(), []
    for r in recs:
        k = r["application_number"]
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)

    # IPC：浏览器结果表（0 积分），按申请号回填
    if uniq:
        try:
            rows = B.search_rows(expr).get("rows") or []
            ipc = {x["app_no"]: x["ipc"] for x in rows if x.get("app_no") and x.get("ipc")}
            for r in uniq:
                r["ipc"] = ipc.get(r["application_number"], "")
        except Exception:
            for r in uniq:
                r.setdefault("ipc", "")

    out = {"inventor": name, "expr": expr, "window_start": s, "window_end": e,
           "anchor": e, "total_raw": total, "records": uniq,
           "common_name": total > 1000,
           "_fetched": dt.date.today().isoformat(), "_source": "mcp+bsk"}
    cache_set("inventor", key, out)
    return out


# ---------------------------------------------------------------- 1.1 授权发明专利

def granted_inventions(app: str, base_date: str | None = None) -> dict:
    """到 base_date 为止，申请人是否有授权发明专利（ISD 非空）。0 积分（浏览器计数）。"""
    base = (base_date or dt.date.today().strftime("%Y%m%d")).replace("-", "")
    key = f"{app}|{base}"
    hit = cache_get("company_rd", key, max_age=90)
    if hit:
        return hit
    expr = f"ALL_AN:({app}) AND PATENT_TYPE:(发明专利) AND ISD:[* TO {base}]"
    n = None
    try:
        n = B.count_only(expr)
    except Exception:
        pass
    if n is None:
        sc = mcp(expr, max_results=1)
        n = sc.get("total")
    out = {"applicant": app, "expr": expr, "granted_invention": n,
           "base_date": base, "has": (n or 0) >= 1 if n is not None else None,
           "_fetched": dt.date.today().isoformat()}
    if n is not None:
        cache_set("company_rd", key, out)
    return out


# ---------------------------------------------------------------- 企业资质（专精特新）

def honor(app: str) -> dict:
    hit = cache_get("company_honor", app, max_age=90)
    if hit:
        return hit
    p = subprocess.run(["qcc", "operation", "get_honor_info", app, "--json"],
                       capture_output=True, text=True, timeout=180)
    raw = p.stdout or ""
    items = []
    try:
        j = json.loads(raw[raw.index("{"):])
        items = j.get("荣誉信息") or []
    except Exception:
        items = []
    z = []
    for it in items:
        nm = (it.get("名称") or "")
        if "专精特新" in nm:
            z.append({"name": nm, "level": it.get("级别") or "", "year": it.get("认证年份") or ""})
    out = {"applicant": app, "honors": items, "zhuanjingtexin": z,
           "_fetched": dt.date.today().isoformat(), "_source": "qcc.get_honor_info",
           "_ok": bool(items)}
    if items:
        cache_set("company_honor", app, out)
    return out


def waiver_from(honor_rec: dict) -> dict | None:
    """只认小巨人（国家级）+ 省级专精特新；创新型中小企业不算。"""
    for z in honor_rec.get("zhuanjingtexin") or []:
        nm, lv = z["name"], z["level"]
        if "小巨人" in nm and "国家级" in lv:
            return {"level": "国家级专精特新小巨人", "year": z["year"], "name": nm}
        if "专精特新" in nm and "省级" in lv and "小巨人" not in nm:
            return {"level": "省级专精特新中小企业", "year": z["year"], "name": nm}
    return None


# ---------------------------------------------------------------- 工商（关联判定用）

def company_reg(app: str) -> dict:
    hit = cache_get("company", app, max_age=90)
    if hit and (hit.get("legal_rep") or hit.get("address")):
        return {"applicant": app, "legal_rep": hit.get("legal_rep") or "",
                "address": hit.get("address") or "", "insured": hit.get("insured"),
                "scope": hit.get("scope") or "", "industry": hit.get("industry") or "",
                "_fetched": hit.get("_fetched", "")}
    base = hit or {}
    p = subprocess.run(["qcc", "company", "get_company_registration_info", app, "--json"],
                       capture_output=True, text=True, timeout=180)
    raw = p.stdout or ""
    try:
        j = json.loads(raw[raw.index("{"):])
    except Exception:
        j = {}
    out = {"applicant": app,
           "legal_rep": j.get("法定代表人") or base.get("legal_rep") or "",
           "address": j.get("注册地址") or base.get("address") or "",
           "insured": int(re.sub(r"\D", "", str(j.get("参保人数") or base.get("insured") or "0")) or 0),
           "scope": j.get("经营范围") or "",
           "industry": (j.get("国标行业") or {}).get("小类") or (j.get("国标行业") or {}).get("中类") or ""}
    cache_set("company", app, out)
    return out


# ---------------------------------------------------------------- 代理师滚动 12 个月

def agent_rolling(name: str, anchor: str | dt.date | None = None) -> dict:
    """代理量窗口同样锚本案申请日（往前 12 个月）。"""
    if isinstance(anchor, str) and anchor:
        today = dt.date.fromisoformat(anchor[:10]) if "-" in anchor[:10] else \
            dt.date(int(anchor[:4]), int(anchor[4:6]), int(anchor[6:8]))
    elif isinstance(anchor, dt.date):
        today = anchor
    else:
        today = dt.date.today()
    key = f"{name}|{today.isoformat()}"
    hit = cache_get("agent_month", key, max_age=1)
    if hit:
        return hit
    years = sorted({today.year, (today - dt.timedelta(days=365)).year})
    data = B.agent_months([(name, str(y)) for y in years])
    months = {}
    for y in years:
        row = data.get(f"{name}|{y}")
        if row is None:
            continue
        try:
            months[y] = B.parse_month_row(row)
        except Exception:
            months[y] = []
    if not months:
        out = {"agent": name, "status": "not_found", "value": None,
               "as_of": today.isoformat(), "_fetched": today.isoformat()}
        cache_set("agent_month", key, out)
        return out

    prev = today - dt.timedelta(days=365)
    total, detail = 0, []
    for y in years:
        m = months.get(y) or []
        for idx, v in enumerate(m, start=1):
            month_date = dt.date(y, idx, 1)
            if prev <= month_date <= today:
                total += v
                detail.append({"ym": f"{y}-{idx:02d}", "n": v})
    out = {"agent": name, "status": "ok", "value": total, "detail": detail,
           "window": [prev.isoformat(), today.isoformat()],
           "as_of": today.isoformat(), "_fetched": today.isoformat(), "_source": "香橼树月度统计"}
    cache_set("agent_month", key, out)
    return out
