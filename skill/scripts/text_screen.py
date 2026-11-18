#!/usr/bin/env python3
"""第 1 层：专利公开文本的两项判据筛查（手段、效果；只看文件本身，不取外部数据）。

用法:
    python3 scripts/text_screen.py <目录或 PDF/文本 列表...> [--json out.json] [--batch-from-input]
    python3 scripts/text_screen.py 测试用例/某批 --json 结果.json
    python3 scripts/text_screen.py a.pdf b.pdf
    python3 scripts/text_screen.py 某批 --batch-from-input   # 第二层批次用；送审文件覆盖机构当月全部时

输出（stdout，一行一案的 JSON，最后一行是汇总）:
    pub / applicant / agency / app_date / title
    means   {"hit": bool, "clauses": [...], "notes": [...]}
    effect  {"hit": bool, "clauses": [...], "notes": [...]}
    batch   {"state": "hit|miss|unevaluated", "note": str}   仅供第二层参考，不参与本层结论
    verdict 非正常 | 待核
    hints   复核提示（结构案/仅缺验证数据，需人工看附图）

判据编号与 `rules/00-thresholds.md` 对应；改阈值改那里，本文件同步改常量。
依赖: pypdf（缺失时回退 pdfplumber）
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

# ---------------------------------------------------------------- 常量（对齐 rules/00-thresholds.md）
T = {
    "refs_stack": 12,      # 第十条 结构堆砌：权1 部件数
    "asm_stack": 5,        # 第十条 结构堆砌：装配关系处数
    "refs_dep": 20,        # 第九条第一项：权1 部件数
    "asm_dep": 4,          # 第九条第一项：装配关系处数
    "dep_n": 6,            # 第九条第一项：从权条数
    "unit_n": 3,           # 第八条第三项：模块化处数
    "act_n": 3,            # 第八条第一项：通用动作种类
    "proc_n": 3,           # 第八条第一项：工艺动作处数
    "exp_single": 1,       # 说明书仅一个实施例
    "param_few": 5,        # 说明书参数少于 5 处
    "mgmt_n": 8,           # 第十二条：事项型词频
    "batch_n": 10,         # 第十八条：同月件数
    "batch_app": 6,        # 第十八条：同月申请人数
    "cite_ratio": 0.20,    # 第十八条：引用在先文献比例上限
    "bg_ratio": 0.50,      # 第十八条：背景技术达 400 字比例上限
    "year_n": 30,          # 第十八条累计口径：12 个月件数
    "year_app": 15,        # 第十八条累计口径：12 个月申请人数
}

PARAM = (r"\d+(?:\.\d+)?\s*(?:%|％|mm|cm|℃|MPa|kPa|kg|g|L|mL|倍|r/min|rpm|min|s|h|"
         r"Hz|kHz|MHz|GHz|A|V|W|kW|N·m|dB|μm|nm|重量份|份|目|m/s|度|°|转|次)")
ACT = r"(?:获取|获得|采集|分析|判断|确定|输出|构建|生成|计算|选取|选择|匹配|评估|优化|训练|识别|提取|预测|分配|发送|接收|统计|比对)"
PROC = (r"(?:混合|搅拌|刮平|振实|抹平|养护|干燥|烘干|烧结|打结|成型|压制|喷涂|焊接|切割|清洗|"
        r"冷却|加热|浸渍|涂覆|挤压|拉伸|退火|淬火|破碎|筛分|过滤|沉淀|结晶|蒸馏|萃取|发酵|杀菌|"
        r"消毒|封装|贴装|刻蚀|镀膜|溅射)")
STRUCT = (r"(?:壳体|支架|底座|法兰|螺栓|螺钉|气缸|电机|转轴|轴承|齿轮|导轨|弹簧|横梁|立柱|机架|"
          r"工作台|夹具|传感器|滑块|丝杆|皮带|链轮|伸缩杆|连接板|安装板)")
ASM = r"(?:固定连接|固定安装|固定设置|紧固连接|铰接|滑动连接|螺纹连接|转动连接|套设|设有|设置有)"
FORM = r"(?:数学描述为|公式为|如下式|计算公式|式[（(]\s*\d+\s*[）)]|其中[，,][A-Za-z][A-Za-z0-9_]{0,3}为)"
QUANT_RESULT = (r"(?:为|达到|达|不超过|小于|高于|低于|≥|≤)[^。；]{0,18}?\d+(?:\.\d+)?\s*"
                r"(?:%|％|倍|dB|μm|nm|MPa|kPa|mg/L|ppm|CFU|g/L|mPa·s|℃|h|min)")
WORK = r"(?:工作原理|工作过程|使用时|操作时|实施时|具体工作|运行过程|工作流程)"
MGMT = r"(?:管理|评价|评估|推荐|考核|评分|排名|调度|决策|满意度|画像|标签|风险等级)"


# ---------------------------------------------------------------- 文本切分
def norm(t: str) -> str:
    return re.sub(r"[\u3000\s]+", "", t or "")


def read_text(path: str) -> str:
    if path.lower().endswith(".txt"):
        return open(path, encoding="utf-8", errors="ignore").read()
    try:
        from pypdf import PdfReader
        r = PdfReader(path)
        return "\n".join((p.extract_text() or "") for p in r.pages)
    except Exception:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)


def clean_claims(cl: str) -> str:
    s = re.sub(r"(?:权利要求书|说明书)\d+/\d+页", "", cl)
    s = re.sub(r"(?:权利要求书|说明书)\s*\d{1,3}", "", s)   # 稿件式页脚：权利要求书2 / 说明书5
    s = re.sub(r"CN\d{9}[A-Z]\d{4}\.\d{2}\.\d{2}", "", s)
    s = re.sub(r"CN\d{9}[A-Z]", "", s)
    return re.sub(r"\d{4}\.\d{2}\.\d{2}", "", s)


# 稿件式文本（Word 转 PDF、未带著录项首页）的章节定位
DOC_TITLE = re.compile(r"说明书\s*\d{1,2}([^。；，、]{4,40}?)技术领域")


def claim_start(n: str) -> int:
    """稿件式文本：第一处「权利要求书」标题之后的第 1 项起点（页脚页码、Word 域代码会夹在中间）。"""
    for mm in re.finditer(r"权利要求书", n):
        seg = n[mm.end(): mm.end() + 80]
        c = re.search(r"1\s*[.．、]\s*(?=[\u4e00-\u9fa5])", seg)
        if c:
            return mm.end() + c.start()
    return -1


def split_sections(t: str) -> tuple[str, str, str]:
    """返回 (首页著录项, 权利要求书, 说明书)。"""
    n = norm(t)
    m = re.search(r"权利要求书\s*\d+\s*页\s*说明书\s*\d+\s*页(?:\s*附图\s*\d+\s*页)?", n)
    if m:
        hdr_end = m.end()
        foots = [f for f in re.finditer(r"权利要求书\d+/\d+页", n) if f.start() > hdr_end]
        if foots:
            cend = foots[-1].end()
        else:
            i = n.find("技术领域", hdr_end)
            cend = i if i > 0 else hdr_end + 6000
        return n[:hdr_end], clean_claims(n[hdr_end:cend]), n[cend:]

    # 稿件式文本：无「权利要求书N页 说明书M页」页脚，按章标题定位
    cs = claim_start(n)
    if cs < 0:
        i = n.find("(57)")
        hdr_end = i + 40 if i > 0 else 0
        j = n.find("技术领域", hdr_end)
        cend = j if j > 0 else hdr_end + 6000
        return n[:hdr_end], clean_claims(n[hdr_end:cend]), n[cend:]
    i = n.find("技术领域", cs)
    cend = i if i > 0 else min(len(n), cs + 8000)
    claims = n[cs:cend]
    tail = re.search(r"说明书\s*\d{0,2}[^。；，]*$", claims)
    if tail:
        claims = claims[: tail.start()]
    return n[:cs], clean_claims(claims), n[cend:]


def claim1_of(claims: str) -> str:
    cl = claims.lstrip("。；")
    m = re.search(r"(?:^|。)(2)[.．、](?=[\u4e00-\u9fa5])", cl)
    return cl[: m.start() + 1] if m else cl


def claims_of(claims: str) -> list[tuple[int, str]]:
    cl = claims.lstrip("。；")
    cands = [(m.start() + (1 if m.group(0)[0] in "。；" else 0), int(m.group(1)))
             for m in re.finditer(r"(?:^|[。；])(\d{1,2})[.．、](?=[\u4e00-\u9fa5])", cl)]
    best, cur = [], []
    for pos, num in cands:
        if not cur:
            if num == 1:
                cur = [(pos, num)]
            continue
        if num == cur[-1][1] + 1:
            cur.append((pos, num))
        elif num == 1:
            cur = [(pos, num)]
        else:
            if len(cur) > len(best):
                best = cur
            cur = []
        if len(cur) > len(best):
            best = cur
    if not best:
        return [(1, cl)]
    return [(n, cl[p: (best[i + 1][0] if i + 1 < len(best) else len(cl))])
            for i, (p, n) in enumerate(best)]


# ---------------------------------------------------------------- 特征
def features(t: str) -> dict:
    hdr, claims, desc = split_sections(t)
    cl = claims_of(claims)
    c1 = claim1_of(claims)
    dep = [s for _, s in cl[1:]]
    f: dict = {}
    f["claim_n"] = len(cl)
    f["c1_refs"] = len(re.findall(r"[（(]\s*\d{1,3}\s*[）)]", c1))
    f["c1_act"] = len(set(re.findall(ACT, c1)))
    f["c1_proc"] = len(re.findall(PROC, c1))
    f["c1_step"] = 1 if re.search(r"步骤|S\d", c1) else 0
    f["c1_struct"] = len(set(re.findall(STRUCT, c1)))
    f["c1_param"] = len(re.findall(PARAM, c1))
    f["c1_asm"] = len(re.findall(ASM, c1))
    f["c1_unit"] = len(re.findall(r"(?:单元|模块)[，,、]?用于", claims))
    # 实施例只认编号列明的方案，说明性文字不计
    f["exp"] = len(set(re.findall(r"实施例\s*[一二三四五六七八九十\d]+(?![起般个])", desc)))
    f["form"] = len(re.findall(FORM, desc))
    f["data"] = len(re.findall(r"(?:实验|试验|测试|检测结果|仿真|对比实验|误差|标定|实测|"
                               r"测定|加标回收|检出限|精密度|性能测试)", desc))
    f["param"] = len(re.findall(PARAM, desc))
    f["work"] = len(re.findall(WORK, desc))
    f["table"] = len(re.findall(r"表\s*\d+", desc))
    f["mgmt"] = len(re.findall(MGMT, desc))
    f["var_def"] = len(re.findall(r"其中[，,][A-Za-z\u4e00-\u9fa5]{1,6}为", claims + desc))
    # 从权
    f["dep_n"] = len(dep)
    f["dep_param"] = len(re.findall(PARAM, "".join(dep)))
    f["dep_parts"] = sum(1 for s in dep
                         if re.search(STRUCT, s) or re.search(r"所述[^，。；]{1,12}(?:包括|设有|设置有|连接|安装|为)", s))
    # 量化结果
    f["quant_result"] = len(re.findall(QUANT_RESULT, desc))
    f["compare"] = len(re.findall(r"(?:对比例|比较例|对照组)", desc))
    f["perf"] = len(re.findall(r"(?:性能测试|检测结果|测试结果|试验结果|性能检测|表征结果)", desc))
    f["has_data"] = bool(f["quant_result"] >= 1 or f["compare"] >= 1 or f["perf"] >= 1 or f["table"] >= 1)
    # 公式是否出现在发明内容/技术方案段
    i = min([x for x in (desc.find("发明内容"), desc.find("技术方案")) if x > 0] or [len(desc)])
    j = desc.find("具体实施方式", i)
    if j < 0:
        j = desc.find("实施例", i)
    if j < 0:
        j = min(len(desc), i + 8000)
    core = desc[i:j]
    f["form_core"] = len(re.findall(FORM, core))
    f["eq_all"] = len(re.findall(r"[=＝]", desc))
    # 著录项（著录项首页可能用全角括号，统一后再匹配）
    h = hdr.replace("（", "(").replace("）", ")")
    f["util"] = "实用新型专利" in h
    if not f["util"] and "发明专利申请" not in h:
        # 稿件式文本无 (12) 行，按正文自称判定
        um = len(re.findall("本实用新型", t))
        iv = len(re.findall("本发明", t))
        f["util"] = um >= 3 and iv == 0
    m = re.search(r"\(22\)申请日\D*(\d{8}|\d{4}\.\d{2}\.\d{2})", h)
    ad = m.group(1) if m else ""
    if not ad:
        m = re.search(r"申请日[:：]?\s*(\d{8})", h)
        ad = f"{m.group(1)[:4]}.{m.group(1)[4:6]}.{m.group(1)[6:]}" if m else ""
    f["app_date"] = ad
    i = h.find("(74)")
    ag = ""
    if i > 0:
        blk = h[i:]
        j = blk.find("(51)")
        if j > 0:
            blk = blk[:j]
        ag = blk.replace("(74)专利代理机构", "").split("专利代理师")[0]
    f["agency"] = ag
    m = re.search(r"\((?:71|73)\)(?:申请人|专利权人)(.*?)(?=\(72\)|\(74\)|\(51\))", h)
    ap = ""
    if m:
        parts = [x for x in re.split(r"地址[^专]*?(?=申请人|专利权人|$)", m.group(1)) if x.strip()]
        ap = ";".join(x.strip() for x in parts)
    f["applicant"] = ap
    m = re.search(r"\(54\)发明名称(.+?)(?:\(57\)|摘要)", h)
    if not m:
        m = DOC_TITLE.search(hdr)
    f["title"] = m.group(1)[:60] if m else ""
    bg = ""
    i = desc.find("背景技术")
    if i > 0:
        j = min([x for x in (desc.find("发明内容", i), desc.find("技术方案", i), desc.find("有益效果", i)) if x > 0]
                or [i + 3000])
        bg = desc[i:j]
    f["bg_len"] = len(bg)
    f["bg_cite"] = len(re.findall(r"CN\d{6,}[A-Z]?", bg))
    return f


# ---------------------------------------------------------------- 判据
def means_judge(f: dict) -> dict:
    """第三章：手段判据。返回命中的条款与说明。"""
    formula = f["form_core"] >= 1 or f["eq_all"] >= 2
    hits, notes = [], []

    broad = False
    if f["c1_act"] >= T["act_n"] and f["c1_refs"] == 0 and f["c1_param"] < 3 \
            and f["form"] == 0 and f["exp"] <= 1:
        broad = True
        hits.append("第八条第一项（通用动作）")
    if f["c1_proc"] >= T["proc_n"] and f["c1_step"] and f["c1_refs"] == 0 \
            and f["c1_param"] < 3 and f["form"] == 0:
        broad = True
        hits.append("第八条第一项（工艺动作）")
    if f["c1_unit"] >= T["unit_n"] and f["c1_refs"] < 4:
        broad = True
        hits.append("第八条第三项（功能性模块）")
    if broad and formula:
        hits = [h for h in hits if "第八条" not in h]
        notes.append("第十一条：说明书在发明内容/技术方案部分给出公式，视为手段可复现")

    if f["c1_refs"] >= T["refs_stack"] and f["c1_asm"] >= T["asm_stack"] \
            and f["c1_param"] < 3 and f["exp"] == 0:
        hits.append("第十条（结构堆砌）")
        notes.append(f"权1 部件 {f['c1_refs']} 个、装配关系 {f['c1_asm']} 处、无工程参数、无实施例")

    if f["dep_n"] >= T["dep_n"] and f["dep_parts"] >= f["dep_n"] - 1 \
            and f["dep_param"] == 0 and f["exp"] == 0 \
            and f["c1_refs"] >= T["refs_dep"] and f["c1_asm"] >= T["asm_dep"]:
        hits.append("第九条第一项（部件罗列且从权凑数）")

    if f["c1_struct"] >= 3 and f["exp"] <= T["exp_single"] and f["work"] == 0 \
            and f["param"] < T["param_few"]:
        hits.append("第九条第二项（仅一个实施例且无工作过程）")

    # 「管理」出现在设备名里（电池管理、热管理）不算事项型，独权有实体结构即排除本款
    if f["mgmt"] >= T["mgmt_n"] and f["var_def"] < 6 and f["form"] == 0 \
            and f["exp"] <= 1 and f["param"] < T["param_few"] \
            and f["c1_struct"] < 3 and f["c1_refs"] < 4:
        hits.append("第十二条（技术问题不落地）")

    return {"hit": bool(hits), "clauses": hits, "notes": notes}


def effect_judge(f: dict) -> dict:
    """第四章：效果判据，含第七条第三项机械豁免与第四项实用新型豁免。"""
    if f["util"]:
        return {"hit": False, "clauses": [], "notes": ["实用新型不适用效果判据"]}
    mech = f["c1_struct"] >= 3
    if mech and (f["c1_param"] >= 3 or f["dep_param"] >= 3):
        return {"hit": False, "clauses": [], "notes": ["机械装置类已记载工程参数，不因缺实验数据认定"]}
    if not f["has_data"]:
        return {"hit": True, "clauses": ["第十四条（无量化结果）"],
                "notes": ["无数据表、无对比例、无指标数值形式的结论"]}
    return {"hit": False, "clauses": [], "notes": []}


def month_index(s: str) -> int | None:
    try:
        y, m = s[:7].split(".")
        return int(y) * 12 + int(m) - 1
    except Exception:
        return None


def batch_judge(cases: list[dict], from_input: bool) -> dict:
    """第五章：批次判据。from_input=True 表示送审文件覆盖该机构当月全部申请。"""
    if not from_input:
        return {"state": "unevaluated",
                "note": "送审文件不等于机构当月全量，须按 `rules/30-batch.md` 用代理机构代码查该月全部申请"}
    groups: dict = {}
    for c in cases:
        if not c["agency"] or not c["app_date"]:
            continue
        groups.setdefault(c["agency"], []).append(c)
    def mark(sub: list[dict], why: str) -> None:
        apps = {c["applicant"] for c in sub}
        cite = sum(1 for c in sub if c["bg_cite"] >= 1) / len(sub)
        bg = sum(1 for c in sub if c["bg_len"] >= 400) / len(sub)
        if cite >= T["cite_ratio"] or bg >= T["bg_ratio"]:
            return
        why = why.format(n=len(sub), app=len(apps), cite=cite, bg=bg)
        for c in sub:
            c.setdefault("_batch", why)

    for key, lst in groups.items():
        months = [month_index(c["app_date"]) for c in lst if month_index(c["app_date"]) is not None]
        if not months:
            continue
        # 第十八条第一款：同一自然月
        for i in range(min(months), max(months) + 1):
            sub = [c for c in lst if month_index(c["app_date"]) == i]
            if len(sub) >= T["batch_n"] and len({c["applicant"] for c in sub}) >= T["batch_app"]:
                mark(sub, "第十八条第一款：同月 {n} 件、{app} 家申请人，引用 {cite:.0%}、长背景 {bg:.0%}")
        # 第十八条第三款：累计口径（12 个月）
        for i in range(min(months), max(months) + 2):
            sub = [c for c in lst if i <= (month_index(c["app_date"]) or -1) < i + 12]
            if len(sub) >= T["year_n"] and len({c["applicant"] for c in sub}) >= T["year_app"]:
                mark(sub, "第十八条第三款：12 个月 {n} 件、{app} 家申请人，引用 {cite:.0%}、长背景 {bg:.0%}")
    return {"state": "evaluated", "note": "以送审文件为全量口径"}


def verdict_of(c: dict) -> tuple[str, list[str]]:
    """第一层只判手段与效果：两项都成立即非正常，其余一律待核。

    批次与主体不在本层，未查之前不得出可提交。实用新型不适用效果判据，
    因此在第一层永远不会出非正常，须进第二层由批次补齐。
    """
    hints = []
    if c["means"]["hit"] and any(k in c["means"]["clauses"] for k in ("第十条（结构堆砌）", "第九条第一项（部件罗列且从权凑数）")):
        hints.append("结构案：核对附图、装配关系与实物，确认后再定")
    if c["effect"]["hit"] and not c["means"]["hit"]:
        hints.append("仅缺验证数据：核对是否属于机械结构无参数的情形")
    if c["means"]["hit"] and c["effect"]["hit"]:
        return "非正常", hints
    return "待核", hints


def collect(paths: list[str]) -> list[str]:
    out = []
    for p in paths:
        if os.path.isdir(p):
            out += sorted(glob.glob(os.path.join(p, "**", "*.pdf"), recursive=True))
            out += sorted(glob.glob(os.path.join(p, "**", "*.txt"), recursive=True))
        else:
            out.append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--json", help="把逐案结果写入该文件")
    ap.add_argument("--batch-from-input", action="store_true",
                    help="送审文件覆盖该机构当月全部申请时，用本批文件算批次")
    args = ap.parse_args()

    files = collect(args.paths)
    if not files:
        print("没有找到 PDF 或 txt", file=sys.stderr)
        return 2

    cases = []
    for p in files:
        try:
            f = features(read_text(p))
        except Exception as e:
            print(json.dumps({"file": os.path.basename(p), "error": str(e)[:100]}, ensure_ascii=False))
            continue
        f["file"] = os.path.basename(p)
        f["pub"] = re.sub(r"\.(pdf|txt)$", "", f["file"])
        f["means"] = means_judge(f)
        f["effect"] = effect_judge(f)
        cases.append(f)

    batch_judge(cases, args.batch_from_input)

    summary = {"非正常": 0, "待核": 0}
    rows = []
    for c in cases:
        v, hints = verdict_of(c)
        summary[v] += 1
        row = {
            "file": c["file"], "pub": c["pub"], "title": c["title"], "applicant": c["applicant"],
            "agency": c["agency"], "app_date": c["app_date"], "util": c["util"],
            "refs": c["c1_refs"], "exp": c["exp"],
            "means": c["means"], "effect": c["effect"],
            "batch": {"state": "hit", "note": c.get("_batch", "")} if c.get("_batch") else
                     {"state": "miss" if args.batch_from_input else "unevaluated",
                      "note": "" if args.batch_from_input else "需查机构当月全量"},
            "verdict": v, "hints": hints,
        }
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))

    print(json.dumps({"summary": summary, "total": len(cases)}, ensure_ascii=False))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fp:
            json.dump(rows, fp, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
