#!/usr/bin/env python3
"""香橼树代理师月度统计取数（bsk 浏览器，0 接口费用）。

用法:
    python3 scripts/xys_agent.py 张亮 --anchor 2025-01-16
    python3 scripts/xys_agent.py 张亮 --years 2024,2025
    python3 scripts/xys_agent.py 张亮 --anchor 2025-01-16 --no-cache

做什么:
    按「代理师姓名 + 年份」查香橼树「代理师月度统计」页，读该行每月第一列（总量），
    再按滚动 12 个月求和（窗口锚点=本案申请日，见 rules/40-agency.md）。

缓存:
    xys_month  键「姓名|年」  30 天  整年分月表（历史月份不会变，换锚点不必重查）
    agent_month 键「姓名|锚点」1 天  窗口求和结果（_schema=2）

说明:
    月度页只有整月粒度，边界月按「起月之后、止月之前（含止月）」近似，报告须标注。
    页面查不到该行 → status=not_found，调用方按待核查处理，不当 0 件。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import inco_client as INCO
import v2_fetch as F

XYS = "https://main.xysip.com/#/pt/orgData/agentYearStatistics"
XYS_HOST = "xysip.com"

# 年份字段是 el-date-editor 的「年」选择器，不是普通 select；
# 页面上第一个 .el-select 是每页条数，别用它改年份。
_QUERY = """(async()=>{
 const Y=%d, NAME=%s;
 const sleep=ms=>new Promise(r=>setTimeout(r,ms));
 const form=document.querySelector('form');
 if(!form) return JSON.stringify({error:'no_form'});
 const yin=[...form.querySelectorAll('input')].find(i=>i.getAttribute('aria-haspopup'));
 if(!yin) return JSON.stringify({error:'no_year_field'});
 if(yin.value.trim()!==String(Y)){
   yin.focus(); yin.click(); await sleep(700);
   let cells=[...document.querySelectorAll('.el-picker-panel td, .el-picker-panel a')];
   let c=cells.find(e=>e.textContent.trim()===String(Y));
   if(!c){
     const prev=[...document.querySelectorAll('.el-picker-panel button, .el-picker-panel .el-date-picker__prev-btn')][0];
     if(prev){prev.click(); await sleep(500);
       cells=[...document.querySelectorAll('.el-picker-panel td, .el-picker-panel a')];
       c=cells.find(e=>e.textContent.trim()===String(Y));}
   }
   if(!c) return JSON.stringify({error:'year_cell_missing', year_in:yin.value});
   c.click(); await sleep(500);
 }
 const ninp=form.querySelector('input');
 ninp.value=NAME;
 ninp.dispatchEvent(new Event('input',{bubbles:true}));
 ninp.dispatchEvent(new Event('change',{bubbles:true}));
 await sleep(250);
 const btn=[...document.querySelectorAll('button')].find(b=>b.innerText.trim()==='搜索');
 if(!btn) return JSON.stringify({error:'no_search_button'});
 btn.click(); await sleep(3500);
 const rows=[...document.querySelectorAll('.el-table__row')].map(r=>r.innerText.replace(/\\s+/g,' ').trim());
 const hdr=[...document.querySelectorAll('.el-table th')].map(th=>th.innerText.replace(/\\s+/g,' ').trim()).slice(1,14);
 return JSON.stringify({year_in:yin.value, name_in:ninp.value, hdr, rows});
})()"""


def _session() -> str:
    sid = INCO.session()
    return sid


def _tab(sid: str) -> str:
    """本会话窗口内指向香橼树的标签页；没有就开一个并等它加载完。"""
    win = INCO._session_window(sid)
    d = INCO._bsk("tab", "list", "--session", sid, "--json")
    tabs = (d.get("tabs") if isinstance(d, dict) else []) or []
    if win is not None:
        mine = [t for t in tabs if str(t.get("window_id")) == str(win)]
        tabs = mine or tabs
    for t in tabs:
        if XYS_HOST in (t.get("url") or ""):
            return str(t.get("tab_id"))
    d = INCO._bsk("tab", "create", "--session", sid, "--url", XYS, "--json")
    tid = str(d.get("tab_id") or "")
    if not tid:
        raise INCO.IncoError("无法打开香橼树标签页：%s" % d)
    for _ in range(10):
        time.sleep(1.5)
        if INCO._tab_url(sid, tid).startswith("http"):
            break
    return tid


def _ready(sid: str, tid: str) -> None:
    """页面首次只渲染框架，表格不出现；刷新一次再等 form。"""
    for attempt in range(3):
        code = ("(()=>{const f=document.querySelector('form');"
                "return JSON.stringify({form:!!f});})()")
        if INCO.js(code, sid, tid).get("form"):
            return
        INCO.js("(()=>{location.reload();return 1;})()", sid, tid)
        time.sleep(8)
    raise INCO.IncoError("香橼树月度页未就绪（form 未出现）")


def _parse_row(row: str, name: str) -> dict:
    """'张亮 50 0 50 …' → {months:[…], total}；列组为 总量/公海/非公海。"""
    parts = row.split()
    if not parts or parts[0] != name:
        return {}
    nums = []
    for p in parts[1:]:
        try:
            nums.append(int(p))
        except ValueError:
            nums = nums  # 忽略非数字（如“一案双申”标记）
    groups = [nums[i] for i in range(0, len(nums) - len(nums) % 3, 3)]  # 每组第一列=总量
    total = None
    if len(groups) >= 2 and groups[-1] == sum(groups[:-1]):
        total = groups.pop()
    return {"months": groups, "total": total}


def fetch_year(name: str, year: int, use_cache: bool = True) -> dict:
    key = f"{name}|{year}"
    if use_cache:
        hit = F.cache_get("xys_month", key, max_age=30)
        if hit:
            hit["_cached"] = True
            return hit
    sid = _session()
    tid = _tab(sid)
    _ready(sid, tid)
    code = _QUERY % (year, json.dumps(name, ensure_ascii=False))
    d = None
    for _ in range(3):
        d = INCO.js(code, sid, tid)
        if d.get("rows") is not None or d.get("error"):
            break
        time.sleep(2)
    if not d or d.get("error"):
        return {"agent": name, "year": year, "status": "error",
                "error": (d or {}).get("error") or "页面未返回", "months": []}
    rec = {}
    for r in d.get("rows") or []:
        rec = _parse_row(r, name)
        if rec:
            break
    if not rec:
        return {"agent": name, "year": year, "status": "not_found", "months": [],
                "note": "页面无该代理师行 → 待核查，不当 0 件"}
    out = {"agent": name, "year": year, "status": "ok",
           "months": rec["months"], "total": rec["total"],
           "month_sum": sum(rec["months"]),
           "_source": "香橼树 代理师月度统计（bsk）",
           "_fetched": dt.date.today().isoformat()}
    F.cache_set("xys_month", key, out)
    return out


def window_months(anchor: dt.date) -> list[str]:
    """滚动 12 个月，整月近似：起月之后、止月（含）。"""
    end_y, end_m = anchor.year, anchor.month
    out = []
    for i in range(11, -1, -1):
        y, m = end_y, end_m - i
        while m <= 0:
            m += 12
            y -= 1
        out.append("%04d-%02d" % (y, m))
    return out


def window_total(name: str, anchor: dt.date, use_cache: bool = True) -> dict:
    months = window_months(anchor)
    years = sorted({int(x[:4]) for x in months})
    tables = {}
    for y in years:
        r = fetch_year(name, y, use_cache)
        if r.get("status") != "ok":
            return {"agent": name, "status": r.get("status"), "error": r.get("error"),
                    "window": [months[0], months[-1]], "value": None,
                    "years": tables}
        tables[y] = r
    per = {}
    missing = []
    for ym in months:
        y, m = int(ym[:4]), int(ym[5:])
        t = tables.get(y) or {}
        arr = t.get("months") or []
        if len(arr) >= m:
            per[ym] = arr[m - 1]
        else:
            missing.append(ym)
    value = sum(per.values())
    out = {"agent": name, "status": "ok" if not missing else "partial",
           "window": [months[0], months[-1]], "value": value, "months": per,
           "missing_months": missing,
           "years": {str(y): {"total": t.get("total"), "month_sum": t.get("month_sum"),
                              "_fetched": t.get("_fetched")} for y, t in tables.items()},
           "scope": "本所口径·取总量(含公海)",
           "_source": "香橼树 代理师月度统计（bsk）",
           "_fetched": dt.date.today().isoformat()}
    F.cache_set("agent_month", f"{name}|{anchor.isoformat()}",
                dict(out, _schema=2))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="香橼树代理师月度统计取数")
    ap.add_argument("name", help="代理师姓名")
    ap.add_argument("--anchor", help="窗口锚点=本案申请日 YYYY-MM-DD（默认今天）")
    ap.add_argument("--years", help="只查指定年份，逗号分隔，如 2024,2025")
    ap.add_argument("--no-cache", action="store_true", help="不吃缓存，强制重查")
    a = ap.parse_args(argv)
    use_cache = not a.no_cache
    try:
        if a.years:
            out = {"agent": a.name, "status": "ok",
                   "years": {y: fetch_year(a.name, int(y), use_cache)
                             for y in a.years.split(",")}}
        else:
            anchor = dt.date.fromisoformat(a.anchor) if a.anchor else dt.date.today()
            out = window_total(a.name, anchor, use_cache)
    except INCO.IncoError as e:
        out = {"agent": a.name, "status": "error", "error": str(e)}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if out.get("status") in ("ok", "partial") else 1


if __name__ == "__main__":
    sys.exit(main())
