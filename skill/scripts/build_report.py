#!/usr/bin/env python3
"""批量筛查报告：cases/*.json -> report.md + report.pdf（含各维度命中统计）。

用法:
    python3 scripts/build_report.py <cases_dir> [-o report] [--title "标题"]

输入:
    <cases_dir>/<公开号>.json     每案一份（字段见 SKILL.md「输出」节）
    <cases_dir>/_batch.json       可选，批次元信息：title / rules_version / sources / cost

输出:
    <out>.md   总报告（Markdown，可 diff、可入库）
    <out>.pdf  同内容排版件（A4，自动横排逐案表）
只产出这两个文件，不生成 xlsx/csv。

字体: 依次尝试 PATENT_REPORT_FONT / Windows simhei.ttf / Deng.ttf / Noto CJK。
      找不到 CJK 字体时只写 md 并明确警告（不静默降级成乱码 PDF）。
依赖: fpdf2
"""
import argparse
import glob
import json
import os
import re
import sys
import time
from collections import Counter

DIMS = [
    "①类型", "①科技属性", "①社保", "①主营匹配",
    "②代理量",
    "③技术领域", "④撰写",
    "⑤a当年件数", "⑤b一致性",
    "⑥多件相似", "违法公德",
]
HEAD_SHORT = {  # 逐案表列名
    "①类型": "①类型", "①科技属性": "①科技", "①社保": "①社保", "①主营匹配": "①主营",
    "②代理量": "②代理", "③技术领域": "③领域", "④撰写": "④撰写",
    "⑤a当年件数": "⑤a", "⑤b一致性": "⑤b", "⑥多件相似": "⑥相似", "违法公德": "违法",
}
SHORT = {  # 逐案表里的单字标记
    "pass": "过", "fail": "挂", "pending": "待", "na": "—",
}
LONG = {"pass": "过", "fail": "不过", "pending": "待核查", "na": "不适用"}
CONCL = ("非正常", "正常", "待核查")

FONT_CANDIDATES = [
    os.environ.get("PATENT_REPORT_FONT", ""),
    r"C:\Windows\Fonts\simhei.ttf",
    "/mnt/c/Windows/Fonts/simhei.ttf",
    r"C:\Windows\Fonts\Deng.ttf",
    "/mnt/c/Windows/Fonts/Deng.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]


def label(c):
    """公开号优先；docx 等无著录项时退回专利名称。"""
    return c.get("pub") or c.get("title") or "—"


def load_cases(cases_dir):
    batch = {}
    bpath = os.path.join(cases_dir, "_batch.json")
    if os.path.exists(bpath):
        with open(bpath, encoding="utf-8") as f:
            batch = json.load(f)
    cases = []
    for path in sorted(glob.glob(os.path.join(cases_dir, "*.json"))):
        if os.path.basename(path).startswith("_"):
            continue
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
        rec.setdefault("dims", {})
        rec.setdefault("notes", {})
        rec.setdefault("conclusion", "待核查")
        cases.append(rec)
    return cases, batch


def compute(cases):
    stat = {d: Counter() for d in DIMS}
    for c in cases:
        for d in DIMS:
            stat[d][c["dims"].get(d, "pending")] += 1
    concl = Counter(c["conclusion"] for c in cases)
    combos = Counter()
    for c in cases:
        fails = tuple(d for d in DIMS if c["dims"].get(d) == "fail")
        if fails:
            combos[fails] += 1
    multi = Counter(len(tuple(d for d in DIMS if c["dims"].get(d) == "fail"))
                    for c in cases if c["conclusion"] == "非正常")
    return stat, concl, combos, multi


def fail_pubs(cases, dim):
    return [label(c) for c in cases if c["dims"].get(dim) == "fail"]


AGENT_DATE = "取数日未记"


def build_md(cases, batch, out_base, stat, concl, combos, multi):
    now = time.strftime("%Y-%m-%d %H:%M")
    n = len(cases)
    L = []
    add = L.append
    title = batch.get("title") or "专利非正常申请批量筛查报告"
    add("# %s\n" % title)
    add("- 生成时间：%s" % now)
    add("- 案件数：%d" % n)
    add("- 判定口径：全维（%d 项）且门；任一维不过 = 非正常；缺数据 = 待核查" % len(DIMS))
    if batch.get("rules_version"):
        add("- 阈值快照：%s" % batch["rules_version"])
    if batch.get("note"):
        add("- 批次说明：%s" % batch["note"])
    add("")

    add("## 一、结论概览\n")
    add("| 结论 | 件数 | 占比 |")
    add("|---|---:|---:|")
    for k in CONCL:
        add("| %s | %d | %.1f%% |" % (k, concl[k], 100.0 * concl[k] / n if n else 0))
    add("| **合计** | **%d** | 100%% |" % n)
    add("")

    add("## 二、各维度命中统计\n")
    add("> 一个案子可能同时挂多项，所以「不过」列的合计会大于非正常件数。")
    add("")
    add("| 维度 | 不过 | 待核查 | 不适用 | 通过 | 不过率 |")
    add("|---|---:|---:|---:|---:|---:|")
    for d in DIMS:
        s = stat[d]
        add("| %s | %d | %d | %d | %d | %.1f%% |"
            % (d, s["fail"], s["pending"], s["na"], s["pass"],
               100.0 * s["fail"] / n if n else 0))
    add("")

    agents = {}
    for c in cases:
        ag = c.get("agent") or ""
        if not ag:
            continue
        e = agents.setdefault(ag, {"pubs": [], "state": c["dims"].get("②代理量"),
                                   "note": (c.get("notes") or {}).get("②代理量", "")})
        e["pubs"].append(label(c))
    if agents:
        add("### ②代理量明细（香橼树，2026-01-01 至取数日 %s）\n" % AGENT_DATE)
        add("| 代理师 | 当年代理量 | 案子数 | 判定 | 公开号 |")
        add("|---|---:|---:|---|---|")
        for ag, e in sorted(agents.items(), key=lambda kv: -len(kv[1]["pubs"])):
            m = re.search(r"代理量\s*(\d+)\s*件", e["note"])
            val = ("%s 件" % m.group(1)) if m else "未核"
            add("| %s | %s | %d | %s | %s |" % (ag, val, len(e["pubs"]),
                                               SHORT.get(e["state"], "?"), "、".join(e["pubs"])))
        add("")

    add("## 三、命中项组合\n")
    if combos:
        add("| 命中组合 | 件数 | 公开号 |")
        add("|---|---:|---|")
        for combo, cnt in combos.most_common():
            pubs = [label(c) for c in cases
                    if tuple(d for d in DIMS if c["dims"].get(d) == "fail") == combo]
            add("| %s | %d | %s |" % ("＋".join(combo), cnt, "、".join(pubs)))
    else:
        add("无命中项。")
    add("")
    add("非正常案的命中项数分布：%s\n"
        % "，".join("%d 项 %d 件" % (k, v) for k, v in sorted(multi.items())))

    add("## 四、逐案总表\n")
    head = ["#", "公开号/名称", "申请人", "结论"] + [HEAD_SHORT[d] for d in DIMS] + ["命中项"]
    add("| " + " | ".join(head) + " |")
    add("|" + "---|" * len(head))
    for i, c in enumerate(cases, 1):
        hit = "＋".join(HEAD_SHORT[d] for d in DIMS if c["dims"].get(d) == "fail") or "—"
        cells = [str(i), label(c), (c.get("applicants") or [""])[0][:22]]
        cells.append(c["conclusion"])
        cells += [SHORT.get(c["dims"].get(d, "pending"), "?") for d in DIMS]
        cells.append(hit)
        add("| " + " | ".join(cells) + " |")
    add("")

    add("## 五、复核清单\n")
    rev = [c for c in cases if c.get("review")]
    if rev:
        for c in rev:
            add("- **%s**（%s）：%s" % (label(c), c["conclusion"], c["review"]))
    else:
        add("无。")
    add("")

    pend = [c for c in cases if c["conclusion"] == "待核查"]
    if pend:
        add("### 待核查案件缺什么\n")
        for c in pend:
            miss = [d for d in DIMS if c["dims"].get(d) == "pending"]
            add("- %s：%s" % (label(c), "、".join(miss) or "未注明"))
        add("")

    b5 = [c for c in cases if c["dims"].get("⑤b一致性") == "fail"]
    if b5:
        add("### ⑤b 降级参考（同名碰撞/母子公司分流）\n")
        add("> ⑤b 用「第一发明人（inventors[0]）名下记录」的申请人比对本案。若下表异申请人实为同名他人，"
            "该案⑤b可降级，「若无⑤b」列即为降级后结论。\n")
        add("| 公开号 | ⑤b 异申请人（样本） | 若无⑤b | 其余挂项 |")
        add("|---|---|---|---|")
        for c in b5:
            fl = c.get("⑤b_foreign") or []
            other = "＋".join(HEAD_SHORT[d] for d in (c.get("still_fail") or [])) or "—"
            add("| %s | %s | %s | %s |" % (c.get("pub", ""),
                                          "、".join(fl[:3]) + ("…" if len(fl) > 3 else "") if fl else "—",
                                          c.get("downgrade_if_5b_collision", "—"), other))
        add("")

    add("## 六、逐案证据\n")
    add("> 判据锚点：权项号、说明书段号、外部数据来源与取数日。（本报告逐维列出证据）\n")
    for c in cases:
        add("**%s**｜%s\n" % (label(c), c.get("title", "")))
        add("申请人：%s｜第一发明人：%s｜申请日：%s｜结论：**%s**\n"
            % ("、".join(c.get("applicants") or []), c.get("first_inventor", ""),
               c.get("app_date", ""), c["conclusion"]))
        verbose = bool(batch.get("verbose_evidence"))
        for d in DIMS:
            st = c["dims"].get(d)
            note = (c.get("notes") or {}).get(d, "—")
            if verbose:
                add("- %s %s：%s" % (d, LONG.get(st, "?"), note))
            elif d in ("②代理量", "⑤a当年件数") and st in ("pass", "fail", "pending"):
                add("- %s %s：%s" % (d, LONG.get(st, "?"), note))
            elif st in ("fail", "pending"):
                add("- %s %s：%s" % (d, "不过" if st == "fail" else "待核查", note))
        add("")

    if batch.get("sources"):
        add("## 七、数据来源与取数日\n")
        add("| 数据源 | 取数日 | 说明 |")
        add("|---|---|---|")
        for name, info in batch["sources"].items():
            if isinstance(info, dict):
                add("| %s | %s | %s |" % (name, info.get("date", ""), info.get("note", "")))
            else:
                add("| %s | %s |  |" % (name, info))
        add("")
    if batch.get("cost"):
        add("## 八、取数成本\n")
        add("```\n%s\n```\n" % json.dumps(batch["cost"], ensure_ascii=False, indent=1))

    with open(out_base + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    return "\n".join(L)


def _pick_font():
    for p in FONT_CANDIDATES:
        if p and os.path.exists(p) and not p.lower().endswith(".ttc"):
            return p
    for p in FONT_CANDIDATES:  # ttc 最后试
        if p and os.path.exists(p):
            return p
    return None


def build_pdf(cases, batch, out_base, stat, concl, combos, multi):
    try:
        from fpdf import FPDF
        from fpdf.enums import XPos, YPos
        from fpdf.fonts import FontFace
    except ImportError:
        return False, "未安装 fpdf2（pip install fpdf2）"

    font = _pick_font()
    if not font:
        return False, "找不到可嵌入的中文字体；设 PATENT_REPORT_FONT 指向一个 .ttf"

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(10, 12, 10)
    pdf.set_auto_page_break(auto=True, margin=14)
    try:
        pdf.add_font("CJK", "", font)
    except Exception as e:  # noqa: BLE001
        return False, "字体加载失败 %s: %s" % (font, e)
    pdf.set_font("CJK", "", 10)
    n = len(cases)
    now = time.strftime("%Y-%m-%d %H:%M")

    def h(txt, size=13):
        pdf.ln(2)
        pdf.set_font("CJK", "", size)
        pdf.multi_cell(0, size * 0.62, txt, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("CJK", "", 10)

    def p(txt):
        pdf.set_font("CJK", "", 9.5)
        pdf.multi_cell(0, 5.2, txt, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("CJK", "", 10)

    def table(cols, rows, widths=None, size=9):
        pdf.set_font("CJK", "", size)
        # 只嵌了常规字重，表头样式必须显式去掉加粗，否则 fpdf2 会要求 B 字体
        face = FontFace(emphasis="", color=(20, 20, 20), fill_color=(236, 236, 236))
        with pdf.table(col_widths=widths, line_height=size * 0.62,
                       headings_style=face, first_row_as_headings=True) as t:
            r = t.row()
            for c in cols:
                r.cell(str(c))
            for row in rows:
                r = t.row()
                for c in row:
                    r.cell(str(c))
        pdf.set_font("CJK", "", 10)

    pdf.add_page()
    pdf.set_font("CJK", "", 17)
    pdf.multi_cell(0, 9, batch.get("title") or "专利非正常申请批量筛查报告",
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1)
    p("生成时间 %s｜案件数 %d｜口径：%d 项全维且门，任一维不过＝非正常，缺数据＝待核查"
      % (now, n, len(DIMS)))
    if batch.get("rules_version"):
        p("阈值快照：%s" % batch["rules_version"])
    if batch.get("note"):
        pdf.multi_cell(0, 5.2, "批次说明：%s" % batch["note"], new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    h("一、结论概览")
    table(["结论", "件数", "占比"],
          [[k, concl[k], "%.1f%%" % (100.0 * concl[k] / n if n else 0)] for k in CONCL]
          + [["合计", n, "100%"]], widths=(40, 30, 30))

    h("二、各维度命中统计")
    p("一个案子可能同时挂多项，「不过」列合计会大于非正常件数。")
    table(["维度", "不过", "待核查", "不适用", "通过", "不过率"],
          [[d, stat[d]["fail"], stat[d]["pending"], stat[d]["na"], stat[d]["pass"],
            "%.1f%%" % (100.0 * stat[d]["fail"] / n if n else 0)] for d in DIMS],
          widths=(46, 20, 24, 24, 22, 24))

    agents = {}
    for c in cases:
        ag = c.get("agent") or ""
        if not ag:
            continue
        e = agents.setdefault(ag, {"pubs": [], "state": c["dims"].get("②代理量"),
                                   "note": (c.get("notes") or {}).get("②代理量", "")})
        e["pubs"].append(label(c))
    if agents:
        h("②代理量明细（香橼树，2026-01-01 至取数日 %s）" % AGENT_DATE)
        rows = []
        for ag, e in sorted(agents.items(), key=lambda kv: -len(kv[1]["pubs"])):
            m = re.search(r"代理量\s*(\d+)\s*件", e["note"])
            rows.append([ag, ("%s 件" % m.group(1)) if m else "未核", len(e["pubs"]),
                         SHORT.get(e["state"], "?"), "、".join(e["pubs"])])
        table(["代理师", "当年代理量", "案子数", "判定", "公开号"], rows,
              widths=(26, 26, 18, 16, 120), size=8)

    h("三、命中项组合")
    if combos:
        table(["命中组合", "件数", "公开号"],
              [[" + ".join(c), k, "、".join(label(cc) for cc in cases
                                            if tuple(d for d in DIMS
                                                     if cc["dims"].get(d) == "fail") == c)]
               for c, k in combos.most_common()], widths=(58, 16, 100), size=8)
    else:
        p("无命中项。")
    p("非正常案的命中项数分布：" + "，".join("%d 项 %d 件" % (k, v)
                                             for k, v in sorted(multi.items())))

    pdf.add_page(orientation="L")   # 先换页再写标题，避免标题孤零零挂在上一页底部
    h("四、逐案总表")
    head = ["#", "公开号/名称", "申请人", "结论"] + [HEAD_SHORT[d] for d in DIMS] + ["命中项"]
    rows = []
    for i, c in enumerate(cases, 1):
        hit = "+".join(HEAD_SHORT[d] for d in DIMS if c["dims"].get(d) == "fail") or "-"
        rows.append([i, label(c), (c.get("applicants") or [""])[0][:16],
                     c["conclusion"]]
                    + [SHORT.get(c["dims"].get(d, "pending"), "?") for d in DIMS]
                    + [hit])
    # 12.2 * len(DIMS) + 固定列；横排可用约 277mm
    widths = [7, 27, 30, 13] + [12.2] * len(DIMS) + [45]
    table(head, rows, widths=widths, size=6.8)

    pdf.add_page(orientation="P")
    h("五、复核清单")
    rev = [c for c in cases if c.get("review")]
    if rev:
        for c in rev:
            p("· %s（%s）：%s" % (label(c), c["conclusion"], c["review"]))
    else:
        p("无。")

    pend = [c for c in cases if c["conclusion"] == "待核查"]
    if pend:
        h("六、待核查案件缺什么")
        for c in pend:
            miss = [d for d in DIMS if c["dims"].get(d) == "pending"]
            p("· %s：%s" % (label(c), "、".join(miss) or "未注明"))

    b5 = [c for c in cases if c["dims"].get("⑤b一致性") == "fail"]
    if b5:
        h("五（续）、⑤b 降级参考（同名碰撞/母子公司分流）")
        p("⑤b 比对「第一发明人（inventors[0]）名下记录」的申请人。异申请人若实为同名他人，该案⑤b 可降级。")
        rows = []
        for c in b5:
            fl = c.get("⑤b_foreign") or []
            other = "+".join(HEAD_SHORT[d] for d in (c.get("still_fail") or [])) or "-"
            rows.append([label(c),
                         "、".join(fl[:2]) + ("…" if len(fl) > 2 else "") if fl else "-",
                         c.get("downgrade_if_5b_collision", "-"), other])
        table(["公开号", "⑤b 异申请人（样本）", "若无⑤b", "其余挂项"], rows,
              widths=(30, 118, 26, 36), size=8)

    pdf.add_page(orientation="P")
    h("六、逐案证据")
    p("判据锚点：权项号、说明书段号、外部数据来源与取数日。（本报告逐维列出证据）")
    for c in cases:
        pdf.set_font("CJK", "", 10.5)
        pdf.multi_cell(0, 5.6, "%s｜%s" % (label(c), c.get("title", "")),
                       new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        p("申请人：%s｜第一发明人：%s｜申请日：%s｜结论：%s"
          % ("、".join(c.get("applicants") or []), c.get("first_inventor", ""),
             c.get("app_date", ""), c["conclusion"]))
        verbose = bool(batch.get("verbose_evidence"))
        for d in DIMS:
            st = c["dims"].get(d)
            note = (c.get("notes") or {}).get(d, "—")
            if verbose:
                p("  · %s %s：%s" % (d, LONG.get(st, "?"), note))
            elif d in ("②代理量", "⑤a当年件数") and st in ("pass", "fail", "pending"):
                p("  · %s %s：%s" % (d, LONG.get(st, "?"), note))
            elif st in ("fail", "pending"):
                p("  · %s %s：%s" % (d, "不过" if st == "fail" else "待核查", note))
        pdf.ln(1)

    if batch.get("sources"):
        h("七、数据来源与取数日")
        rows = []
        for name, info in batch["sources"].items():
            if isinstance(info, dict):
                rows.append([name, info.get("date", ""), info.get("note", "")])
            else:
                rows.append([name, str(info), ""])
        table(["数据源", "取数日", "说明"], rows, widths=(46, 32, 100), size=8)

    if batch.get("cost"):
        h("八、取数成本")
        p(json.dumps(batch["cost"], ensure_ascii=False, indent=1))

    pdf.output(out_base + ".pdf")
    return True, ""


def main(argv):
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("cases_dir")
    ap.add_argument("-o", "--out", default="report")
    ap.add_argument("--title", default="")
    a = ap.parse_args(argv[1:])

    cases, batch = load_cases(a.cases_dir)
    global AGENT_DATE
    AGENT_DATE = (batch.get("sources", {}).get("香橼树", {}) or {}).get("date") or time.strftime("%Y-%m-%d")
    if not cases:
        print("没有找到案件 JSON：%s/*.json" % a.cases_dir)
        return 2
    if a.title:
        batch["title"] = a.title

    out = os.path.abspath(a.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)

    stat, concl, combos, multi = compute(cases)
    build_md(cases, batch, out, stat, concl, combos, multi)
    print("md  ->", out + ".md")
    ok, why = build_pdf(cases, batch, out, stat, concl, combos, multi)
    print(("pdf -> " + out + ".pdf") if ok else ("pdf 跳过：" + why))
    print("非正常 %d / 正常 %d / 待核查 %d；不过项合计 %d"
          % (concl["非正常"], concl["正常"], concl["待核查"],
             sum(stat[d]["fail"] for d in DIMS)))
    return 0 if ok else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
