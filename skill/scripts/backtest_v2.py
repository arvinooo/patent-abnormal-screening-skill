#!/usr/bin/env python3
"""v2 回测：拿历史批次（v1 已判的 case JSON）用 v2 口径重判，出新旧对照表。

用法:
    python3 backtest_v2.py --batch-a <常州cases目录> --batch-b <天津cases目录> -o <输出目录>
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

import v2_judge as J

# 常州批 case JSON 里没写全部发明人，这里补（来源：AI预警测试案件明细表-常州.xls）
# 本案 IPC（回测期人工给定：文本无著录项，按技术对象就近归类）
CASE_IPC = {
    "一种云视频会议终端的可升降安装机架": "F16M11/00",
    "风电土建测量用测量装置": "G01B5/00",
    "风电场电气预埋施工用管线弯头预制设备": "B21D7/16",
    "用于风电场的防尘开槽器": "B26D3/06",
    "风电场电气工程穿线施工装置": "H02G1/08",
    "用于风电场道路的周边防护网": "E04H17/14",
    "一种污泥脱水一体化装置": "C02F11/12",
    "一种组合式布袋除尘器": "B01D46/00",
    "一种脉冲布袋除尘器": "B01D46/00",
    "一种除尘器箱体": "B01D46/00",
    "一种酸碱中和污水处理器": "C02F1/66",
    "一种静态混合器": "B01F5/00",
    "一种双向密封球阀密封结构": "F16K5/06",
    "一种复合涂层球阀密封组件": "F16K5/06",
    "一种对夹式蝶阀": "F16K1/22",
    "一种尿素生产用硬密封球阀": "F16K5/06",
    "一种尿素用球阀": "F16K5/06",
    "一种带涂层的硬密封球阀": "F16K5/06",
    "一种带电动执行器的法兰球阀": "F16K5/06",
    "一种截止阀": "F16K1/00",
    "一种截止阀保护套": "F16K27/00",
    "一种抗冲刷阀门": "F16K47/00",
    "一种抗冲刷阀门密封组件": "F16K5/06",
    "一种抗结晶硬密封阀门": "F16K5/06",
    "一种波纹管防泄漏截止阀": "F16K41/00",
}

# 常州批 case JSON 里没写全部发明人（来源：案件明细表）
INVENTORS = {
    "谢建政": ["谢建政", "朱鹏博"],
    "谢章龙": ["谢章龙", "石伟"],
}


def load(cases_dir: str) -> list[dict]:
    out = []
    for p in sorted(glob.glob(os.path.join(cases_dir, "*.json"))):
        if os.path.basename(p).startswith("_"):
            continue
        d = json.load(open(p, encoding="utf-8"))
        d["_src"] = p
        out.append(d)
    return out


def adapt(c: dict) -> dict:
    """v1 case JSON -> v2 judge 入参"""
    first = c.get("first_inventor") or ""
    invs = INVENTORS.get(first)
    if not invs:
        others = [x for x in re.split(r"[、,，\s]+", c.get("other_inventors") or "") if x]
        invs = [first] + others
    texts = c.get("dims") or {}
    return {
        "title": c.get("title"),
        "applicant": (c.get("applicants") or [""])[0],
        "first_inventor": first,
        "inventors": invs,
        "agent": c.get("agent") or "",
        "ipc": c.get("ipc") or CASE_IPC.get(c.get("title"), ""),
        "app_date": c.get("app_date") if c.get("app_date") not in (None, "", "未提供", "未提供（明细表无申请日）", "未提供（docx 无著录项）") else "",
        "main_match": texts.get("①主营匹配") or "pending",
        "type_check": texts.get("①类型") or "pending",
        "tech_attr": texts.get("①科技属性") or "pending",
        # 文本维沿用 v1（判据没变）
        "_text_dims": {k: v for k, v in texts.items()
                       if k in ("③技术领域", "④撰写", "⑥多件相似", "违法公德",
                                "①类型", "①科技属性", "①主营匹配", "⑤b一致性")},
    }


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-a", required=True, help="常州批 cases 目录")
    ap.add_argument("--batch-b", required=True, help="天津批 cases 目录")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args(argv[1:])
    os.makedirs(a.out, exist_ok=True)

    rows, cases_out = [], []
    for label, d in (("常州19件", a.batch_a), ("天津6件", a.batch_b)):
        for c in load(d):
            case = adapt(c)
            text = case.pop("_text_dims")
            r = J.judge(case, text_dims=text)
            v1 = c.get("conclusion")
            rows.append({
                "批次": label, "专利": case["title"], "申请人": case["applicant"],
                "第一发明人": case["first_inventor"],
                "v1": v1, "v2": r["conclusion"],
                "变化": "一致" if v1 == r["conclusion"] else f"{v1}→{r['conclusion']}",
                "v2命中": "、".join(r["hits"]) or "—",
                "计数": r["evidence"].get("计数", ""),
                "研发能力": r["evidence"].get("研发能力", ""),
                "跨机构": (f"{r['evidence'].get('跨机构', {}).get('申请人数', '')} 家 / "
                          f"{r['evidence'].get('跨机构', {}).get('组数', '')} 组"),
                "豁免": (r["waiver"] or {}).get("level", "—"),
            })
            rec = dict(case)
            rec.update({"conclusion": r["conclusion"], "dims": r["dims"],
                        "notes": {"v2证据": r["evidence"]},
                        "v2_hits": r["hits"], "v1_conclusion": v1})
            slug = re.sub(r"[^\w\u4e00-\u9fff]+", "_", case["title"] or "case")
            json.dump(rec, open(os.path.join(a.out, slug + ".json"), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            cases_out.append(rec)
            print(f"[{label}] {case['title'][:22]:<24} v1={v1:<4} v2={r['conclusion']:<4} "
                  f"命中={','.join(r['hits']) or '—'}", flush=True)

    # 对照表
    md = ["# v2 回测：新旧口径对照\n",
          f"- 样本：常州 19 件 + 天津 6 件 = {len(rows)} 件",
          "- 文本维（③④⑥违法）沿用 v1 结论（判据未变）；①四项同样沿用，仅重算 ⑤/1.1/2.1/②/豁免\n",
          "| 批次 | 专利 | 申请人 | 第一发明人 | v1 | v2 | 变化 | v2 命中 | 计数 | 研发能力 | 跨机构 |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append("| {批次} | {专利} | {申请人} | {第一发明人} | {v1} | {v2} | {变化} | {v2命中} | {计数} | {研发能力} | {跨机构} |"
                  .format(**r))
    md.append("")
    n = {k: sum(1 for r in rows if r["v2"] == k) for k in ("非正常", "正常", "待核查")}
    chg = sum(1 for r in rows if r["变化"] != "一致")
    md.append(f"## 汇总\n\n- v2 结论：非正常 {n['非正常']} / 正常 {n['正常']} / 待核查 {n['待核查']}")
    md.append(f"- 结论发生变化：{chg} 件")
    open(os.path.join(a.out, "compare.md"), "w", encoding="utf-8").write("\n".join(md))
    print("\ncompare.md ->", os.path.join(a.out, "compare.md"))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
