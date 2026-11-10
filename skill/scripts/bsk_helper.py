#!/usr/bin/env python3
"""bsk 浏览器取数小工具（香橼树代理师月度统计）。

所有动作都按已验证的流程写：sync -> 动作 -> sync -> 读取。
只做"取数"，不做判定。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time

SID_FILE = os.path.join(os.environ.get("PATENT_CACHE_DIR", "cache"), ".bsk_session")


def _bsk(*args, timeout=200) -> dict:
    p = subprocess.run(["bsk", *args], capture_output=True, text=True, timeout=timeout)
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"_raw": (p.stdout or "")[:400], "_err": (p.stderr or "")[:400]}


def session() -> str:
    """复用已有会话；没有就开一个（不动用户的标签页）。"""
    if os.path.exists(SID_FILE):
        sid = open(SID_FILE).read().strip()
        sessions = _bsk("session", "list", "--json")
        ids = [x.get("session_id") for x in sessions] if isinstance(sessions, list) else []
        if sid and sid in ids:
            return sid
    d = _bsk("session", "start", "--json")
    sid = d.get("session_id") or ""
    os.makedirs(os.path.dirname(SID_FILE), exist_ok=True)
    open(SID_FILE, "w").write(sid)
    return sid


def stop_session():
    sid = ""
    if os.path.exists(SID_FILE):
        sid = open(SID_FILE).read().strip()
    if sid:
        _bsk("session", "stop", sid)
        try:
            os.remove(SID_FILE)
        except OSError:
            pass


def sync(sid: str) -> tuple[str, str]:
    d = _bsk("cooperate", "sync", "--session", sid, "--json")
    return d.get("context_id") or "", d.get("observation_id") or ""


def js(sid: str, code: str, timeout_s: int = 180) -> dict:
    """执行一段 JS，自动重试一次（页面变化会要求重新 sync）。"""
    for _ in range(2):
        ctx, obs = sync(sid)
        if not ctx:
            time.sleep(2)
            continue
        d = _bsk("evaluate", "--session", sid, "--context", ctx, "--observation", obs,
                 "--timeout", f"{timeout_s}s", "--json", code)
        if d.get("value") is not None:
            return {"value": d["value"]}
    return {"error": "evaluate failed"}


def has_tabs(sid: str) -> bool:
    d = _bsk("tab", "list", "--session", sid, "--json")
    return bool(d.get("tabs")) if isinstance(d, dict) else False


def _open(sid: str, url: str, wait=6):
    _bsk("tab", "create", "--session", sid, "--url", url, "--json")
    time.sleep(wait)


# ---------------------------------------------------------------- 香橼树（代理师月度）

MONTH_JS = """(async()=>{
  const ins=[...document.querySelectorAll('input.el-input__inner')];
  if(ins.length<2) return JSON.stringify({error:'no inputs',url:location.href});
  const nameIn=ins[0], yearIn=ins[1];
  const out={};
  for(const [nm,yr] of %s){
    nameIn.value=nm; nameIn.dispatchEvent(new Event("input",{bubbles:true}));
    yearIn.value=yr; yearIn.dispatchEvent(new Event("input",{bubbles:true}));
    yearIn.dispatchEvent(new Event("change",{bubbles:true}));
    await new Promise(r=>setTimeout(r,400));
    [...document.querySelectorAll('button')].find(b=>b.innerText.trim()==='搜索')?.click();
    await new Promise(r=>setTimeout(r,3000));
    const rows=[...document.querySelectorAll('.el-table__row')].map(r=>r.innerText.replace(/\\s+/g,' ').trim());
    out[nm+'|'+yr]=rows[0]||null;
  }
  return JSON.stringify(out);
})()"""


def agent_months(pairs: list[tuple[str, str]]) -> dict:
    """pairs: [(代理师, '2025'), ...] -> {'姓名|年': '姓名 n n n ...'}"""
    sid = session()
    if not has_tabs(sid):
        _open(sid, "https://main.xysip.com/?r=%d#/pt/orgData/agentYearStatistics" % int(time.time()), 10)
    d = js(sid, MONTH_JS % json.dumps(pairs, ensure_ascii=False), 150)
    return json.loads(d.get("value") or "{}")


def parse_month_row(row: str) -> list[int]:
    """'汝涛 17 0 17 9 0 9 ...' -> 每月总量 [17,9,...]（每组三列取第一列）"""
    nums = [int(x) for x in re.findall(r"\b\d+\b", row or "")][1:]  # 去掉姓名后的数字
    return nums[0::3]
