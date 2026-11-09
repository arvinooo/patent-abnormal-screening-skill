#!/usr/bin/env python3
"""发明人取数（星河智源，浏览器，0 积分）：名下专利 + 申请人 + 当年件数。

用法:
    python3 scripts/mindflow_inventor.py "金锐博" [--year 2026] [--session <id>] [--keep]
    python3 scripts/mindflow_inventor.py "金锐博" --out cache/inventor_batch.json

会话：默认复用当前活着的浏览器会话（没有才新开），跑完只关自己开的那个，
所以同一批反复跑本脚本也只占一个窗口；要强制另开一个用 --new-session。

做什么（两条检索，能省就省）:

    ⚠️ ⑤a 口径：不论发明人位次、当年申请按申请号去重计件，检索式 IN:(姓名) AND APD:[...]；
       F_IN 只含第一发明人，会漏件，不用于判定。

    1) 当年件数
       expr = IN:(姓名) AND APD:[YYYY0101 TO 今天]
       → 融合检索页填完表达式后**页面即时预览「当前检索共 N 条专利」**，不必点检索。
          条数 ≤5 → 直接判通过，立刻走人（条数 ≥ 件数，条数不过线，件数更不会）。
          条数 >5 → 才点检索读「检索到 N 组（共 M 条）」，用**组数**判（组=申请件数）。
    2) 名下全部专利的申请人（⑤b）
       expr = IN:(姓名)  → 结果页读表格「当前申请(专利权)人」列。

输出（stdout，JSON）:
    {"inventor":"金锐博","year":2026,"ytd":{"expr":...,"preview":N,"groups":N,"records":M},
     "all":{"groups":16,"records":19,"rows":[{"pub":"CN121632333A","app_date":"2026-02-05",
            "applicants":["湖南师范大学"],"ipc":"G01J1/42","app_no":"CN..."}]},
     "cost_points":0}

依赖: bsk CLI（daemon + 浏览器扩展已连接）。不依赖 MCP、不消耗积分。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

FUSION = "https://www.mindflow.cn/search/fusion"
# 件数阈值：预览条数 >5 才需要真检索读「组」（条数 ≥ 件数，条数不过线就不必细算）
GROUP_LIMIT = 5

REF_PATTERNS = {
    "expr_mode": r'(@e\d+) button "检索式方式"',
    "expr_box": r'(@e\d+) textbox "请输入检索式"',
    "search_btn": r'(@e\d+) button "检索"(?!\w)',
}
COUNT_PREVIEW = re.compile(r"当前检索共\s*\n?\s*([\d,]+)\s*\n?\s*条专利")
COUNT_RESULT = re.compile(r"检索到\s*([\d,]+)\s*组\s*（共\s*([\d,]+)\s*条）")
ROW_CELL = re.compile(r'\n\s+cell(?:\s+"([^"]*)")?')


class Err(RuntimeError):
    pass


LAST_OBS = {"text": "", "id": ""}


def bsk(*args, want_json=True, timeout=60):
    cmd = ["bsk"] + list(args)
    for attempt in range(4):
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (p.stdout or "").strip()
        if want_json and out.startswith("{"):
            try:
                d = json.loads(out)
            except ValueError:
                d = None
            if d is not None:
                code = d.get("code")
                if code in ("cdp_failed", "timeout"):
                    time.sleep(3 + 2 * attempt)   # 会话未 settle，等一等再重试
                    continue
                # 成功响应也可能带 "code"（如 press 返回 {"code":"Enter"}），只认 exit_code/ok
                if d.get("exit_code", 0) >= 1 or d.get("ok") is False:
                    raise Err("%s: %s" % (code or "BSK_ERROR", d.get("message") or d.get("error")))
                return d
        if p.returncode == 0:
            return {"_raw": out}
        raise Err("bsk %s 失败(exit %d): %s" % (" ".join(args[:2]), p.returncode,
                                              (p.stderr or out)[:300]))
    raise Err("bsk %s 重试用尽" % " ".join(args[:2]))


def sync(session):
    """拿 context_id + 最新 observation（refs 每轮都会重排，用完即弃）。

    sync 在「页面没变化」时只返回 kind=unchanged、不带 observation 文本，
    这时补一次 observe，否则调用方会因为拿不到 ref 而误判页面结构。
    """
    d = bsk("cooperate", "sync", "--session", session, "--json")
    obs = (d.get("observation") or {}).get("text") or ""
    LAST_OBS["id"] = d.get("observation_id") or LAST_OBS["id"]
    if len(obs) < 500:
        obs = observe(session) or obs
    LAST_OBS["text"] = obs
    return d["context_id"], obs


def observe(session):
    d = bsk("observe", "--session", session, "--max-tokens", "12000", "--json")
    text = d.get("text") or (d.get("observation") or {}).get("text") or ""
    if text:
        LAST_OBS["text"] = text
        LAST_OBS["id"] = d.get("observation_id") or LAST_OBS["id"]
    return text


def start_session(name="mindflow-inventor"):
    d = bsk("session", "start", "--name", name, "--json")
    return d["session_id"]


def live_sessions():
    """当前活着的会话（每个会话就是一个浏览器窗口）。"""
    try:
        d = bsk("session", "list", "--json")
    except Err:
        return []
    if isinstance(d, list):
        return d
    return d.get("sessions") or []


def reuse_session():
    """已有会话就复用它的 session_id，别再新开窗口。"""
    for s in live_sessions():
        sid = s.get("session_id") or s.get("id")
        if sid:
            return sid
    return ""


def stop_session(session):
    """关会话（=关掉那个 Agent Window）。

    `bsk session stop` 收**位置参数**（`bsk session stop <id>` / `--all`），
    写成 `--session <id>` 会返回 cli_parse_error，窗口关不掉。
    """
    time.sleep(2.0)
    for _ in range(3):
        try:
            bsk("session", "stop", session, "--json")
            return True
        except Err:
            time.sleep(1.5)
    sys.stderr.write("警告：会话 %s 没关掉，手动跑 bsk session stop %s\n" % (session, session))
    return False


def goto_fusion(session):
    for attempt in range(4):
        ctx, obs = sync(session)
        if 'button "检索式方式"' in obs or 'textbox "请输入检索式"' in obs:
            return obs
        try:
            bsk("navigate", "--session", session, "--context", ctx,
                "--observation", LAST_OBS["id"], FUSION, "--json")
        except Err as e:
            if "observation" in str(e) or "strict" in str(e):
                time.sleep(2 + attempt)
                continue
            raise
        time.sleep(4.0)
        _, obs = sync(session)
        if 'button "检索式方式"' in obs or 'textbox "请输入检索式"' in obs:
            return obs
    raise Err("导航到融合检索页失败（4 次重试）")


def ref(obs, key):
    m = re.search(REF_PATTERNS[key], obs)
    if not m:
        raise Err("页面里找不到「%s」，可能改版了；用 observe 人工确认一次" % key)
    return m.group(1)


def fill_expr(session, expr):
    """切到检索式方式并填入表达式，返回填充后的 observation（含即时预览条数）。

    三种状态要分开处理：
      A. 检索式方式 + 编辑器空   → 直接 fill
      B. 检索式方式 + 编辑器有旧值（VOM 里编辑器会拆成 token 按钮，不再是 textbox）
         → 先点「清空」，再 fill
      C. 还在表格方式            → 点「检索式方式」后重来
    """
    for _ in range(4):
        ctx, obs = sync(session)
        if "textbox \"请输入检索式\"" in obs:
            box = ref(obs, "expr_box")
            bsk("fill", "--session", session, "--context", ctx,
                "--ref", box, "--value", expr, "--json")
            time.sleep(0.8)
            _, obs = sync(session)
            if "当前检索共" not in obs:
                obs = observe(session)
            return obs

        if 'button "填入上一次检索式"' in obs:   # 状态 B：检索式方式 + 编辑器里有旧表达式
            # 只在「填入上一次检索式 … 保存检索式」这段里找清空（页面还有语义/图像的清空，
            # 且主内容的无障碍名里也会重复出现这些字样，不能用裸字符串定位）
            m_save = re.search(r'(@e\d+) button "保存检索式"', obs)
            m_prev = re.search(r'(@e\d+) button "填入上一次检索式"', obs)
            region = obs[(m_prev.end() if m_prev else 0):(m_save.start() if m_save else len(obs))]
            clears = re.findall(r'(@e\d+) button "清空"', region)
            if not clears:
                raise Err("检索式方式下找不到「清空」按钮，页面可能改版")
            bsk("click", "--session", session, "--context", ctx,
                "--ref", clears[-1], "--json")
            time.sleep(1.2)
            continue

        if 'button "检索式方式"' in obs:      # 状态 C：还在表格方式
            bsk("click", "--session", session, "--context", ctx,
                "--ref", ref(obs, "expr_mode"), "--json")
            time.sleep(1.5)
            continue

        # 状态 D：站内把 URL 重定向到结果页了（常见）→ 强制导航回融合检索
        goto_fusion(session)
    raise Err("切到检索式方式失败：重试 4 次仍未出现表达式输入框")


def submit(session):
    """聚焦检索按钮再按 Enter（按钮右下角常被在线咨询气泡压住，直接 click 会失败）。"""
    ctx, obs = sync(session)
    btn = ref(obs, "search_btn")
    bsk("press", "--session", session, "--context", ctx,
        "--ref", btn, "Enter", "--json")
    time.sleep(4.0)
    return observe(session)


def parse_rows(obs):
    """结果表 → [{pub, app_date, applicants, ipc, app_type, app_no, status}]"""
    rows = []
    for block in re.split(r'\n\s+row\s+"', obs)[1:]:
        cells = [m.group(1) or "" for m in ROW_CELL.finditer(block)]
        if len(cells) < 10 or not cells[0].isdigit():
            continue
        rows.append({
            "pub": cells[1],
            "title": cells[2],
            "app_date": cells[3],
            "pub_date": cells[4],
            "status": cells[5],
            "applicants": [a for a in re.split(r"[\s；;]+", cells[6]) if a],
            "ipc": cells[7],
            "app_type": cells[8],
            "app_no": cells[9],
            "validity": cells[10] if len(cells) > 10 else "",
        })
    return rows


def _num(x):
    return int(str(x).replace(",", ""))


def run(inventor, year, session):
    today = time.strftime("%Y%m%d")
    ytd_expr = "IN:(%s) AND APD:[%d0101 TO %s]" % (inventor, year, today)
    all_expr = "IN:(%s)" % inventor

    # ---- ⑤a：先看即时预览，够小就走 ----
    obs = fill_expr(session, ytd_expr)
    m = COUNT_PREVIEW.search(obs)
    preview = _num(m.group(1)) if m else None
    ytd = {"expr": ytd_expr, "preview": preview, "groups": None, "records": None,
           "method": "preview"}
    need_detail = preview is None or preview > GROUP_LIMIT
    if need_detail:
        obs = submit(session)
        m = COUNT_RESULT.search(obs)
        if not m:
            raise Err("⑤a 检索后没读到「检索到 N 组」，用 observe 人工看一眼")
        ytd.update(groups=_num(m.group(1)), records=_num(m.group(2)), method="result")

    # ---- ⑤b：名下全部专利的申请人 ----
    obs = fill_expr(session, all_expr)
    obs = submit(session)
    m = COUNT_RESULT.search(obs)
    rows = parse_rows(obs)
    allinfo = {"expr": all_expr,
               "groups": _num(m.group(1)) if m else None,
               "records": _num(m.group(2)) if m else None,
               "rows": rows}
    if not rows:
        allinfo["warning"] = "结果表没解析到行（虚拟滚动/改版），人工补一次"

    return {"inventor": inventor, "year": year, "ytd": ytd, "all": allinfo,
            "cost_points": 0, "source": "mindflow 融合检索（浏览器）",
            "fetched": time.strftime("%Y-%m-%d")}


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("inventor", nargs="?", default="", help="单个发明人姓名")
    ap.add_argument("--names", default="", help="逗号分隔的多个发明人：一批只开一个会话")
    ap.add_argument("--year", type=int, default=int(time.strftime("%Y")))
    ap.add_argument("--session", default="", help="复用指定会话")
    ap.add_argument("--new-session", action="store_true",
                    help="强制新开窗口；默认复用已有会话，避免窗口越开越多")
    ap.add_argument("--keep", action="store_true", help="保留会话（默认用完就关）")
    ap.add_argument("--out", default="", help="同时写入缓存文件")
    a = ap.parse_args(argv[1:])

    names = [n.strip() for n in (a.names or a.inventor).split(",") if n.strip()]
    if not names:
        print(json.dumps({"error": "没给发明人姓名"}, ensure_ascii=False))
        return 2

    own = False
    session = a.session
    if not session and not a.new_session:
        session = reuse_session()
        if session:
            sys.stderr.write("复用已有会话 %s，不再新开窗口\n" % session)
    if not session:
        session = start_session()
        own = True
    recs = []
    try:
        # 每个会话先开一个带 URL 的标签页：新窗口是 about:blank，没有内容脚本
        bsk("tab", "create", "--session", session, "--url", FUSION, "--json")
        time.sleep(4.0)
        # 站内常把 /search/fusion 重定向到上次的 /search/list，这里确保真的落到融合检索页
        goto_fusion(session)
        for name in names:
            recs.append(run(name, a.year, session))
    finally:
        if own and not a.keep:
            stop_session(session)

    out = recs[0] if len(recs) == 1 else recs
    txt = json.dumps(out, ensure_ascii=False, indent=1)
    print(txt)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(txt + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Err as e:
        try:
            if LAST_OBS["text"]:
                with open("/tmp/mindflow_last_obs.txt", "w", encoding="utf-8") as f:
                    f.write(LAST_OBS["text"])
        except Exception:
            pass
        print(json.dumps({"error": str(e),
                          "hint": "最后一份页面观察已存 /tmp/mindflow_last_obs.txt"},
                         ensure_ascii=False))
        sys.exit(1)
