#!/usr/bin/env python3
"""排序实验：给一批带标签的公开文本，量化每个维度的命中分布与去重成本。

用法:
    python3 scripts/experiment_order.py --pos 目录A [目录B ...] --neg 目录C [目录D ...]

标签来自目录归属，不来自文件夹名：--pos 里的 PDF 全部按非正常计，--neg 全部按正常计。
"正常"目录若包含"非正常"目录里的同一公开号，自动按非正常计（两批重叠时以正例为准）。

输出四段：语料概况、去重键、文本条款精确率、调参扫描。
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


def eff_raw(f: dict) -> bool:
    """第十六条的原始成立条件（不含护栏），扫护栏时用；判案请用 text_screen.effect_judge。"""
    if f["util"]:
        return False
    if f["c1_struct"] >= 3 and (f["c1_param"] >= 3 or f["dep_param"] >= 3):
        return False
    return f["perf_table"] == 0 and f["test_res"] == 0


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

    rate("手段或效果（原始条件）", lambda f: TS.means_judge(f)["hit"] or eff_raw(f))
    rate("仅手段", lambda f: TS.means_judge(f)["hit"])
    rate("仅效果（原始条件）", eff_raw)

    import collections
    cl = collections.Counter()
    for r in pos + neg:
        for c in TS.means_judge(r["f"])["clauses"] + (["第十六条（缺少验证结构）"] if eff_raw(r["f"]) else []):
            cl[(c, r in neg)] += 1
    for (c, is_neg), n in sorted(cl.items(), key=lambda x: -x[1]):
        if not is_neg:
            print(f"  {c:32s} 正 {n:4d}  反 {cl[(c, True)]:3d}")

    print("\n=== 调参：效果护栏")

    def guard(f: dict, mode: str) -> bool:
        e2, p25 = f["exp"] >= 2, f["param"] >= 25
        perf = f["perf_table"] > 0 or f["test_res"] > 0
        return {
            "无": False,
            "引用": f["bg_cite"] > 0,
            "引用或量化结果": f["bg_cite"] > 0 or f["quant_result"] > 0,
            "实施例2且参数25": e2 and p25,
            "现放行": e2 and (perf or p25),
            "引用或现放行": f["bg_cite"] > 0 or (e2 and (perf or p25)),
        }[mode]

    for mode in ("无", "引用", "引用或量化结果", "实施例2且参数25", "现放行", "引用或现放行"):
        rate(f"效果护栏：{mode}",
             lambda f, m=mode: TS.means_judge(f)["hit"] or
             (eff_raw(f) and not guard(f, m)))

    print("\n=== 调参：硬判条款集（其余需与效果双证据）")
    all_means = ("第十二条", "第十条", "第九条第一项", "第十一条第一项")
    for k in (2, 3, 4):
        hard = all_means[:k]

        def judge(f: dict, H=hard) -> bool:
            cl = TS.means_judge(f)["clauses"]
            is_hard = any(any(x in c for x in H) for c in cl)
            soft = bool(cl) and not is_hard
            return is_hard or (soft and eff_raw(f) and not guard(f, "引用"))

        rate(f"硬判：{'/'.join(hard)}", judge)

    print("\n=== 三档结论（text_screen.verdict_of）")
    def verdict(r: dict) -> str:
        v, _ = TS.verdict_of({"means": TS.means_judge(r["f"]),
                              "effect": TS.effect_judge(r["f"])})
        return v

    for scope, rows in (("全语料", pos + neg),):
        g = collections.Counter((verdict(r), "正" if r in pos else "反") for r in rows)
        line = "  ".join(f"{v} {g[(v, '正')]}正/{g[(v, '反')]}反" for v in ("非正常", "存疑", "正常"))
        tp, fp = g[("非正常", "正")], g[("非正常", "反")]
        print(f"{scope} {len(rows):3d} 件：{line}  精确 {tp / max(1, tp + fp):.0%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
