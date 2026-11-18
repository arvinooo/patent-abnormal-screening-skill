#!/usr/bin/env python3
"""把 text_screen.py 的结果转成简洁报告（Markdown 表格，无废话）。

用法:
    python3 scripts/build_report.py 结果.json [-o 报告.md] [--title 批次名]

报告只有三块：结论计数、非正常清单、存疑清单。每案一行，原因一句话。
批次事实不重复写进每行，统一放在表下一行。
"""
from __future__ import annotations

import argparse
import json

SHORT = {
    "第八条第一项（通用动作）": "排在最前面的权利要求只有一串动作，没写用什么算、算到什么数",
    "第八条第一项（工艺动作）": "排在最前面的权利要求只有一串工艺步骤，没有一个温度、时间或尺寸",
    "第八条第三项（功能性模块）": "权利要求通篇是功能模块，没有结构、没有流程",
    "第九条第一项（部件罗列且从权凑数）": "部件罗列，后面几条只补零件拆分和材质，没有任何工程参数",
    "第九条第二项（仅一个实施例且无工作过程）": "只有一个实施例，没有装配后的工作过程",
    "第十条（结构堆砌）": "排在最前面的权利要求列了 {n} 个部件，彼此只写装配关系，没有参数、没有实施例",
    "第十二条（技术问题不落地）": "通篇是管理、评价、考核这类事项，落不到设备状态或工艺参数",
    "第十四条（无量化结果）": "说明书没有数据表、没有对比例，也没有指标数值的结论",
}


def short_reason(case: dict) -> str:
    # 逐案 JSON 已给出结论句式时优先用它（第二层结论、人工复核结论都在这里）
    if case.get("reason"):
        return case["reason"]
    parts = []
    for c in case.get("means", {}).get("clauses", []):
        parts.append(SHORT.get(c, c).replace("{n}", str(case.get("refs", ""))))
    for c in case.get("effect", {}).get("clauses", []):
        parts.append(SHORT.get(c, c))
    if parts:
        return "；".join(parts)
    if case.get("batch", {}).get("state") == "hit":
        return "仅批次一项成立，文本层面未见其他异常"
    return "—"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json")
    ap.add_argument("-o", "--out")
    ap.add_argument("--title", default="非正常筛查结论")
    args = ap.parse_args()

    rows = json.load(open(args.json, encoding="utf-8"))
    bad = [r for r in rows if r["verdict"] == "非正常"]
    doubt = [r for r in rows if r["verdict"] == "存疑"]
    ok = [r for r in rows if r["verdict"] == "可提交"]

    lines = [f"# {args.title}", ""]
    lines.append(f"合计 {len(rows)} 件：非正常 {len(bad)} 件，存疑 {len(doubt)} 件，可提交 {len(ok)} 件。")
    lines.append("")

    batch_notes = [r["batch"]["note"] for r in rows
                   if r.get("batch", {}).get("state") == "hit" and r["batch"].get("note")]

    lines.append("## 一、非正常")
    lines.append("")
    if bad:
        lines.append("| # | 名称 | 申请人 | 原因 |")
        lines.append("|---|---|---|---|")
        for i, r in enumerate(bad, 1):
            lines.append(f"| {i} | {r.get('title') or r['pub']} | {r.get('applicant') or '—'} | {short_reason(r)} |")
        if batch_notes:
            lines.append("")
            lines.append(f"批次：{batch_notes[0].split('：', 1)[-1]}。")
    else:
        lines.append("无。")
    lines.append("")

    lines.append("## 二、存疑")
    lines.append("")
    if doubt:
        lines.append("| # | 名称 | 申请人 | 差什么 |")
        lines.append("|---|---|---|---|")
        for i, r in enumerate(doubt, 1):
            lines.append(f"| {i} | {r.get('title') or r['pub']} | {r.get('applicant') or '—'} | {short_reason(r)} |")
    else:
        lines.append("无。")

    hints = [(r.get("title") or r["pub"], h) for r in bad + doubt
             for h in (r.get("hints") or [])]
    if hints:
        lines.append("")
        lines.append("复核提示：")
        seen = set()
        for title, h in hints:
            if h in seen:
                continue
            seen.add(h)
            lines.append(f"- {h}（{title}）")

    text = "\n".join(lines)
    if args.out:
        open(args.out, "w", encoding="utf-8").write(text)
        print(f"已写入 {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
