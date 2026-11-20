#!/usr/bin/env python3
"""把筛查结果转成给委托方看的报告（Markdown，三张表）。

用法:
    python3 scripts/build_report.py 结果.json [-o 报告.md] [--title 批次名]

报告结构固定：免责句 → 件数合计 → 非正常（不过）→ 正常（过）→ 待核（为什么核不了）。

写法：每案一行，原因一句话，写文档里看到的事实；不写条款号、不写内部代号、不带证据原文。
待核必须写清是哪一类：缺字段（哪个字段）、积分不足（哪个平台、哪几项没查到）、查无此人（哪个平台、查谁）。
输入字段：title / applicant / verdict / reason（有 reason 时优先用），means / effect / batch 用于兜底生成原因。
对外表述：正常 → 正常；存疑、待核 → 待核。
"""
from __future__ import annotations

import argparse
import json

DISCLAIMER = "**本报告仅供参考，不作为非正常判断的唯一依据。**"

SHORT = {
    "第九条第一项（通用动作）": "排在前的权利要求只有一串动作，没写用什么算、算到什么数",
    "第九条第二项（工艺动作）": "排在前的权利要求只有一串工艺步骤，没有一个温度、时间或尺寸",
    "第十条（功能性模块）": "权利要求通篇是功能模块，没有结构、没有流程",
    "第十一条第一项（部件罗列且从权凑数）": "部件罗列，从权只补零件拆分和材质，没有任何工程参数",
    "第十一条第二项（仅一个实施例且无工作过程）": "只有一个实施例，没有装配后的工作过程",
    "第十二条（结构堆砌）": "排在前的权利要求列了 {n} 个部件，彼此只写装配关系，没有参数、没有实施例",
    "第十四条（技术问题不落地）": "通篇是管理、评价、考核这类事项，落不到设备状态或工艺参数",
    "第十六条（缺少验证结构）": "说明书没有性能测试表，也没有测试结果的数值",
}


def short_reason(case: dict) -> str:
    # 逐案 JSON 已给出结论句式时优先用它（脚本结论、人工复核结论都在这里）
    if case.get("reason"):
        return case["reason"]
    parts = []
    for c in case.get("means", {}).get("clauses", []):
        parts.append(SHORT.get(c, c).replace("{n}", str(case.get("refs", ""))))
    for c in case.get("effect", {}).get("clauses", []):
        parts.append(SHORT.get(c, c))
    if parts:
        return "；".join(parts)
    if case.get("verdict") == "待核":
        return "缺取数信息，尚未核实（报告未填原因）"
    if case.get("verdict") == "存疑":
        return "主体核查未完成，需补充信息后复核"
    return "—"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json")
    ap.add_argument("-o", "--out")
    ap.add_argument("--title", default="非正常申请筛查报告")
    ap.add_argument("--note", default="", help="批次说明（一行，印在合计下面）")

    args = ap.parse_args()

    rows = json.load(open(args.json, encoding="utf-8"))
    bad = [r for r in rows if r["verdict"] == "非正常"]
    doubt = [r for r in rows if r["verdict"] in ("存疑", "待核")]
    ok = [r for r in rows if r["verdict"] in ("可提交", "正常")]

    L = [f"# {args.title}", "", DISCLAIMER, ""]
    L.append(f"合计 {len(rows)} 件：非正常 {len(bad)} 件，正常 {len(ok)} 件，待核 {len(doubt)} 件。")
    if args.note:
        L.append("")
        L.append(args.note)
    L.append("")

    L.append(f"## 一、非正常，不建议提交（{len(bad)} 件）")
    L.append("")
    if bad:
        L.append("| # | 名称 | 申请人 | 原因 |")
        L.append("|---|---|---|---|")
        for i, r in enumerate(bad, 1):
            L.append(f"| {i} | {r.get('title') or r['pub']} | {r.get('applicant') or '—'} | {short_reason(r)} |")
    else:
        L.append("无。")
    L.append("")

    L.append(f"## 二、正常，可以提交（{len(ok)} 件）")
    L.append("")
    if ok:
        L.append("| # | 名称 | 申请人 |")
        L.append("|---|---|---|")
        for i, r in enumerate(ok, 1):
            L.append(f"| {i} | {r.get('title') or r['pub']} | {r.get('applicant') or '—'} |")
    else:
        L.append("无。")
    L.append("")

    L.append(f"## 三、待核，需补充信息（{len(doubt)} 件）")
    L.append("")
    if doubt:
        L.append("| # | 名称 | 申请人 | 待核原因 |")
        L.append("|---|---|---|---|")
        for i, r in enumerate(doubt, 1):
            L.append(f"| {i} | {r.get('title') or r['pub']} | {r.get('applicant') or '—'} | {short_reason(r)} |")
    else:
        L.append("无。")

    text = "\n".join(L)
    if args.out:
        open(args.out, "w", encoding="utf-8").write(text)
        print(f"已写入 {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
