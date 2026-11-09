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
}

# 常见名不作身份锚（rules/20 §2.2）
COMMON_NAMES = {
    "张伟", "王伟", "王芳", "李娜", "刘洋", "李伟", "王静", "张敏", "李静", "王秀英",
    "张丽", "李秀英", "刘伟", "王强", "张杰", "李强", "王勇", "张军", "李军", "王杰",
    "张勇", "李勇", "刘杰", "王涛", "王刚", "张磊", "李磊", "王鹏", "刘洋", "陈静",
}

# 企业类维度：可被专精特新豁免
WAIVABLE = ["①类型", "①科技属性", "①社保", "①主营匹配", "研发能力"]

DIMS = [
    "①类型", "①科技属性", "①社保", "①主营匹配", "研发能力",
    "发明人件数", "⑤b一致性", "跨机构", "②代理量",
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
    rec_inv = [strip_id(x) for x in (rec.get("inventors") or [])]
    others = {strip_id(x) for x in (case.get("inventors") or [])} - {strip_id(case.get("first_inventor") or "")}
    overlap = {x for x in rec_inv if x in others}

    # 申请人相同 + 领域一致 → 铁定同一人（第二锚）
    same_app = bool({norm_name(a) for a in (rec.get("applicants") or [])}
                    & {norm_name(case.get("applicant") or "")})
    same_ipc = same_field(rec.get("ipc"), case.get("ipc"))

    # 外观设计与实用新型/发明不可能是同一件申请 → 直接剔除
    pub = str(rec.get("application_number") or "")
    if (not ipc_code(rec.get("ipc")) and rec.get("is_design")) or re.search(r"S$", str(rec.get("publication_number") or "")):
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
        item = {"app_no": r.get("application_number"), "title": (r.get("title") or "")[:40],
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
