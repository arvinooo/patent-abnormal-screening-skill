#!/usr/bin/env python3
"""incoPat 浏览器取数客户端（bsk 驱动）。

前置条件：
  1. bsk daemon 在跑（`bsk status` 有 daemon version）。
  2. 浏览器已登录 incoPat 账号（账号密码或 IP 登录均可），且能看到「检索」菜单。

口径：
  - 表达式检索走「指令检索」：https://www.incopat.com/advancedSearch/init 的 #textarea。
    字段码：AP=申请人 IN=发明(设计)人 AD=申请日 PD=公开日 PN/AN IPC TI/AB/CLAIM/DES/ALL。
    日期区间用方括号：AD=[20251110 TO 20261109]；不支持 AD>= 与 AD=[* TO ...]。
  - 语义检索走「语义检索」：https://www.incopat.com/semanticSearch/init 的 #querytext，
    结果带相关度百分比。
  - 结果页左侧统计按「国家 / 专利类型」分组，「发明授权」一栏即授权发明文本数。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time

CACHE = os.environ.get("PATENT_CACHE_DIR", "cache")
SID_FILE = os.path.join(CACHE, ".bsk_session")
TAB_FILE = os.path.join(CACHE, ".bsk_inco_tab")

ADV = "https://www.incopat.com/advancedSearch/init"
SEM = "https://www.incopat.com/semanticSearch/init"
HOME = "https://www.incopat.com/"


class IncoError(RuntimeError):
    pass


def _bsk(*args, timeout: int = 240) -> dict:
    p = subprocess.run(["bsk", *args], capture_output=True, text=True, timeout=timeout)
    out = (p.stdout or "").strip()
    try:
        return json.loads(out)
    except Exception:
        return {"_raw": out[:400], "_err": (p.stderr or "")[:400], "_rc": p.returncode}


def _session_file() -> str:
    sid = ""
    if os.path.exists(SID_FILE):
        sid = open(SID_FILE).read().strip()
    return sid


def session(create: bool = True) -> str:
    """复用已有会话（一个会话 = 一个浏览器窗口）；没有就开一个。"""
    sid = _session_file()
    if sid:
        d = _bsk("session", "list", "--json")
        ids = [x.get("session_id") for x in d] if isinstance(d, list) else []
        if sid in ids:
            return sid
    if not create:
        return ""
    d = _bsk("session", "start", "--json")
    sid = d.get("session_id") or ""
    if not sid:
        raise IncoError("无法开启浏览器会话：%s" % d)
    os.makedirs(os.path.dirname(SID_FILE), exist_ok=True)
    open(SID_FILE, "w").write(sid)
    return sid


def tab(sid: str | None = None) -> str:
    """取当前会话里指向 incoPat 的标签页；没有就开一个检索页。"""
    sid = sid or session()
    want = ""
    if os.path.exists(TAB_FILE):
        want = open(TAB_FILE).read().strip()
    d = _bsk("tab", "list", "--session", sid, "--json")
    tabs = d.get("tabs") or [] if isinstance(d, dict) else []
    for t in tabs:
        if str(t.get("tab_id")) == want:
            return want
    for t in tabs:
        u = t.get("url") or ""
        if "incopat.com" in u:
            tid = str(t.get("tab_id"))
            open(TAB_FILE, "w").write(tid)
            return tid
    d = _bsk("tab", "create", "--session", sid, "--url", ADV, "--json")
    tid = str(d.get("tab_id") or "")
    if not tid:
        raise IncoError("无法打开标签页：%s" % d)
    os.makedirs(os.path.dirname(TAB_FILE), exist_ok=True)
    open(TAB_FILE, "w").write(tid)
    time.sleep(8)
    return tid


def _sync(sid: str, tid: str) -> tuple[str, str]:
    d = _bsk("cooperate", "sync", "--session", sid, "--tab-id", tid, "--json")
    ctx = d.get("context_id") or ""
    obs = d.get("observation_id") or ""
    if not ctx:
        raise IncoError("sync 失败：%s" % d)
    return ctx, obs


def js(code: str, sid: str | None = None, tid: str | None = None,
       attempts: int = 3) -> dict:
    """在 incoPat 标签页内执行一段表达式，返回解析后的值。"""
    sid = sid or session()
    tid = tid or tab(sid)
    last = {}
    for i in range(attempts):
        try:
            ctx, obs = _sync(sid, tid)
            d = _bsk("evaluate", "--session", sid, "--tab-id", tid,
                     "--context", ctx, "--observation", obs, "--json", code)
            if d.get("ok") is False or d.get("value") is None:
                last = d
                time.sleep(2 + 2 * i)
                continue
            v = d.get("value")
            if isinstance(v, str):
                try:
                    return json.loads(v)
                except Exception:
                    return {"_text": v}
            return {"_value": v}
        except Exception as e:          # 页面切换/自动刷新会让 evaluate 失效
            last = {"_err": str(e)[:200]}
            time.sleep(2 + 2 * i)
    return last if isinstance(last, dict) else {"_err": str(last)[:200]}


def goto(url: str, wait: float = 8.0, sid: str | None = None, tid: str | None = None):
    sid = sid or session()
    tid = tid or tab(sid)
    js("(()=>{location.href=%s;return 1;})()" % json.dumps(url), sid, tid)
    time.sleep(wait)


# ---------------------------------------------------------------- 表达式检索

_FILL_AND_SEARCH = """(()=>{
  const ed=document.getElementById('textarea');
  if(!ed) return JSON.stringify({error:'no editor',url:location.href});
  ed.focus();
  document.execCommand('selectAll',false,null);
  document.execCommand('insertText',false,%s);
  if(typeof directSearch==='function'){
    try{ directSearch(1); }catch(e){ return JSON.stringify({error:'页面脚本未就绪: '+e.message}); }
    return JSON.stringify({ok:1});
  }
  const b=[...document.querySelectorAll('input.retrieval')].filter(function(x){
    return (x.getAttribute('onclick')||'').includes('directSearch');
  });
  if(!b[0]) return JSON.stringify({error:'no search button'});
  b[0].click();
  return JSON.stringify({ok:1});
})()"""

_PREVIEW = """(function(){
  const ed=document.getElementById('textarea');
  if(!ed) return JSON.stringify({error:'no editor'});
  ed.focus();
  document.execCommand('selectAll',false,null);
  document.execCommand('insertText',false,%s);
  const m=document.body.innerText.match(/当前检索共\\s*([\\d,]+)\\s*条专利/);
  const e=document.body.innerText.match(/检索式解析错误[^\\n]*/);
  return JSON.stringify({count:m?m[1]:null,error:e?e[0]:null});
})()"""

_READ_RESULT = r"""(function(){
  const t=document.body.innerText;
  const expr=%s;
  const echo=(expr.length>4 && t.indexOf(expr.slice(0,18))>=0);
  const g=t.match(/共\s*([\d,]+)\s*条/);
  const none=(t.indexOf('暂无匹配结果')>=0);
  const ready=(echo&&(g!==null||none));
  const stat={};
  const re=/([\u4e00-\u9fff]{2,10})\(([\d,]+)\)/g;
  let m;
  while((m=re.exec(t))){ stat[m[1]]=parseInt(m[2].replace(/,/g,''),10); }
  const rows=[...document.querySelectorAll('.graphic-list-table-box tr')].map(function(r){
    return (r.innerText||'').replace(/\s+/g,' ').trim();
  }).filter(function(x){return x.length>40;});
  return JSON.stringify({url:location.href,count:g?g[1]:(none&&ready?'0':null),
                         none:none,ready:ready,stat:stat,rows:rows});
})()"""


def _read_result(expr: str, sid: str, tid: str) -> dict:
    return js(_READ_RESULT % json.dumps(expr, ensure_ascii=False), sid, tid)


_PUB_RE = re.compile(r"\bCN\d{6,12}[A-Z]\b")
_APPNO_RE = re.compile(r"\bCN\d{8,13}(?:\.\d)?\b")
_IPC_RE = re.compile(r"\b[A-H]\d{2}[A-Z]\s?\d+/\d+\b")
_DATE_RE = re.compile(r"\b(20\d{6}|19\d{6})\b")
_AP_RE = re.compile(r"[\u4e00-\u9fff（）()·A-Za-z]{2,40}(?:公司|大学|学院|研究院|研究所|中心|厂|局|医院|集团|合作社)")
_TYPE_RE = re.compile(r"(发明申请|发明授权|实用新型|外观设计)")


def parse_row(raw: str) -> dict:
    """把结果行文本解析成字段。行结构见 incoPat 结果页「图文浏览 → 列表」。"""
    pubs = _PUB_RE.findall(raw) or _APPNO_RE.findall(raw)
    app_nos = _APPNO_RE.findall(raw)
    ipcs = _IPC_RE.findall(raw)
    dates = _DATE_RE.findall(raw)
    apps = _AP_RE.findall(raw)
    types = _TYPE_RE.findall(raw)
    title = ""
    tm = re.search(r"(?:发明申请|发明授权|实用新型|外观设计)\s*\S*\s*([^\[][^\[]*?)\s*\[", raw)
    if tm:
        title = tm.group(1).strip()
    if not title:
        tm = re.search(r"(?:发明申请|发明授权|实用新型|外观设计)\s*\S*\s*(.+?)\s*\[", raw)
        if tm:
            title = tm.group(1).strip()
    app_date = ""
    dm = re.search(r"申请日[：:]\s*(\d{8})", raw)
    if dm:
        app_date = dm.group(1)
    pub_date = ""
    pm = re.search(r"公开\(公告\)日[：:]\s*(\d{8})", raw)
    if pm:
        pub_date = pm.group(1)
    return {"raw": raw,
            "pub": pubs[0] if pubs else "",
            "app_no": app_nos[0] if app_nos else "",
            "app_date": app_date,
            "pub_date": pub_date,
            "type": types[0] if types else "",
            "title": title,
            "applicants": [a.rstrip(";") for a in apps][:10],
            "ipc": ipcs[0] if ipcs else "",
            "dates": dates}


def preview_count(expr: str, wait: float = 2.5) -> dict:
    """只取「当前检索共 N 条专利」预览（不点检索，最省）。"""
    sid = session()
    tid = tab(sid)
    js(_PREVIEW % json.dumps(expr, ensure_ascii=False), sid, tid)
    time.sleep(wait)
    d = js("(function(){"
           "const m=document.body.innerText.match(/当前检索共\\\\s*([\\\\d,]+)\\\\s*条专利/);"
           "const e=document.body.innerText.match(/检索式解析错误[^\\\\n]*/);"
           "return JSON.stringify({count:m?m[1]:null,error:e?e[0]:null});})()", sid, tid)
    n = d.get("count")
    return {"expr": expr, "count": int(str(n).replace(",", "")) if n else None,
            "error": d.get("error")}


def wait_ready(timeout: float = 30.0, sid: str | None = None, tid: str | None = None) -> bool:
    """等待检索页就绪：编辑器存在 + 页面检索脚本已加载完成。"""
    sid = sid or session()
    tid = tid or tab(sid)
    code = ("JSON.stringify({ok:!!document.getElementById('textarea')"
            "&&typeof directSearch==='function'"
            "&&typeof judgeExistHight==='function'})")
    end = time.time() + timeout
    while time.time() < end:
        d = js(code, sid, tid, attempts=1)
        if d.get("ok"):
            return True
        time.sleep(2)
    return False


def search(expr: str, wait: float = 10.0, all_pages: bool = False,
           page_size: int = 100, max_pages: int = 10) -> dict:
    """跑一次表达式检索，返回条数、左侧统计、结果行。

    all_pages=True 时把每页调到 page_size 并翻页取完（上限 max_pages 页）。
    """
    sid = session()
    tid = tab(sid)
    goto(ADV, wait=3, sid=sid, tid=tid)
    wait_ready(30, sid, tid)
    d = js(_FILL_AND_SEARCH % json.dumps(expr, ensure_ascii=False), sid, tid)
    if d.get("error") and not d.get("ok"):
        return {"expr": expr, "error": d["error"]}
    time.sleep(wait)
    r = _read_result(expr, sid, tid)
    # incoPat 检索是异步渲染：条数或「无结果」出现前重试
    for i in range(6):
        if r.get("count") is not None:
            break
        time.sleep(4)
        r = _read_result(expr, sid, tid)
    rows = [parse_row(x) for x in (r.get("rows") or [])]
    if all_pages:
        js(_SET_PAGE_SIZE % json.dumps(str(page_size)), sid, tid)
        time.sleep(3)
        r = _read_result(expr, sid, tid)
        rows = [parse_row(x) for x in (r.get("rows") or [])]
        seen = {x["pub"] or x["app_no"] for x in rows}
        for _ in range(max_pages - 1):
            nx = js(_NEXT_PAGE, sid, tid)
            if not nx.get("next"):
                break
            time.sleep(3)
            r2 = _read_result(expr, sid, tid)
            for raw in r2.get("rows") or []:
                rec = parse_row(raw)
                k = rec["pub"] or rec["app_no"]
                if k and k in seen:
                    continue
                seen.add(k)
                rows.append(rec)
    return {"expr": expr, "count": int(str(r.get("count")).replace(",", "")) if r.get("count") else None,
            "stat": r.get("stat") or {}, "rows": rows, "url": r.get("url")}


def granted_invention(app: str, base_date: str) -> dict:
    """到 base_date 为止，申请人名下的授权发明文本数（结果页「发明授权」统计）。"""
    base = (base_date or "").replace("-", "")[:8]
    if not base:
        import datetime as dt
        base = dt.date.today().strftime("%Y%m%d")
    expr = f"AP=({app}) AND AD=[19850101 TO {base}]"
    r = search(expr)
    stat = r.get("stat") or {}
    if r.get("error") or r.get("count") is None:
        return {"applicant": app, "expr": expr, "granted_invention": None,
                "has": None, "error": r.get("error") or "检索未返回条数", "base_date": base}
    n = stat.get("发明授权", 0)
    return {"applicant": app, "expr": expr, "granted_invention": n,
            "has": n >= 1, "base_date": base, "total": r.get("count"),
            "stat": stat, "rows": r.get("rows") or []}


def inventor_patents(name: str, start: str, end: str) -> dict:
    """发明人滚动窗口下的专利：按申请号去重后的记录 + 申请人。"""
    expr = f"IN=({name}) AND AD=[{start} TO {end}]"
    r = search(expr)
    rows = r.get("rows") or []
    seen, uniq = set(), []
    for x in rows:
        k = x.get("app_no") or x.get("pub")
        if not k or k in seen:
            continue
        seen.add(k)
        uniq.append(x)
    return {"inventor": name, "expr": expr, "window_start": start, "window_end": end,
            "total_raw": r.get("count"), "stat": r.get("stat") or {},
            "records": uniq, "errors": [r["error"]] if r.get("error") else []}


# ---------------------------------------------------------------- 语义检索

_SEM_SEARCH = """(function(){
  const ed=document.getElementById('querytext');
  if(!ed) return JSON.stringify({error:'no semantic editor'});
  ed.focus(); ed.value=%s;
  ed.dispatchEvent(new Event('input',{bubbles:true}));
  ed.dispatchEvent(new Event('change',{bubbles:true}));
  const b=[...document.querySelectorAll('input.retrieval')].filter(function(x){return x.offsetParent!==null});
  if(!b[0]) return JSON.stringify({error:'no semantic button'});
  b[0].click();
  return JSON.stringify({ok:1});
})()"""

_READ_SEM = """(function(){
  const t=document.body.innerText;
  const g=t.match(/共计\\s*([\\d,]+)\\s*条专利/)||t.match(/共\\s*([\\d,]+)\\s*条/);
  const stat={};
  const re=/([\\u4e00-\\u9fff]{2,10})\\(([\\d,]+)\\)/g;
  let m; while((m=re.exec(t))){stat[m[1]]=parseInt(m[2].replace(/,/g,''),10);}
  const rows=[...document.querySelectorAll('.graphic-list-table-box tr')].map(function(r){
    return (r.innerText||'').replace(/\\s+/g,' ').trim();
  }).filter(function(x){return x.length>40;});
  return JSON.stringify({url:location.href,count:g?g[1]:null,stat:stat,rows:rows});
})()"""


def semantic(text: str, wait: float = 14.0) -> dict:
    """语义检索：输入技术文本，返回带相关度的候选与统计。"""
    sid = session()
    tid = tab(sid)
    goto(SEM, wait=8, sid=sid, tid=tid)
    d = js(_SEM_SEARCH % json.dumps(text, ensure_ascii=False), sid, tid)
    if d.get("error"):
        return {"query": text, "error": d["error"], "rows": []}
    time.sleep(wait)
    r = js(_READ_SEM, sid, tid)
    out = []
    for raw in r.get("rows") or []:
        m = re.match(r"^\s*\d+\s+([\d.]+)%\s+(.*)$", raw)
        score = float(m.group(1)) if m else None
        body = m.group(2) if m else raw
        rec = parse_row(body)
        rec["score"] = score
        out.append(rec)
    return {"query": text, "count": int(str(r.get("count")).replace(",", "")) if r.get("count") else None,
            "stat": r.get("stat") or {}, "rows": out, "url": r.get("url")}


def claims_text(pub: str) -> dict:
    """取单件专利的权利要求文本。

    incoPat 详情页地址依赖检索上下文（formerQuery），无法用公开号直接拼 URL；
    这里改为在当前结果页按公开号定位该行，抓「首项权利要求」字段——
    该字段需先在结果页「显示字段」勾选（账号会记住），未勾选时返回空。
    """
    sid = session()
    tid = tab(sid)
    code = r"""(function(){
      const rows=[...document.querySelectorAll('.graphic-list-table-box tr')];
      const hit=rows.find(function(r){return (r.innerText||'').indexOf(%s)>=0;});
      if(!hit) return JSON.stringify({found:0});
      const t=(hit.innerText||'').replace(/\s+/g,' ');
      const m=t.match(/首项权利要求\(中文\)[：:]\s*(.+?)(?=$| 申请| IPC| 公开| 发明)/);
      return JSON.stringify({found:1,claims:m?m[1]:'',raw:t.slice(0,1500)});
    })()""" % json.dumps(pub)
    d = js(code, sid, tid)
    return {"pub": pub, "claims": d.get("claims") or "",
            "found": bool(d.get("found")), "raw": d.get("raw") or ""}


def close(tab_only: bool = False):
    """收尾：关闭本客户端用的标签页或整个会话。

    tab_only=True 只关标签页，保留会话供其它脚本（香橼树）复用。
    """
    try:
        sid = _session_file()
        if not sid:
            return
        if tab_only:
            try:
                tid = open(TAB_FILE).read().strip()
                _bsk("tab", "close", "--session", sid, tid, "--json")
            except Exception:
                pass
            if os.path.exists(TAB_FILE):
                os.remove(TAB_FILE)
        else:
            _bsk("session", "stop", sid, "--json")
            for f in (TAB_FILE, SID_FILE):
                if os.path.exists(f):
                    os.remove(f)
    except Exception:
        pass
