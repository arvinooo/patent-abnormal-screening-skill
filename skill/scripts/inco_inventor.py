#!/usr/bin/env python3
"""发明人取数（incoPat，浏览器，无接口费用）：窗口内专利 + 申请人 + 共同发明人。

用法:
    python3 scripts/inco_inventor.py "金锐博" [--anchor 2026-11-10] [--co "张三,李四"]
    python3 scripts/inco_inventor.py --names "金锐博,王键,张钦崇" --out cache/inventor_batch.json

做什么（两件事）:

    1) 窗口内件数（1.2）
       expr = IN=(姓名) AND AD=[窗口起 TO 窗口止]
       结果行按申请号去重计件；页脚每页调 100，必要时翻页。
    2) 剔重名（stage2-subject/S1-inventor.md §2）
       窗口内同名记录 ≤300：拉全量，对 --co 各跑一次同窗口检索，按申请号求交集标 co_inventors。
       窗口内同名记录 >300（常见名溢出）：不拉全量，改用交集式
         IN=(第一发明人) AND IN=(其余发明人) AND AD=[窗口] 直接计数。
       本案只有一个发明人时传 --applicant，改用 AP=(申请人) AND IN=(发明人) 锚定。

窗口：默认锚点=今天，往前 365 天；有本案申请日时传 --anchor（有申请号先从 incoPat 取回申请日）。

输出（stdout，JSON）:
    {"inventor":"焦劼","window":[start,end],"total_raw":16,
     "records":[{"pub":"CN122744414A","app_no":"CN202611139689.5",
                 "app_date":"20260730","title":"…","applicants":["…"],
                 "ipc":"A23F3/34","type":"发明申请","co_inventors":[]}],
     "stat":{"发明申请":15,"发明授权":1}}

依赖: bsk CLI（daemon + 浏览器已登录 incoPat）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import inco_client as INCO

COMMON_NAMES = set("""张伟 王伟 王芳 李娜 刘洋 李伟 王静 张敏 李静 王秀英 张丽 李秀英
刘伟 王强 张杰 李强 王勇 张军 李军 王杰 张勇 李勇 刘杰 王涛""".split())


def _window(anchor: str | None) -> tuple[str, str]:
    """滚动 12 个月窗口：锚点往前 365 天（含锚点当天）。"""
    a = anchor or dt.date.today().isoformat()
    try:
        end = dt.date.fromisoformat(a)
    except ValueError:
        end = dt.date.today()
    start = end - dt.timedelta(days=365)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def _dedup(rows: list[dict]) -> list[dict]:
    """按申请号去重（一案双申、A/B 文本算 1 件）。"""
    seen, out = set(), []
    for x in rows:
        k = x.get("app_no") or x.get("pub")
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out


def _collect(expr: str) -> dict:
    """跑一次检索并按申请号去重取全记录。"""
    r = INCO.search(expr, all_pages=True)
    return {"expr": expr, "total_raw": r.get("count"), "stat": r.get("stat") or {},
            "records": _dedup(r.get("rows") or []), "error": r.get("error")}


def fetch_one(name: str, anchor: str | None, co: list[str],
              applicant: str = "", cap: int = 300, no_co: bool = False) -> dict:
    start, end = _window(anchor)
    win = f"AD=[{start} TO {end}]"
    others = [x for x in co if x and x != name]
    # 一次检索同时拿条数：原先为取 count 先跑一遍同表达式，等于每次多花一次浏览器检索
    base = _collect(f"IN=({name}) AND {win}")
    total = base.get("total_raw")
    if total is None or base.get("error"):
        return {"inventor": name, "window": [start, end], "count": None,
                "common_name": name in COMMON_NAMES,
                "error": base.get("error") or "检索未返回条数"}

    if total > cap:
        # 常见名溢出：no_co 且给了申请人 → 先用申请人锚定，一条检索解决
        if no_co and applicant:
            c = _collect(f"AP=({applicant}) AND IN=({name}) AND {win}")
            return {"inventor": name, "window": [start, end],
                    "anchor": anchor or dt.date.today().isoformat(),
                    "total_raw": total, "count": len(c["records"]), "records": c["records"],
                    "mode": "applicant", "applicant": applicant,
                    "note": "常见名溢出，按申请人锚定计数"}
        # 其次用交集缩小到可确认同一人的集合
        if others:
            union: dict = {}
            for other in others:
                c = _collect(f"IN=({name}) AND IN=({other}) AND {win}")
                for rec in c["records"]:
                    union.setdefault(rec.get("app_no") or rec.get("pub"), rec)
            recs = list(union.values())
            return {"inventor": name, "window": [start, end],
                    "anchor": anchor or dt.date.today().isoformat(),
                    "total_raw": total, "count": len(recs), "records": recs,
                    "mode": "intersect", "co": others,
                    "common_name": name in COMMON_NAMES,
                    "note": ("按共同发明人交集剔重名：同一人的记录=与%s共同署名的记录；"
                             "交集为 0 时件数按 0 记" % "/".join(others))}
        if applicant:
            c = _collect(f"AP=({applicant}) AND IN=({name}) AND {win}")
            recs = c["records"]
            return {"inventor": name, "window": [start, end],
                    "anchor": anchor or dt.date.today().isoformat(),
                    "total_raw": total, "count": len(recs), "records": recs,
                    "mode": "applicant", "applicant": applicant,
                    "note": "单发明人无法求交集，按申请人锚定计数（cross-org 亦限该集合）"}
        return {"inventor": name, "window": [start, end], "count": None,
                "total_raw": total, "common_name": True if name in COMMON_NAMES else None,
                "note": "常见名溢出且无共同发明人/申请人可锚定，保持存疑"}

    rec = base
    records = rec["records"]
    # 未溢出：仍按共同发明人交集标 co_inventors，便于逐条核对
    for other in ([] if no_co else others):
        r2 = _collect(f"IN=({name}) AND IN=({other}) AND {win}")
        hit = {x.get("app_no") or x.get("pub") for x in r2["records"]}
        for r in records:
            k = r.get("app_no") or r.get("pub")
            if k and k in hit:
                r.setdefault("co_inventors", [])
                if other not in r["co_inventors"]:
                    r["co_inventors"].append(other)
    if no_co and others:
        for r in records:
            r.setdefault("co_inventors", [])
    return {
        "inventor": name, "window": [start, end],
        "anchor": anchor or dt.date.today().isoformat(),
        "expr": rec["expr"], "total_raw": total, "stat": rec["stat"],
        "count": len(records), "records": records, "mode": "full",
        "common_name": name in COMMON_NAMES, "errors": [],
        "_source": "incoPat 结果行（按申请号去重）",
    }


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?", help="单个发明人姓名")
    ap.add_argument("--names", help="逗号分隔的多个发明人")
    ap.add_argument("--anchor", help="窗口锚点（本案申请日，YYYY-MM-DD）；缺省用今天")
    ap.add_argument("--co", default="", help="本案其余发明人，逗号分隔（用于交集剔重名）")
    ap.add_argument("--applicant", default="", help="本案申请人（单发明人、常见名溢出时用它锚定）")
    ap.add_argument("--out", help="写出的 JSON 文件")
    ap.add_argument("--no-co", action="store_true",
                    help="跳过逐共同发明人交集检索（每条省一次浏览器检索），剔重名退化为 IPC 兜底")
    ap.add_argument("--facts", action="store_true",
                    help="把件数写进缓存 facts（键 T20|姓名|窗口），不落原始记录")
    a = ap.parse_args(argv[1:])

    names = [x.strip() for x in (a.names or a.name or "").split(",") if x.strip()]
    if not names:
        print("需要发明人姓名：位置参数或 --names", file=sys.stderr)
        return 2
    co = [x.strip() for x in a.co.split(",") if x.strip()]

    results = []
    for n in names:
        print(f"[取数] {n} …", file=sys.stderr)
        results.append(fetch_one(n, a.anchor, co, applicant=a.applicant, no_co=a.no_co))

    if a.facts:
        import cache
        for r in results:
            w = r.get("window") or []
            n = r.get("count")
            if n is None or len(w) != 2:
                continue
            cache._save("facts", dict(cache._load("facts"), **{
                f"T20|{r['inventor']}|{w[0].replace('-', '')}-{w[1].replace('-', '')}": {
                    "_schema": cache.SCHEMA.get("facts", 1), "n": n,
                    "org_groups": None, "_fetched": dt.date.today().isoformat()}}))

    out = {"anchor": a.anchor or dt.date.today().isoformat(),
           "co_inventors": co, "results": results,
           "fetched_at": dt.datetime.now().isoformat(timespec="seconds")}
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"-> {a.out}", file=sys.stderr)
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
