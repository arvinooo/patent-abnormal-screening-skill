#!/usr/bin/env python3
"""v2 判定规则（纯逻辑，不联网）。

只要有人把"案件"和"已取到的外部数据"喂进来，这里输出逐维结论。
阈值与 rules/00-thresholds.md 一一对应；改阈值两处一起改。
"""
from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------- 阈值（对齐 rules/00-thresholds.md）
T = {
    "social_fail": 3,          # T01 社保 <3 不过
    "social_grey": 10,         # 3–10 灰区，≥10 过
    "inventor_min": 5,         # T03 有效件数 ≥5 不过
    "cross_org_min": 3,        # T04 关联聚组 ≥3 组不过
    "agent_fail": 300,         # T05 代理量 ≥300 不过
    "window_days": 365,        # 滚动窗口
    "ipc_same": 4,             # 一致：IPC 主分类前 4 位
    "ipc_similar": 3,          # 相似：前 3 位
    # ---- v2.1 ③ 创新度（对应 rules/00 T08 / T10–T12）
    "novelty_fail": 0.80,      # T10 单篇覆盖度 ≥80% 且差异为常见替换/公知常识 → 不过
    "novelty_full": 1.00,      # T11 单篇 100% → 不过
    "combo_full": 1.00,        # T12 ≤2 篇合并 100% 且无协同 → 不过
    "algo_ratio": 0.80,        # T08 计算机算法：计算类特征占比 ≥80%
}

# 差异定性（rules/50 §3b 第 3 步）
DIFF_SIMPLE = {"常见替换", "公知常识"}          # 简单替换 / 公知常识
DIFF_SUBSTANTIVE = {"实质改进", "协同"}          # 有实质差异

# 常见名不作身份锚（rules/20 §2.2）
COMMON_NAMES = {
    "张伟", "王伟", "王芳", "李娜", "刘洋", "李伟", "王静", "张敏", "李静", "王秀英",
    "张丽", "李秀英", "刘伟", "王强", "张杰", "李强", "王勇", "张军", "李军", "王杰",
    "张勇", "李勇", "刘杰", "王涛", "王刚", "张磊", "李磊", "王鹏", "刘洋", "陈静",
}

# 企业类维度：可被专精特新豁免
WAIVABLE = ["①类型", "①科技属性", "①社保", "①主营匹配", "授权发明专利"]

DIMS = [
    "①类型", "①科技属性", "①社保", "①主营匹配", "授权发明专利",
    "发明人件数", "跨机构", "②代理量",
    "③技术领域", "④撰写", "⑥多件相似", "违法公德",
]

# ---------------------------------------------------------------- 文本小工具


def strip_id(name: str) -> str:
    """明细表里发明人常带身份证号：'林美华150428199609244921' -> '林美华'"""
    return re.sub(r"\d{6,}.*$", "", (name or "").strip())


def mask_id(raw: str) -> str:
    """报告里展示：林美华150428********4921"""
    m = re.match(r"^([^\d]+)(\d{6})(\d+)(\d{4})$", (raw or "").strip())
    return f"{m.group(1)}{m.group(2)}********{m.group(4)}" if m else strip_id(raw)


def norm_name(s: str) -> str:
    """公司名归一化：去空格、全角转半角、去括号差异，用于关联比对"""
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"[\s（）()【】\[\]·、,，.。]", "", s)
    return s


def ipc_code(ipc) -> str:
    if not ipc:
        return ""
    if isinstance(ipc, (list, tuple)):
        ipc = ipc[0] if ipc else ""
    return str(ipc).split()[0].split(",")[0].strip().upper()


def same_field(a: str, b: str) -> bool:
    """领域一致/相似（rules/20 §2.3）"""
    a, b = ipc_code(a), ipc_code(b)
    if not a or not b:
        return False
    return a[: T["ipc_same"]] == b[: T["ipc_same"]] or a[: T["ipc_similar"]] == b[: T["ipc_similar"]]


# ---------------------------------------------------------------- 1.2 剔重名


def decide_identity(rec: dict, case: dict) -> tuple[str, str]:
    """判一条记录是不是本案第一发明人本人。

    case 需要：applicant, inventors(本案全部发明人，含第一), ipc(本案)
    rec  需要：inventors, applicants, ipc, application_number

    返回 (keep|drop|pending, 理由)
    """
    # 记录侧的发明人名单：取数写的是 inventors，跑批落盘/手工缓存常写 co_inventors
    rec_inv = [strip_id(x) for x in (rec.get("inventors") or rec.get("co_inventors") or [])]
    others = {strip_id(x) for x in (case.get("inventors") or [])} - {strip_id(case.get("first_inventor") or "")}
    overlap = {x for x in rec_inv if x in others}

    # 申请人相同 + 领域一致 → 铁定同一人（第二锚）
    same_app = bool({norm_name(a) for a in (rec.get("applicants") or [])}
                    & {norm_name(case.get("applicant") or "")})
    same_ipc = same_field(rec.get("ipc"), case.get("ipc"))

    # 外观设计与实用新型/发明不可能是同一件申请 → 直接剔除
    pub_no = str(rec.get("pub") or rec.get("publication_number") or "")
    if (not ipc_code(rec.get("ipc")) and rec.get("is_design")) or re.search(r"S$", pub_no):
        return "drop", "外观设计，与本案不同类型"

    if overlap and not (overlap & COMMON_NAMES):
        return "keep", f"共同发明人重合（{'、'.join(sorted(overlap))}）"

    if same_app and same_ipc:
        return "keep", "同申请人且同领域"

    if not case.get("ipc") or not rec.get("ipc"):
        return "pending", "缺 IPC，领域无法比对"

    if same_ipc:
        if same_app:
            return "keep", "同申请人且同领域"
        return "pending", "领域相同但申请人不同，可能同行业同名他人"

    return "drop", f"领域不同（{ipc_code(rec.get('ipc'))} vs 本案 {ipc_code(case.get('ipc'))}）"


def dedupe(records: list[dict], case: dict) -> dict:
    """返回 {kept, dropped, pending, detail}"""
    kept, dropped, pend = [], [], []
    for r in records:
        st, why = decide_identity(r, case)
        item = {"app_no": r.get("app_no") or r.get("application_number"),
                "title": (r.get("title") or "")[:40],
                "applicants": r.get("applicants") or [], "ipc": ipc_code(r.get("ipc")),
                "reason": why}
        (kept if st == "keep" else dropped if st == "drop" else pend).append(item)
    return {"kept": kept, "dropped": dropped, "pending": pend}


# ---------------------------------------------------------------- 2.1 跨机构聚组


def relation(a: str, b: str, info: dict) -> str:
    """判两家申请人的关联，返回依据字符串；无关联返回 ''。info: 名称 -> {legal_rep, address}"""
    if norm_name(a) == norm_name(b):
        return "同一主体"
    na, nb = norm_name(a), norm_name(b)
    if len(na) >= 6 and (na.startswith(nb) or nb.startswith(na)):
        return "名称互相包含"
    if len(na) >= 6 and len(nb) >= 6 and na[:6] == nb[:6]:
        return "≥6 字共同前缀"
    ia, ib = info.get(a) or {}, info.get(b) or {}
    if ia.get("legal_rep") and ia.get("legal_rep") == ib.get("legal_rep"):
        return f"同法定代表人（{ia['legal_rep']}）"
    if ia.get("address") and norm_name(ia["address"]) == norm_name(ib.get("address") or ""):
        return "同注册地址"
    return ""


def group_applicants(apps: list[str], info: dict) -> dict:
    """并查集聚组，返回 {groups: [[..]], edges: [(a,b,依据)]}"""
    apps = list(dict.fromkeys(apps))
    parent = {a: a for a in apps}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    edges = []
    for i, a in enumerate(apps):
        for b in apps[i + 1:]:
            why = relation(a, b, info)
            if why:
                edges.append((a, b, why))
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[rb] = ra
    groups = {}
    for a in apps:
        groups.setdefault(find(a), []).append(a)
    return {"groups": list(groups.values()), "edges": edges}


# ---------------------------------------------------------------- 结论汇总


def summarize(dims: dict, waiver: dict | None) -> tuple[str, list[str]]:
    """dims: {维度: pass|fail|pending|na|waived}；waiver 非空时不否决 WAIVABLE 里的 fail。

    返回 (结论, 命中项列表)
    """
    fail, pending = [], []
    for d, v in dims.items():
        if v == "fail":
            if waiver and d in WAIVABLE:
                dims[d] = "waived"
            else:
                fail.append(d)
        elif v == "pending":
            pending.append(d)
    if fail:
        return "非正常", fail
    if pending:
        return "待核查", []
    return "正常", []


# ---------------------------------------------------------------- v2.1 ③ 创新度


CATEGORY_STATUS = {
    "生活类": "fail",          # T08 红线
    "计算机算法": "fail",       # T08 红线
    "机械装置": "novelty",      # 转 T10–T12
    "方法步骤图的系统": "novelty",  # 转 T10–T12
    "非四类": "pass",           # 直接过，不看创新度
}


def category_of(flags: dict) -> tuple[str, str]:
    """③ §3a 归四类。flags 由调用方读权1+技术领域段后填：

    生活类：consumer / industrial_use / only_form / ipc_consumer
    计算机算法：method_only / calc_ratio / has_hardware / pure_calc_effect
    机械装置：mechanical
    方法步骤图：step_chart
    """
    if (flags.get("consumer") and not flags.get("industrial_use")
            and flags.get("only_form") and flags.get("ipc_consumer")):
        return "生活类", "消费品+非生产场景+仅形态便利改进+消费类IPC"
    if (flags.get("method_only") and (flags.get("calc_ratio") or 0) >= T["algo_ratio"]
            and not flags.get("has_hardware") and flags.get("pure_calc_effect")):
        return "计算机算法", (f"纯方法/模型、计算类特征 {flags.get('calc_ratio'):.0%}、"
                            "无硬件执行体、效果为纯计算指标")
    if flags.get("mechanical"):
        return "机械装置", "方案主体为机械结构/装置，创新点在部件结构与连接关系"
    if flags.get("step_chart"):
        return "方法步骤图的系统", "权1 为流程步骤序列，各步无装置/工艺参数支撑"
    return "非四类", ""


def tech_dim(category: str, novelty_result: tuple[str, str] | None = None) -> tuple[str, str]:
    """③ §3b 分流：非四类直接过；生活类/算法不过；机械装置/方法步骤图看 novelty() 结果。"""
    st = CATEGORY_STATUS.get(category, "pass")
    if st == "pass":
        return "pass", f"归「{category}」，按规则不看创新度"
    if st == "fail":
        return "fail", f"归「{category}」→ T08 红线，一票不过"
    if not novelty_result:
        return "pending", f"归「{category}」，需跑 3c 创新度（检索未完成）"
    return novelty_result


def hard_category(flags: dict) -> tuple[str, str]:
    """兼容旧调用：只回答红线两类是否命中（不再含机械装置/方法步骤图）。"""
    cat, why = category_of(flags)
    if CATEGORY_STATUS.get(cat) == "fail":
        return "fail", f"{cat}（{why}）"
    return "pass",


def novelty(coverage: float, diff: str, combined: float | None = None,
            refs: int = 1, self_repeat: bool = False,
            closest: str = "") -> tuple[str, str]:
    """③ §3b 第 4 步。coverage=单篇覆盖度(0-1)；diff=常见替换/公知常识/实质改进/协同；
    combined ≤2 篇合并覆盖度；self_repeat=最接近文件是本案申请人的在先申请。"""
    cov = float(coverage or 0)
    whom = f"最接近 {closest}，" if closest else ""
    if cov >= T["novelty_full"]:
        return "fail", f"{whom}单篇覆盖度 {cov:.0%}（100%，X 类全文命中）"
    if self_repeat and cov >= T["novelty_fail"]:
        return "fail", f"{whom}本案申请人的在先申请，覆盖度 {cov:.0%} → 自我重复申请"
    if cov >= T["novelty_fail"] and diff in DIFF_SIMPLE:
        return "fail", (f"{whom}单篇覆盖度 {cov:.0%}（≥80%），剩余差异属「{diff}」→ 创新度不足")
    if (combined is not None and combined >= T["combo_full"] and refs <= 2
            and diff != "协同"):
        return "fail", (f"{refs} 篇合并覆盖 {combined:.0%}（100%），无协同 → 要素简单拼凑")
    return "pass", (f"{whom}单篇覆盖度 {cov:.0%}，差异属「{diff or '未定性'}」"
                    + (f"，合并 {combined:.0%}" if combined is not None else "")
                    + " → 创新度够")


def coverage_table(features: list[str], refs: dict) -> dict:
    """生成逐特征对照表：features=F1..Fn；refs={公开号: {对应特征下标或 F 编号}}。

    返回 {pub: {"k": n, "N": N, "ratio": float, "hit": [...], "miss": [...]}} 与合并覆盖。
    """
    N = len(features) or 1
    per, union = {}, set()
    for pub, hit in (refs or {}).items():
        hs = {str(x) for x in (hit or [])}
        hit_f = [f for f in features if f in hs]
        union |= set(hit_f)
        per[pub] = {"k": len(hit_f), "N": N, "ratio": len(hit_f) / N,
                    "hit": hit_f, "miss": [f for f in features if f not in hs]}
    return {"per": per, "combined": len(union) / N, "combined_hit": sorted(union)}
