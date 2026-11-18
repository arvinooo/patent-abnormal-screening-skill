#!/usr/bin/env python3
"""发明人取数（incoPat，浏览器，无接口费用）：窗口内专利 + 申请人 + 共同发明人。

用法:
    python3 scripts/inco_inventor.py "金锐博" [--anchor 2026-11-10] [--co "张三,李四"]
    python3 scripts/inco_inventor.py --names "金锐博,王键,张钦崇" --out cache/inventor_batch.json

做什么（两件事）:

    1) 窗口内件数（1.2）
       expr = IN=(姓名) AND AD=[窗口起 TO 窗口止]
       结果行按申请号去重计件；页脚每页调 100，必要时翻页。
    2) 共同发明人（剔重名用）
       对 --co 给出的本案其余发明人各跑一次同窗口 IN=(...)，
       与候选记录按申请号求交集，标 co_inventors。

窗口：默认锚点=今天，往前 365 天；有本案申请日时传 --anchor。

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


def fetch_one(name: str, anchor: str | None, co: list[str]) -> dict:
    start, end = _window(anchor)
    rec = INCO.inventor_patents(name, start, end)
    records = rec.get("records") or []
    # 剔重名：对其余发明人各跑一次同窗口检索，按申请号求交集标共同发明人
    others = [x for x in co if x and x != name]
    for other in others:
        r2 = INCO.inventor_patents(other, start, end)
        hit = {x.get("app_no") or x.get("pub") for x in (r2.get("records") or [])}
        hit.discard("")
        for r in records:
            k = r.get("app_no") or r.get("pub")
            if k and k in hit:
                r.setdefault("co_inventors", [])
                if other not in r["co_inventors"]:
                    r["co_inventors"].append(other)
    return {
        "inventor": name,
        "window": [start, end],
        "anchor": anchor or dt.date.today().isoformat(),
        "expr": rec.get("expr"),
        "total_raw": rec.get("total_raw"),
        "stat": rec.get("stat") or {},
        "count": len(records),
        "records": records,
        "common_name": name in COMMON_NAMES,
        "errors": rec.get("errors") or [],
        "_source": "incoPat 结果行（按申请号去重）",
    }


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?", help="单个发明人姓名")
    ap.add_argument("--names", help="逗号分隔的多个发明人")
    ap.add_argument("--anchor", help="窗口锚点（本案申请日，YYYY-MM-DD）；缺省用今天")
    ap.add_argument("--co", default="", help="本案其余发明人，逗号分隔（用于共同发明人反查）")
    ap.add_argument("--out", help="写出的 JSON 文件")
    a = ap.parse_args(argv[1:])

    names = [x.strip() for x in (a.names or a.name or "").split(",") if x.strip()]
    if not names:
        print("需要发明人姓名：位置参数或 --names", file=sys.stderr)
        return 2
    co = [x.strip() for x in a.co.split(",") if x.strip()]

    results = []
    for n in names:
        print(f"[取数] {n} …", file=sys.stderr)
        results.append(fetch_one(n, a.anchor, co))

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
