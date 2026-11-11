#!/usr/bin/env python3
"""单案判定：把 rules/ 的逻辑串起来，输出 dims + 结论 + 证据。

文本维（③④⑥违法）不在本文件算，由调用方传入。
"""
from __future__ import annotations

import datetime as dt

import v2_fetch as F
import v2_rules as R


def judge(case: dict, text_dims: dict | None = None, verbose: bool = False) -> dict:
    """
    case:
      title, applicant, first_inventor, inventors[list], agent,
      ipc(可空), app_date(可空 'YYYY-MM-DD'), main_match('pass'|'fail'|'pending'),
      type_check / tech_attr 可空（默认沿用 text_dims 或按名称启发式）
    """
    text_dims = text_dims or {}
    ev = {}
    dims = {}

    # ---------- 第 1 步：前置资质（豁免）
    h = F.honor(case["applicant"])
    w = F.waiver_from(h)
    if w:
        ev["豁免依据"] = f"{w['level']}（{w['year']} 年认定）"
    elif not h.get("_ok"):
        ev["豁免依据"] = "资质未取到 → 企业类维度按正常判"

    # ---------- 企业类（①四项）
    reg = F.company_reg(case["applicant"])
    dims["①类型"] = case.get("type_check") or text_dims.get("①类型") or "pending"
    dims["①科技属性"] = case.get("tech_attr") or text_dims.get("①科技属性") or "pending"
    insured = F._int(reg.get("insured"))
    dims["①社保"] = "fail" if insured < R.T["social_fail"] else "pass"
    dims["①主营匹配"] = case.get("main_match") or text_dims.get("①主营匹配") or "pending"
    ev["①社保"] = f"参保 {insured} 人（企查查 {reg.get('_fetched') or ''}）"

    # ---------- 第 4 步：⑤ 发明人件数（剔重名 → 计数）
    anchor = case.get("app_date") or None          # 窗口锚点 = 本案申请日（无则查询日）
    inv = F.inventor_records(case["first_inventor"], anchor,
                             co_inventors=case.get("inventors") or [])
    dd = R.dedupe(inv.get("records") or [], case)
    kept, dropped, pend = dd["kept"], dd["dropped"], dd["pending"]
    N = len(kept)
    ev["计数"] = (f"名口径 {len(inv.get('records') or [])} 件 → 剔除重名 {len(dropped)} 件"
                  f"{'、待核查 ' + str(len(pend)) + ' 件' if pend else ''} → 有效 {N} 件")
    if dropped:
        ev["被剔除"] = [f"{x['app_no']} {x['title']}（{x['reason']}）" for x in dropped][:8]

    # 件数只判件数，不掺申请人的授权发明专利
    if inv.get("common_name"):
        dims["发明人件数"] = "pending"
        ev["计数"] = f"常见名（名下 {inv.get('total_raw')} 组），计数不可采信"
    elif pend and N == 0:
        dims["发明人件数"] = "pending"
    elif N >= R.T["inventor_min"]:
        dims["发明人件数"] = "fail"
    else:
        dims["发明人件数"] = "pass"

    # ---------- 1.1 授权发明专利（申请人维度独立一条，每案都判）
    base = (case.get("app_date") or "").replace("-", "") or dt.date.today().strftime("%Y%m%d")
    rd = F.granted_inventions(case["applicant"], base)
    dims["授权发明专利"] = "pass" if rd.get("has") else ("fail" if rd.get("has") is False else "pending")
    ev["授权发明专利"] = (f"到基准日 {base} 授权发明专利 {rd.get('granted_invention')} 件"
                      f"（基准：{'本案申请日' if case.get('app_date') else '查询日（本案无申请日）'}）")

    # ---------- 第 6 步：2.1 跨机构（复用 kept 集合）
    apps = [case["applicant"]]
    for x in kept:
        apps.extend(x.get("applicants") or [])
    # 同名不同写法（全角/半角括号）按归一化名合并，保留先出现的写法
    _uniq = {}
    for a in apps:
        _uniq.setdefault(R.norm_name(a), a)
    apps = list(_uniq.values())
    if inv.get("common_name"):
        dims["跨机构"] = "pending"
        ev["跨机构"] = f"常见名（名下 {inv.get('total_raw')} 组），机构数不可采信 → 待核查"
    info = {}
    for a in apps:
        if a == case["applicant"]:
            info[a] = {"legal_rep": reg.get("legal_rep") or "", "address": reg.get("address") or ""}
        else:
            rr = F.company_reg(a)
            info[a] = {"legal_rep": rr.get("legal_rep") or "", "address": rr.get("address") or ""}
    co_sets = [x.get("applicants") or [] for x in kept if len(x.get("applicants") or []) > 1]
    grp = R.group_applicants(apps, info, co_sets) if len(apps) > 1 else {"groups": [apps] if apps else [], "edges": []}
    if dims.get("跨机构") == "pending":
        pass
    elif len(apps) <= 1:
        dims["跨机构"] = "pass"
    else:
        dims["跨机构"] = "fail" if len(grp["groups"]) >= R.T["cross_org_min"] else "pass"
    ev["跨机构"] = {"申请人数": len(apps), "组数": len(grp["groups"]),
                    "分组": grp["groups"],
                    "关联依据": [f"{a}↔{b}：{why}" for a, b, why in grp["edges"]]}

    # ---------- 第 7 步：② 代理师
    if not case.get("agent"):
        dims["②代理量"] = "pending"
        ev["②代理量"] = "未提供代理师"
    else:
        ag = F.agent_rolling(case["agent"], anchor)
        if ag.get("status") != "ok":
            dims["②代理量"] = "pending"
            ev["②代理量"] = "该所名录查无此人 → 待核查"
        else:
            dims["②代理量"] = "fail" if ag["value"] >= R.T["agent_fail"] else "pass"
            ev["②代理量"] = (f"本所口径，滚动 12 个月（{ag['window'][0]} ~ {ag['window'][1]}），"
                             f"代理量 {ag['value']} 件")

    # ---------- 文本维（原样带入）
    for d in ("③技术领域", "④撰写", "⑥多件相似", "违法公德"):
        dims[d] = text_dims.get(d) or "pending"

    conclusion, hits = R.summarize(dims, w)
    return {"dims": dims, "conclusion": conclusion, "hits": hits,
            "waiver": w, "evidence": ev,
            "detail": {"kept": kept, "dropped": dropped, "pending": pend,
                       "records_total": len(inv.get("records") or [])}}
