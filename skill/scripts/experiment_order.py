#!/usr/bin/env python3
"""排序实验：给一批带标签的公开文本，量化每个维度的命中分布与去重成本。

用法:
    python3 scripts/experiment_order.py --pos 目录A [目录B ...] --neg 目录C [目录D ...]

标签来自目录归属，不来自文件夹名：--pos 里的 PDF 全部按非正常计，--neg 全部按正常计。
"正常"目录若包含"非正常"目录里的同一公开号，自动按非正常计（两批重叠时以正例为准）。

输出四段：语料概况、去重键、文本条款精确率、批次覆盖。
报告解释见 references/ordering.md。
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import text_screen as TS  # noqa: E402

FRONT = {
    "app": re.compile(r"\(71\)申请人\s*([\s\S]{0,200}?)\n\s*地址"),
    "inv": re.compile(r"\(72\)发明人\s*([\s\S]{0,200}?)\n"),
    "agt": re.compile(r"专利代理师\s*([^\s\n]+)"),
    "date": re.compile(r"\(22\)申请日\s*([\d.]+)"),
}
AG = re.compile(r"\(74\)([\s\S]{0,160}?)(?:\(51\)|\(54\)|\(57\))")


def agency(t: str) -> str:
    m = AG.search(t)
    if not m:
        return ""
    s = re.sub(r"\s+", "", m.group(1))
    s = re.sub(r"专利代理师.*$", "", s)
    s = re.sub(r"\d{5}$", "", s)
    return re.sub(r"[A-Z]\d{2}[A-Z].*$", "", s)


def load(patterns: list[str]) -> list[dict]:
    rows = []
    for pat in patterns:
        for p in glob.glob(os.path.join(pat, "**", "*.pdf"), recursive=True):
            t = TS.read_text(p)
            d = {"pub": os.path.basename(p)[:-4], "len": len(t), "ag": agency(t), "path": p}
            for k, rx in FRONT.items():
                m = rx.search(t)
                d[k] = re.sub(r"\s+", "", m.group(1)) if m else ""
            d["f"] = TS.features(t)
            rows.append(d)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pos", nargs="+", required=True)
    ap.add_argument("--neg", nargs="+", default=[])
    a = ap.parse_args()

    pos = load(a.pos)
    seen = {r["pub"] for r in pos}
    neg = [r for r in load(a.neg) if r["pub"] not in seen]
    print(f"语料 {len(pos) + len(neg)} 件：正例 {len(pos)} 反例 {len(neg)}")

    invs = {x for r in pos + neg for x in (r["inv"] or "").replace("　", " ").split()
            if len(x) >= 2 and x != "请求不公布姓名"}
    print(f"去重键：申请人 {len({r['app'] for r in pos + neg if r['app']})} "
          f"发明人 {len(invs)} 代理师 {len({r['agt'] for r in pos + neg if r['agt']})} "
          f"机构 {len({r['ag'] for r in pos + neg if r['ag']})}")

    def rate(name: str, rule) -> None:
        tp = sum(1 for r in pos if rule(r["f"]))
        fp = sum(1 for r in neg if rule(r["f"]))
        print(f"{name:26s} 召回 {tp}/{len(pos)}={tp / max(1, len(pos)):.0%}  "
              f"误伤 {fp}/{len(neg)}={fp / max(1, len(neg)):.0%}  精确 {tp / max(1, tp + fp):.0%}")

    rate("手段或效果", lambda f: TS.means_judge(f)["hit"] or TS.effect_judge(f)["hit"])
    rate("仅手段", lambda f: TS.means_judge(f)["hit"])
    rate("仅效果", lambda f: TS.effect_judge(f)["hit"])

    import collections
    cl = collections.Counter()
    for r in pos + neg:
        for c in TS.means_judge(r["f"])["clauses"] + TS.effect_judge(r["f"])["clauses"]:
            cl[(c, r in neg)] += 1
    for (c, is_neg), n in sorted(cl.items(), key=lambda x: -x[1]):
        if not is_neg:
            print(f"  {c:32s} 正 {n:4d}  反 {cl[(c, True)]:3d}")

    g = collections.defaultdict(list)
    for r in pos + neg:
        if r["ag"]:
            g[r["ag"]].append(r)
    def bg(r: dict) -> str:
        _, _, d = TS.split_sections(TS.read_text(r["path"]))
        m = re.search(r"背景技术", d)
        if not m:
            return ""
        tail = d[m.end():]
        e = re.search(r"发明内容|发明概述|技术方案|要解决的技术问题|附图说明", tail)
        return tail[:e.start()] if e else tail[:2000]

    def text_conds(v: list[dict]) -> tuple[float, float]:
        bgs = [bg(r) for r in v]
        bgs = [x for x in bgs if x]
        if not bgs:
            return 1.0, 1.0
        cite = sum(1 for x in bgs if re.search(r"CN\s?\d{6,}|公开号|专利号", x)) / len(bgs)
        long = sum(1 for x in bgs if len(re.sub(r"\s|\[\d+\]", "", x)) >= 400) / len(bgs)
        return cite, long

    hit = set()
    for k, v in g.items():
        if len(v) < 30 or len({x["app"] for x in v if x["app"]}) < 15:
            continue
        cite, long = text_conds(v)
        flag = "命中" if cite < 0.20 and long < 0.50 else "不符"
        print(f"  机构 {k[:22]:24s} {len(v):3d} 件 {len({x['app'] for x in v if x['app']}):3d} 家 "
              f"引文 {cite:.0%} 长背景 {long:.0%} → {flag}")
        if flag == "命中":
            hit.add(k)
    print(f"批次命中机构 {len(hit)} 家")
    bat = [r for r in pos + neg if r["ag"] in hit]
    print(f"  覆盖正例 {sum(1 for r in bat if r in pos)}/{len(pos)}  反例 {sum(1 for r in bat if r in neg)}/{len(neg)}")

    tail = [r for r in pos + neg if r["ag"] not in hit]
    sus = [r for r in tail if TS.means_judge(r["f"])["hit"] or TS.effect_judge(r["f"])["hit"]]
    tp = sum(1 for r in sus if r in pos)
    print(f"尾部 {len(tail)} 件（正例 {sum(1 for r in tail if r in pos)}、"
          f"申请人 {len({r['app'] for r in tail if r['app']})}）："
          f"文本可疑 {len(sus)}（正例 {tp}，精确 {tp / max(1, len(sus)):.0%}），"
          f"可疑申请人 {len({r['app'] for r in sus if r['app']})}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
