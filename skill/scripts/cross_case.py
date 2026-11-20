#!/usr/bin/env python3
"""跨案核对：按申请人成组，算标题相似度与正文 20 字重合率，给出候选。

用法:
    python3 scripts/cross_case.py --cases 合并.json [--dir PDF目录] [-o 跨案.json]

判据在标准第二十七条：拆分型、复制型成立即一票否决；本脚本只做分组与计数，
结论由人读标题确认（脚本不代替人工判断产品线边界）。
输出每组：成员、申请日跨度、标题相似度、正文 20 字片段最高重合率、分类号分布、提示。
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _date(x: dict) -> dt.date:
    d = (x.get("app_date") or "").replace(".", "-")[:10]
    try:
        return dt.date.fromisoformat(d)
    except ValueError:
        return dt.date.today()


def _norm_title(t: str) -> str:
    t = re.sub(r"[^\u4e00-\u9fff]", "", t or "")
    for w in ("一种", "用于", "及其", "方法", "系统", "装置", "设备"):
        t = t.replace(w, "")
    return t


def _bigrams(t: str) -> set[str]:
    t = _norm_title(t)
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _shingles(text: str, k: int = 20) -> set[str]:
    t = re.sub(r"[^\u4e00-\u9fff]", "", text or "")
    return {t[i:i + k] for i in range(len(t) - k + 1)}


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a))


def _find_file(name: str, root: str | None) -> str | None:
    if not root:
        return None
    direct = os.path.join(root, name)
    if os.path.exists(direct):
        return direct
    for dp, _, fs in os.walk(root):
        if name in fs:
            return os.path.join(dp, name)
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="跨案核对候选")
    ap.add_argument("--cases", required=True)
    ap.add_argument("--dir", help="PDF/Word 目录（给了才算正文重合率）")
    ap.add_argument("-o", "--out")
    a = ap.parse_args(argv)

    rows = [x for x in json.load(open(a.cases, encoding="utf-8")) if x.get("applicant")]
    # 一案双申（同一申请号的发明+实用新型）只算 1 件，否则复制型会误命中
    seen, uniq = set(), []
    for x in rows:
        key = re.sub(r"\D", "", x.get("app_no") or "") or re.sub(r"\D", "", x.get("pub") or "") or x["file"]
        if key in seen:
            continue
        seen.add(key)
        uniq.append(x)
    # 一案双申：同申请人、同申请日、主题相同，一份发明一份实用新型 → 只算 1 件
    by_day: dict[tuple, list[dict]] = collections.defaultdict(list)
    for x in uniq:
        by_day[(x["applicant"], x.get("app_date") or "")].append(x)
    import text_screen as TS
    text_sh: dict[str, set[str]] = {}

    def sh_of(m: dict) -> set[str]:
        if m["file"] not in text_sh:
            p = _find_file(m["file"], a.dir)
            text_sh[m["file"]] = _shingles(TS.read_text(p)) if p else set()
        return text_sh[m["file"]]

    drop = set()
    for members in by_day.values():
        utils = [m for m in members if m.get("util")]
        invs = [m for m in members if not m.get("util")]
        for u in utils:
            for iv in invs:
                same_text = a.dir and _overlap(sh_of(u), sh_of(iv)) >= 0.8
                similar_title = _jaccard(_bigrams(u.get("title")), _bigrams(iv.get("title"))) >= 0.6
                if same_text or similar_title:
                    drop.add(u["file"])
    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for x in uniq:
        if x["file"] in drop:
            continue
        groups[x["applicant"]].append(x)

    out = []
    for app, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(members) < 2:
            continue
        dates = [_date(x) for x in members]
        span = (max(dates) - min(dates)).days
        titles = [_bigrams(x.get("title")) for x in members]
        tsim = max((_jaccard(titles[i], titles[j])
                    for i in range(len(titles)) for j in range(i + 1, len(titles))), default=0.0)
        overlap = 0.0
        if a.dir:
            sh = [sh_of(x) for x in members]
            overlap = max((_overlap(sh[i], sh[j])
                           for i in range(len(sh)) for j in range(i + 1, len(sh))), default=0.0)
        ipcs = sorted({x.get("ipc") for x in members if x.get("ipc")})
        hint = []
        if span > 366:
            hint.append("申请日跨度超过 12 个月，不构成组")
        if overlap >= 0.5:
            hint.append("正文重合率 ≥50%，复制型候选")
        if tsim >= 0.4:
            hint.append("标题高度相似，重点看是否同一产品")
        if len(ipcs) >= 4:
            hint.append(f"涉及 {len(ipcs)} 个分类号，跨类设备多，拆分型可能性低")
        out.append({
            "applicant": app, "count": len(members), "span_days": span,
            "title_similarity": round(tsim, 2), "text_overlap": round(overlap, 2),
            "ipcs": ipcs, "hints": hint,
            "members": [{"file": x["file"], "title": x.get("title") or "",
                         "app_date": x.get("app_date") or "", "verdict": x.get("verdict")} for x in members],
        })

    payload = {"groups": out, "summary": {"groups": len(out),
                                          "members": sum(g["count"] for g in out)}}
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(text)
        print(json.dumps(payload["summary"], ensure_ascii=False))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
