#!/usr/bin/env python3
"""v2 取数层：incoPat 浏览器 / 企查查 / 香橼树 + 缓存。

原则：先查缓存；没命中才取数；派生结论不缓存（每次现算）。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import time

import bsk_helper as B
import inco_client as INCO

CACHE = os.environ.get("PATENT_CACHE_DIR", "cache")
CACHE_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache.py")


# ---------------------------------------------------------------- 缓存（文件式，支持新命名空间）

def _ns_path(ns: str) -> str:
    os.makedirs(CACHE, exist_ok=True)
    return os.path.join(CACHE, ns + ".json")


def _load(ns: str) -> dict:
    p = _ns_path(ns)
    if not os.path.exists(p):
        return {}
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}


def _save(ns: str, data: dict):
    p = _ns_path(ns)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, p)


def _age_days(rec: dict) -> float:
    stamp = (rec or {}).get("_fetched") or ""
    try:
        d = dt.date.fromisoformat(stamp[:10])
        return (dt.date.today() - d).days
    except Exception:
        return 1e9


def cache_get(ns: str, key: str, max_age: int = 90):
    rec = _load(ns).get(key)
    if not rec:
        return None
    return rec if _age_days(rec) <= max_age else None


def cache_set(ns: str, key: str, obj: dict):
    data = _load(ns)
    data[key] = obj
    _save(ns, data)


# ---------------------------------------------------------------- 发明人记录（12 个月）

def window(anchor: str | dt.date | None = None) -> tuple[str, str]:
    """窗口 = 锚点往前 12 个月。锚点默认"="本案申请日"，没有申请日则用查询日。"""
    if isinstance(anchor, str) and anchor:
        d = dt.date.fromisoformat(anchor[:10].replace("/", "-")) if "-" in anchor[:10] else \
            dt.date(int(anchor[:4]), int(anchor[4:6]), int(anchor[6:8]))
    elif isinstance(anchor, dt.date):
        d = anchor
    else:
        d = dt.date.today()
    start = d - dt.timedelta(days=365)
    return start.strftime("%Y%m%d"), d.strftime("%Y%m%d")


def inventor_records(name: str, anchor: str | dt.date | None = None,
                     co_inventors: list[str] | None = None) -> dict:
    """滚动 12 个月（锚=本案申请日）、不论位次、按申请号去重。

    剔重名所需的共同发明人：incoPat 结果行不含发明人名单，改用反查——
    对本案其余发明人各检索一次，与候选记录按申请号求交集，
    交集内的记录标记 co_inventors（见 rules/20-inventor.md §2.2）。
    """
    s, e = window(anchor)
    others = [x for x in (co_inventors or []) if x and x != name]
    key = f"{name}|{s}|{e}|{'/'.join(sorted(others))}"
    hit = cache_get("inventor", key, max_age=7)
    if hit:
        return hit

    raw = INCO.inventor_patents(name, s, e)
    recs = raw.get("records") or []
    total = raw.get("total_raw") or 0

    # 反查共同发明人：{其他发明人名: 申请号集合}
    co_map = {}
    errors = list(raw.get("errors") or [])
    for other in others:
        rr = INCO.inventor_patents(other, s, e)
        if rr.get("errors"):
            errors.extend(rr["errors"])
            continue
        co_map[other] = {x.get("app_no") for x in (rr.get("records") or []) if x.get("app_no")}

    for r in recs:
        hits = [o for o, nos in co_map.items() if r.get("app_no") in nos]
        r["inventors"] = hits
        r["co_inventors"] = hits

    out = {"inventor": name, "expr": raw.get("expr"), "window_start": s, "window_end": e,
           "anchor": e, "total_raw": total, "stat": raw.get("stat") or {},
           "records": recs, "co_lookup": {k: len(v) for k, v in co_map.items()},
           "common_name": total > 1000, "errors": errors,
           "_fetched": dt.date.today().isoformat(), "_source": "incoPat(bsk)"}
    if recs or total == 0:
        cache_set("inventor", key, out)
    return out


# ---------------------------------------------------------------- 1.1 授权发明专利

def granted_inventions(app: str, base_date: str | None = None) -> dict:
    """到 base_date 为止，申请人是否有授权发明专利（结果页「发明授权」统计）。"""
    base = (base_date or dt.date.today().strftime("%Y%m%d")).replace("-", "")
    key = f"{app}|{base}"
    hit = cache_get("company_rd", key, max_age=90)
    if hit:
        return hit
    r = INCO.granted_invention(app, base)
    out = {"applicant": app, "expr": r.get("expr"), "granted_invention": r.get("granted_invention"),
           "base_date": base, "has": r.get("has"), "total": r.get("total"),
           "stat": r.get("stat") or {}, "errors": [r["error"]] if r.get("error") else [],
           "_fetched": dt.date.today().isoformat(), "_source": "incoPat(bsk)"}
    if out["has"] is not None:
        cache_set("company_rd", key, out)
    return out


# ---------------------------------------------------------------- 企业资质（专精特新）

def honor(app: str) -> dict:
    hit = cache_get("company_honor", app, max_age=90)
    if hit:
        return hit
    p = subprocess.run(["qcc", "operation", "get_honor_info", app, "--json"],
                       capture_output=True, text=True, timeout=180)
    raw = p.stdout or ""
    items = []
    try:
        j = json.loads(raw[raw.index("{"):])
        items = j.get("荣誉信息") or []
    except Exception:
        items = []
    z = []
    for it in items:
        nm = (it.get("名称") or "")
        if "专精特新" in nm:
            z.append({"name": nm, "level": it.get("级别") or "", "year": it.get("认证年份") or ""})
    out = {"applicant": app, "honors": items, "zhuanjingtexin": z,
           "_fetched": dt.date.today().isoformat(), "_source": "qcc.get_honor_info",
           "_ok": bool(items)}
    if items:
        cache_set("company_honor", app, out)
    return out


def waiver_from(honor_rec: dict) -> dict | None:
    """只认小巨人（国家级）+ 省级专精特新；创新型中小企业不算。"""
    for z in honor_rec.get("zhuanjingtexin") or []:
        nm, lv = z["name"], z["level"]
        if "小巨人" in nm and "国家级" in lv:
            return {"level": "国家级专精特新小巨人", "year": z["year"], "name": nm}
        if "专精特新" in nm and "省级" in lv and "小巨人" not in nm:
            return {"level": "省级专精特新中小企业", "year": z["year"], "name": nm}
    return None


# ---------------------------------------------------------------- 工商（关联判定用）

def company_reg(app: str) -> dict:
    hit = cache_get("company", app, max_age=90)
    if hit and (hit.get("legal_rep") or hit.get("address")):
        return {"applicant": app, "legal_rep": hit.get("legal_rep") or "",
                "address": hit.get("address") or "", "insured": hit.get("insured"),
                "scope": hit.get("scope") or "", "industry": hit.get("industry") or "",
                "_fetched": hit.get("_fetched", "")}
    base = hit or {}
    p = subprocess.run(["qcc", "company", "get_company_registration_info", app, "--json"],
                       capture_output=True, text=True, timeout=180)
    raw = p.stdout or ""
    try:
        j = json.loads(raw[raw.index("{"):])
    except Exception:
        j = {}
    out = {"applicant": app,
           "legal_rep": j.get("法定代表人") or base.get("legal_rep") or "",
           "address": j.get("注册地址") or base.get("address") or "",
           "insured": int(re.sub(r"\D", "", str(j.get("参保人数") or base.get("insured") or "0")) or 0),
           "scope": j.get("经营范围") or "",
           "industry": (j.get("国标行业") or {}).get("小类") or (j.get("国标行业") or {}).get("中类") or ""}
    cache_set("company", app, out)
    return out


# ---------------------------------------------------------------- 代理师滚动 12 个月

def agent_rolling(name: str, anchor: str | dt.date | None = None) -> dict:
    """代理量窗口同样锚本案申请日（往前 12 个月）。"""
    if isinstance(anchor, str) and anchor:
        today = dt.date.fromisoformat(anchor[:10]) if "-" in anchor[:10] else \
            dt.date(int(anchor[:4]), int(anchor[4:6]), int(anchor[6:8]))
    elif isinstance(anchor, dt.date):
        today = anchor
    else:
        today = dt.date.today()
    key = f"{name}|{today.isoformat()}"
    hit = cache_get("agent_month", key, max_age=1)
    if hit:
        return hit
    years = sorted({today.year, (today - dt.timedelta(days=365)).year})
    data = B.agent_months([(name, str(y)) for y in years])
    months = {}
    for y in years:
        row = data.get(f"{name}|{y}")
        if row is None:
            continue
        try:
            months[y] = B.parse_month_row(row)
        except Exception:
            months[y] = []
    if not months:
        out = {"agent": name, "status": "not_found", "value": None,
               "as_of": today.isoformat(), "_fetched": today.isoformat()}
        cache_set("agent_month", key, out)
        return out

    prev = today - dt.timedelta(days=365)
    total, detail = 0, []
    for y in years:
        m = months.get(y) or []
        for idx, v in enumerate(m, start=1):
            month_date = dt.date(y, idx, 1)
            if prev <= month_date <= today:
                total += v
                detail.append({"ym": f"{y}-{idx:02d}", "n": v})
    out = {"agent": name, "status": "ok", "value": total, "detail": detail,
           "window": [prev.isoformat(), today.isoformat()],
           "as_of": today.isoformat(), "_fetched": today.isoformat(), "_source": "香橼树月度统计"}
    cache_set("agent_month", key, out)
    return out


# ---------------------------------------------------------------- ③ 创新度：对比文件检索（v2.1）

def _norm_date(x) -> str:
    """incoPat 给 YYYYMMDD，统一成 YYYY-MM-DD，便于与本案申请日比较。"""
    s = str(x or "").strip()[:10]
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _pub_date(r: dict) -> str:
    return _norm_date(r.get("pub_date") or r.get("app_date") or
                      r.get("publication_date") or r.get("application_date"))


def prior_art(queries: list[str], semantic: str | None = None,
              before: str | None = None, limit: int = 8,
              keywords: list[str] | None = None) -> dict:
    """③ 创新度取数：布尔检索 + 语义检索两路，合并去重，只留早于本案的文件。

    queries  : 布尔检索式列表（incoPat 语法，见 rules/50 §3b）
    semantic : 语义检索输入（独权原文 + 创新点清单）
    before   : 本案申请日（YYYY-MM-DD）；无申请日传查询日
    """
    import hashlib
    before = (before or dt.date.today().isoformat())[:10]
    bkey = before.replace("-", "")
    sig = hashlib.md5(json.dumps([sorted(queries), semantic, bkey], ensure_ascii=False)
                      .encode()).hexdigest()
    hit = cache_get("prior_art", sig, max_age=7)
    if hit:
        if "all_candidates" in hit:                       # 缓存里存全量，返回时再排序截断
            return _rank_prior(hit, keywords, limit)
        return hit

    cands, routes, errors = {}, [], []

    def _add(rec: dict, route: str, score=None):
        p = rec.get("pub") or rec.get("app_no")
        if not p:
            return
        e = cands.setdefault(p, {
            "pub": rec.get("pub"), "app_no": rec.get("app_no"),
            "app_date": rec.get("app_date"), "pub_date": rec.get("pub_date"),
            "title": rec.get("title"), "ipc": rec.get("ipc"),
            "applicants": rec.get("applicants") or [], "_route": []})
        if route not in e["_route"]:
            e["_route"].append(route)
        if score is not None:
            e["score"] = max(e.get("score") or 0, score)

    for q in queries:
        r = INCO.search(q)
        if r.get("error"):
            errors.append(f"布尔 {q}: {r['error']}")
            continue
        routes.append(f"布尔 {q}（{r.get('count')} 篇）")
        for x in r.get("rows") or []:
            _add(x, "布尔")

    if semantic:
        r = INCO.semantic(semantic)
        if r.get("error"):
            errors.append(f"语义: {r['error']}")
        else:
            routes.append(f"语义（{r.get('count')} 篇，带相关度）")
            for x in r.get("rows") or []:
                _add(x, "语义", x.get("score"))

    # 只看早于本案的文件（申请日或公开日早于 before）
    keep = [c for c in cands.values()
            if (_norm_date(c.get("app_date")) and _norm_date(c.get("app_date")) < before)
            or (_norm_date(c.get("pub_date")) and _norm_date(c.get("pub_date")) < before)]

    out = {"before": before, "queries": queries, "semantic": bool(semantic),
           "routes": routes, "all_candidates": keep, "candidates": keep[:limit],
           "candidates_total": len(keep), "errors": errors,
           "_fetched": dt.date.today().isoformat(), "_source": "incoPat(bsk)"}
    if routes:  # 至少有一路成功才缓存
        cache_set("prior_art", sig, out)
    return _rank_prior(out, keywords, limit)


def _rank_prior(out: dict, keywords: list[str] | None, limit: int) -> dict:
    """排序：语义相关度 > 标题命中领域关键词数 > 公开日早的靠前。"""
    kws = [k for k in (keywords or []) if k]
    scored = []
    for c in out.get("all_candidates") or out.get("candidates") or []:
        t = c.get("title") or ""
        sem = c.get("score") or 0
        score = sem + 2 * sum(1 for k in kws if k in t)
        scored.append((score, _pub_date(c), c))
    scored.sort(key=lambda x: (-x[0], x[1] or "9999"))
    res = dict(out)
    res["candidates"] = [c for _, _, c in scored[:limit]]
    res["ranked_by"] = "语义相关度>标题关键词>公开日"
    return res


def prior_claims(pubs: list[str]) -> dict:
    """取对比文件的权项文本。

    incoPat 结果行默认不含权利要求，需先在结果页「显示字段」勾选
    「首项权利要求(中文)」；勾选后列表行会带该字段，这里按公开号回填。
    取不到时返回 missing，由规则层标 pending。
    """
    pubs = [p for p in dict.fromkeys(pubs) if p]
    if not pubs:
        return {"claims": {}, "errors": ["no pubs"]}
    cache_all = _load("prior_claims")
    need = [p for p in pubs if p not in cache_all]
    if need:
        for pub in need[:20]:
            r = INCO.claims_text(pub)
            txt = (r.get("claims") or "").strip()
            if not txt:
                continue
            cache_all[pub] = {"pub": pub, "claims_text": txt,
                              "_fetched": dt.date.today().isoformat(),
                              "_source": "incoPat(bsk)"}
        _save("prior_claims", cache_all)
    got = {p: cache_all[p] for p in pubs if p in cache_all}
    return {"claims": got, "missing": [p for p in pubs if p not in cache_all]}
