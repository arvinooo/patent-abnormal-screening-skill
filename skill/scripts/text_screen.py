#!/usr/bin/env python3
"""文本步：专利公开文本的判据筛查（手段、效果；只看文件本身，不取外部数据）。

用法:
    python3 scripts/text_screen.py <目录或 PDF/文本 列表...> [--json out.json]
    python3 scripts/text_screen.py 测试用例/某批 --json 结果.json
    python3 scripts/text_screen.py a.pdf b.pdf

输出（stdout，一行一案的 JSON，最后一行是汇总）:
    pub / applicant / agency / agent / inventors / app_date / title
    means   {"hit": bool, "clauses": [...], "notes": [...]}
    effect  {"hit": bool, "clauses": [...], "notes": [...]}
    verdict 非正常 | 存疑 | 正常   硬证据单判、软证据须与第十六条同时成立（见 thresholds.md）
    hints   复核提示（结构案/仅缺验证数据，需人工看附图）

判据编号与 `thresholds.md` 对应；改阈值改那里，本文件同步改常量。
依赖: pypdf（缺失时回退 pdfplumber）；docx 用 python-docx；首页发明人取不到时用 pdfplumber 重读第 1 页
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

# ---------------------------------------------------------------- 常量（对齐 thresholds.md）
T = {
    "refs_stack": 12,      # 第十二条 结构堆砌：权1 部件数（仅发明）
    "asm_stack": 5,        # 第十二条 结构堆砌：装配关系处数
    "refs_dep": 20,        # 第十一条第一项：权1 部件数
    "asm_dep": 4,          # 第十一条第一项：装配关系处数
    "dep_n": 6,            # 第十一条第一项：从权条数
    "unit_n": 3,           # 第十条 功能性模块：模块化处数
    "act_ratio": 0.6,      # 第九条第一项：通用动作分句占比
    "proc_n": 3,           # 第九条第二项：工艺动作处数
    "exp_single": 1,       # 第十一条第二项：说明书仅一个实施例
    "param_few": 5,        # 第十一条第二项：说明书参数少于 5 处
    "mgmt_n": 8,           # 第十四条：事项型词频
    "cite_ratio": 0.20,    # 第二十条：引用在先文献比例上限
    "bg_ratio": 0.50,      # 第二十条：背景技术达 400 字比例上限
    "year_n": 30,          # 第二十条：12 个月件数
    "year_app": 15,        # 第二十条：12 个月申请人数
}

MODEL = (r"(?:CNN|LSTM|GRU|U-?Net|YOLO|Transformer|BERT|ResNet|GAN|卷积|神经网络|注意力|"
         r"特征图|损失函数|梯度|编码器|解码器|随机森林|支持向量机|决策树|聚类|回归模型|深度学习)")
TESTRES = (r"(?:测试|检测|试验|实验|性能)(?:结果|数据)[^。；]{0,80}?\d+(?:\.\d+)?"
           r"|(?:腐蚀率|除垢率|抗菌率|抗拉强度|伸长率|检出限|回收率|精密度|纯度)[^。；]{0,40}?\d+(?:\.\d+)?")

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
    if path.lower().endswith(".docx"):
        import docx
        d = docx.Document(path)
        parts = [p.text for p in d.paragraphs]
        for tb in d.tables:
            for row in tb.rows:
                parts.extend(c.text for c in row.cells)
        return "\n".join(parts)
    try:
        from pypdf import PdfReader
        r = PdfReader(path)
        return "\n".join((pg.extract_text() or "") for pg in r.pages)
    except Exception:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            return "\n".join((pg.extract_text() or "") for pg in pdf.pages)


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


def _split_inventors(raw: str) -> list[str]:
    s = re.sub(r"\d{17}[\dXx]", "", raw or "")
    parts = [x.strip(" ，,、;；") for x in re.split(r"[，,、;；\s\u3000]+", s)]
    return [x for x in parts if 1 < len(x) <= 6 and re.fullmatch(r"[\u4e00-\u9fff·A-Za-z]+", x)]


# ---------------------------------------------------------------- 特征
def features(t: str) -> dict:
    hdr, claims, desc = split_sections(t)
    nchar = len(norm(t))
    cl = claims_of(claims)
    c1 = claim1_of(claims)
    dep = [s for _, s in cl[1:]]
    f: dict = {}
    f["nchar"] = nchar
    f["claim_n"] = len(cl)
    f["c1_refs"] = len(re.findall(r"[（(]\s*\d{1,3}\s*[）)]", c1))
    f["c1_act"] = len(set(re.findall(ACT, c1)))
    clauses = [s for s in re.split(r"[；;。]", c1) if s.strip()]
    f["c1_act_ratio"] = (round(sum(1 for s in clauses if re.search(ACT, s)) / len(clauses), 2)
                         if clauses else 0)
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
    f["perf_table"] = len(re.findall(
        r"(?:测试|检测|试验|实验|性能)[^。；]{0,60}表\s*\d"
        r"|表\s*\d[^。]{0,60}(?:测试|检测|试验|实验|性能|结果)", desc))
    f["test_res"] = len(re.findall(TESTRES, desc))
    f["model_claim"] = len(re.findall(MODEL, claims))
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
    # 公式是否出现在发明内容/技术方案段
    i = min([x for x in (desc.find("发明内容"), desc.find("技术方案")) if x > 0] or [len(desc)])
    j = desc.find("具体实施方式", i)
    if j < 0:
        j = desc.find("实施例", i)
    if j < 0:
        j = min(len(desc), i + 8000)
    core = desc[i:j]
    f["form_core"] = len(re.findall(FORM, core))
    f["model_core"] = len(re.findall(MODEL, core))
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
    m = re.search(r"\(72\)发明人([\s\S]{0,220}?)(?:\(74\)|\(51\)|\(54\)|$)", h)
    names = _split_inventors(m.group(1)) if m else []
    f["inventors"] = names
    m = re.search(r"专利代理师\s*([\u4e00-\u9fff]{2,4})", h)
    f["agent"] = m.group(1) if m else ""
    m = re.search(r"\((?:71|73)\)(?:申请人|专利权人)(.*?)(?=\(72\)|\(74\)|\(51\))", h)
    ap = ""
    if m:
        parts = [x for x in re.split(r"地址[^专]*?(?=申请人|专利权人|$)", m.group(1)) if x.strip()]
        ap = ";".join(x.strip() for x in parts)
    f["applicant"] = ap
    m = re.search(r"\(54\)(?:发明|实用新型|外观设计)?名称(.+?)(?:\(57\)|摘要)", h)
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
    if f["c1_act_ratio"] >= T["act_ratio"] and f["c1_refs"] < 4 and f["c1_param"] < 3 \
            and f["form_core"] == 0 and f["model_claim"] + f["model_core"] == 0:
        broad = True
        hits.append("第九条第一项（通用动作）")
    if f["c1_proc"] >= T["proc_n"] and f["c1_step"] and f["c1_param"] < 3 \
            and f["c1_refs"] < 4 and f["c1_struct"] < 3:
        # 工艺动作是正常化工/材料案的常见写法，单独命中精确率过低，只记提示
        notes.append("第九条第二项（工艺动作）：仅提示，不计入分档")
    if f["c1_unit"] >= T["unit_n"] and f["model_claim"] + f["model_core"] == 0 and f["exp"] == 0:
        broad = True
        hits.append("第十条（功能性模块）")
    if broad and formula:
        hits = [h for h in hits if "第九条" not in h and "第十条" not in h]
        notes.append("第十三条：说明书在发明内容/技术方案部分给出公式，视为手段可复现")

    if not f["util"] and f["c1_refs"] >= T["refs_stack"] and f["c1_asm"] >= T["asm_stack"] \
            and f["c1_param"] < 3 and f["exp"] == 0:
        hits.append("第十二条（结构堆砌）")
        notes.append(f"权1 部件 {f['c1_refs']} 个、装配关系 {f['c1_asm']} 处、无工程参数、无实施例")

    if f["dep_n"] >= T["dep_n"] and f["dep_parts"] >= f["dep_n"] - 1 \
            and f["dep_param"] == 0 and f["exp"] == 0 \
            and f["c1_refs"] >= T["refs_dep"] and f["c1_asm"] >= T["asm_dep"]:
        hits.append("第十一条第一项（部件罗列且从权凑数）")

    if f["c1_struct"] >= 3 and f["exp"] <= T["exp_single"] and f["work"] == 0 \
            and f["param"] < T["param_few"]:
        hits.append("第十一条第二项（仅一个实施例且无工作过程）")

    # 「管理」出现在设备名里（电池管理、热管理）不算事项型，独权有实体结构即排除本款
    if f["mgmt"] >= T["mgmt_n"] and f["var_def"] < 6 and f["form"] == 0 \
            and f["exp"] <= 1 and f["param"] < T["param_few"] \
            and f["c1_struct"] < 3 and f["c1_refs"] < 4:
        hits.append("第十四条（技术问题不落地）")

    return {"hit": bool(hits), "clauses": hits, "notes": notes}


def effect_judge(f: dict) -> dict:
    """第四章：效果判据。放行条件见 thresholds.md T11：引用在先文献、有表、有数、机械参数、实用新型。"""
    if f["util"]:
        return {"hit": False, "clauses": [], "notes": ["实用新型不适用效果判据"]}
    mech = f["c1_struct"] >= 3
    if mech and (f["c1_param"] >= 3 or f["dep_param"] >= 3):
        return {"hit": False, "clauses": [], "notes": ["机械装置类已记载工程参数，不因缺实验数据认定"]}
    # 性能表/测试数值 = 确有验证，放行；仅"引用在先文献"证据弱，降为存疑而非放行
    if f["perf_table"] > 0 or f["test_res"] > 0:
        return {"hit": False, "clauses": [], "notes": ["有验证结构：性能测试表或测试结果数值"]}
    if f["bg_cite"] > 0:
        return {"hit": False, "guarded": True, "clauses": ["第十六条（仅引用在先文献）"],
                "notes": ["背景技术引用了在先文献，但无性能表、无测试数值：证据不足，记存疑"]}
    return {"hit": True, "clauses": ["第十六条（缺少验证结构）"],
            "notes": ["无引用文献、无性能测试表、无测试结果数值"]}


def month_index(s: str) -> int | None:
    try:
        y, m = s[:7].split(".")
        return int(y) * 12 + int(m) - 1
    except Exception:
        return None


HARD_CLAUSES = ("第十二条（结构堆砌）", "第十条（功能性模块）")


def verdict_of(c: dict) -> tuple[str, list[str]]:
    """文本三档：非正常 / 存疑 / 正常。

    硬证据（第十二条、第十条）单独成立即非正常；其余手段条款属软证据，须与第十六条同时成立。
    单一软证据或单一缺验证只记存疑，进第二层用主体事实判决。
    实测见 references/ordering.md §3：硬判只留这两条，精确 94%。
    """
    hints = []
    hits = c["means"]["clauses"]
    hard = any(k in h for k in HARD_CLAUSES for h in hits)
    soft = bool(hits) and not hard
    effect = c["effect"]["hit"]

    if hard:
        if any("第十二条" in h for h in hits):
            hints.append("结构案：核对附图、装配关系与实物，确认后再定")
        return "非正常", hints
    if soft and effect:
        return "非正常", hints
    if c["effect"].get("guarded"):
        return "存疑", ["仅引用在先文献、无验证数据：进第二层"]
    if soft or effect:
        if effect and not soft:
            hints.append("仅缺验证数据：核对是否属于机械结构无参数的情形")
        return "存疑", hints
    if c.get("util"):
        return "存疑", ["实用新型无手段命中：按标准列存疑，进第二层"]
    hints.append("文本无命中，按正常提交；主体未核")
    return "正常", hints


FAM = re.compile(r"(CN\d{6,})([A-Z])")


def dedupe_files(files: list[str]) -> list[str]:
    """同一申请号的公开文本与授权公告只算一件：有授权公告（B）取 B，否则取先出现的一份。"""
    keep: dict = {}
    for p in files:
        m = FAM.search(os.path.basename(p))
        key = m.group(1) if m else p
        if key not in keep or (m and m.group(2) == "B"):
            keep[key] = p
    return sorted(keep.values())


def collect(paths: list[str]) -> list[str]:
    out = []
    for p in paths:
        if os.path.isdir(p):
            out += sorted(glob.glob(os.path.join(p, "**", "*.pdf"), recursive=True))
            out += sorted(glob.glob(os.path.join(p, "**", "*.docx"), recursive=True))
            out += sorted(glob.glob(os.path.join(p, "**", "*.txt"), recursive=True))
        else:
            out.append(p)
    return out


def _meta_fallback(path: str) -> dict:
    """pypdf 会把相邻汉字粘在一起，首页著录项取不到发明人时用 pdfplumber 单独重读第 1 页。"""
    if not path.lower().endswith(".pdf"):
        return {}
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            t = pdf.pages[0].extract_text() or ""
    except Exception:
        return {}
    h = t.replace("（", "(").replace("）", ")")
    out: dict = {}
    m = re.search(r"\(72\)发明(?:人|设计人)\s*([\s\S]{0,260}?)(?:\(74\)|\(51\)|\(54\)|$)", h)
    if m:
        out["inventors"] = _split_inventors(m.group(1))
    m = re.search(r"(?:专利代理师|代理人)\s*([\u4e00-\u9fff]{2,4})", h)
    if m:
        out["agent"] = m.group(1)
    m = re.search(r"\((?:71|73)\)(?:申请人|专利权人)\s*([\s\S]{0,200}?)(?:\(72\)|\(74\)|\(51\)|$)", h)
    if m:
        out["applicant"] = re.sub(r"\s+", "", m.group(1))
    return out


def _case_one(p: str) -> dict:
    """单件：抽正文 → 特征 → 判据 → 三档。进程池里跑，返回值必须可 pickle。"""
    f = features(read_text(p))
    if not f["inventors"] or not f["applicant"]:
        for k, v in _meta_fallback(p).items():
            if not f.get(k) and v:
                f[k] = v
    f["file"] = os.path.basename(p)
    f["pub"] = re.sub(r"\.(pdf|txt|docx)$", "", f["file"])
    if p.lower().endswith(".docx") and (not f["title"] or not f["applicant"]):
        # 常州分所稿件命名：申请人+专利类型+发明名称.docx
        stem = os.path.splitext(f["file"])[0]
        parts = [x.strip() for x in stem.split("+") if x.strip()]
        if len(parts) >= 3:
            f["applicant"] = f["applicant"] or parts[0]
            f["title"] = f["title"] or parts[-1]
            if "实用新型" in parts[1]:
                f["util"] = True
            elif "发明" in parts[1]:
                f["util"] = False
    f["means"] = means_judge(f)
    f["effect"] = effect_judge(f)
    v, hints = verdict_of(f)
    if f["nchar"] < 2000:
        hints.append("抽取不完整：正文不足 2000 字，结论不可信")
    if not f["title"] or not f["applicant"]:
        hints.append("首页著录项未取到：标题或申请人缺")
    if not f["inventors"]:
        hints.append("发明人未取到：PDF 著录项与清单都没有")
    return {
        "file": f["file"], "pub": f["pub"], "title": f["title"], "applicant": f["applicant"],
        "agency": f["agency"], "agent": f["agent"], "inventors": f["inventors"],
        "app_date": f["app_date"], "util": f["util"],
        "refs": f["c1_refs"], "exp": f["exp"],
        "means": f["means"], "effect": f["effect"],
        "verdict": v, "hints": hints,
    }


def _run(files: list[str], workers: int) -> list[dict]:
    """逐件跑；workers>1 时用进程池（PDF 抽取是 CPU 瓶颈，实测 8 进程约 4.8x）。"""
    if workers <= 1:
        return [_case_one(p) for p in files]
    import multiprocessing as mp
    with mp.Pool(workers) as pool:
        return pool.map(_case_one, files, chunksize=4)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--json", help="把逐案结果写入该文件")
    ap.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1),
                    help="进程数，默认 min(8, CPU)；1 = 串行")
    args = ap.parse_args()

    files = dedupe_files(collect(args.paths))
    if not files:
        print("没有找到 PDF 或 txt", file=sys.stderr)
        return 2

    rows = []
    summary = {"非正常": 0, "存疑": 0, "正常": 0}
    for r in _run(files, args.workers):
        summary[r["verdict"]] += 1
        rows.append(r)
        print(json.dumps(r, ensure_ascii=False))

    print(json.dumps({"summary": summary, "total": len(rows)}, ensure_ascii=False))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fp:
            json.dump(rows, fp, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
