#!/usr/bin/env python3
"""第二层发明人件数（T20/T21 组数）批量跑：一个会话、团队锚定、只写数字。

用法:
    python3 scripts/subject_inventor.py --cases 合并结果.json -o subject.json
    python3 scripts/subject_inventor.py --cases 合并结果.json -o subject.json --limit 5

输入 cases：text_screen --json 经 load_list --merge 的行，至少含
    file / verdict / applicant / inventors / app_date / ipc / agency / agent
只处理 verdict=存疑 的行。

三条提速：
  1. 同一进程里逐个发明人取数，复用同一个 incoPat 会话（省掉每次重开窗口的 10s）。
  2. 同申请人 ≥3 件且第一发明人相同 → 团队锚定：只按「申请人 + 首发明人」查一次，
     用同一窗口，避免逐共同发明人交集造成的多次检索与超时。
  3. 结果写 facts（键 T20|姓名|窗口），下次同窗口命中就不再取数。

剔除同名他人：有本案 IPC 时按 IPC 前 4 位保留；无 IPC 时按共同发明人重合保留；
两者都没有才按原始条数计（报告注明）。
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cache
import inco_inventor as INV

TEAM_MIN = 3


def _anchor(case: dict) -> str:
    d = (case.get("app_date") or "").replace(".", "-").replace("/", "-")[:10]
    if len(d) == 10 and d[4] == "-":
        return d
    return dt.date.today().isoformat()


def _dedupe(records: list[dict], case_ipcs: list[str], co: list[str]) -> tuple[list[dict], str]:
    if case_ipcs:
        pre = {i[:4] for i in case_ipcs if i}
        kept = [r for r in records if (r.get("ipc") or "")[:4] in pre]
        return kept, "ipc"
    team = set(co)
    kept = [r for r in records if set(r.get("co_inventors") or []) & team]
    if kept:
        return kept, "co"
    return records, "raw"


def _facts_key(name: str, start: str, end: str) -> str:
    return f"T20|{name}|{start}-{end}"


def _write_facts(name: str, anchor: str, n: int) -> None:
    start, end = INV._window(anchor)
    store = cache._load("facts")
    key = _facts_key(name, start, end)
    store[key] = {"_schema": cache.SCHEMA.get("facts", 1), "n": n, "org_groups": None,
                  "_fetched": dt.date.today().isoformat()}
    cache._save("facts", store)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="T20 发明人件数批量取数")
    ap.add_argument("--cases", required=True, help="合并后的逐案 JSON")
    ap.add_argument("-o", "--out", required=True, help="逐案结果 JSON")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 个发明人（调试）")
    ap.add_argument("--no-cache", action="store_true", help="不吃 facts 缓存")
    a = ap.parse_args(argv)

    cases = [x for x in json.load(open(a.cases, encoding="utf-8"))
             if x.get("verdict") == "存疑"]
    by_app = collections.Counter(x.get("applicant") for x in cases if x.get("applicant"))

    groups: dict[tuple, dict] = {}
    for x in cases:
        invs = x.get("inventors") or []
        if not invs:
            continue
        name, team = invs[0], tuple(invs[1:])
        app = x.get("applicant") or ""
        anchor = _anchor(x)
        team_mode = bool(app) and by_app.get(app, 0) >= TEAM_MIN
        key = (name, "team:" + app) if team_mode else (name, anchor)
        g = groups.setdefault(key, {"name": name, "anchor": anchor, "team": team,
                                   "applicant": app if team_mode else "",
                                   "ipcs": [], "files": []})
        g["files"].append(x["file"])
        if x.get("ipc"):
            g["ipcs"].append(x["ipc"])
        if team_mode:                      # 团队模式统一用最早一案锚点
            g["anchor"] = min(g["anchor"], anchor)

    todo = list(groups.values())
    if a.limit:
        todo = todo[: a.limit]

    results = []
    for i, g in enumerate(todo, 1):
        name = g["name"]
        start, end = INV._window(g["anchor"])
        hit = None if a.no_cache else cache._load("facts").get(_facts_key(name, start, end))
        if hit and hit.get("n") is not None:
            results.append({"inventor": name, "anchor": g["anchor"], "n": hit["n"],
                            "mode": "facts", "files": g["files"], "orgs": []})
            print(f"[{i}/{len(todo)}] {name} 命中 facts：{hit['n']} 件", flush=True)
            continue
        no_co = bool(g["ipcs"])
        try:
            r = INV.fetch_one(name, g["anchor"], list(g["team"]),
                             applicant=g["applicant"], no_co=no_co)
            records = r.get("records") or []
            kept, dedupe_mode = _dedupe(records, g["ipcs"], g["team"])
            orgs = sorted({x for rec in kept for x in (rec.get("applicants") or [])})
            results.append({"inventor": name, "anchor": g["anchor"], "n": len(kept),
                            "raw": len(records), "dedupe": dedupe_mode,
                            "mode": r.get("mode"), "team": bool(g["applicant"]),
                            "orgs": orgs, "note": r.get("note"), "error": r.get("error"),
                            "files": g["files"]})
            _write_facts(name, g["anchor"], len(kept))
            print(f"[{i}/{len(todo)}] {name} {len(kept)} 件（原始 {len(records)}，{dedupe_mode}）", flush=True)
        except Exception as e:            # noqa: BLE001
            results.append({"inventor": name, "anchor": g["anchor"], "n": None,
                            "error": str(e)[:160], "files": g["files"]})
            print(f"[{i}/{len(todo)}] {name} 失败：{str(e)[:80]}", flush=True)

    try:                              # 收尾关会话，别给用户留浏览器窗口
        sid = INV.INCO.session(create=False)
        if sid:
            INV.INCO._bsk("session", "stop", sid, "--quiet")
    except Exception:                 # noqa: BLE001
        pass

    json.dump({"results": results,
               "summary": {"groups": len(groups), "done": len(results),
                           "hit": sum(1 for r in results if (r.get("n") or 0) >= 5)}},
              open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"-> {a.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
