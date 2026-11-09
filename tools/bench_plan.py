# -*- coding: utf-8 -*-
"""本地验收：真实调用 API，测 3 天 / 5 天耗时 + 景点是否重复"""
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)

from streamlit.testing.v1 import AppTest  # noqa: E402


def norm(name):
    n = (name or "").strip()
    for ch in "（(）)【[]】·・ 　\t\n":
        n = n.replace(ch, "")
    for c in ("天津", "石家庄", "保定", "正定", "河北"):
        n = n.replace(c, "")
    return n


def run(days, city="天津", budget=1500):
    at = AppTest.from_file(os.path.join(HERE, "main.py"), default_timeout=420)
    at.run()
    at.sidebar.text_input[0].set_value(city)
    at.sidebar.slider[0].set_value(days)
    at.sidebar.slider[1].set_value(budget)
    at.sidebar.multiselect[0].set_value(["古迹", "美食", "打卡热点"])
    t0 = time.time()
    at.sidebar.button[0].click().run()
    dt = time.time() - t0

    plan = at.session_state.get("plan")
    if not plan:
        print(f"[{days}天] 失败：无 plan", flush=True)
        return
    names = []
    for d in plan.get("days") or []:
        dnames = [it.get("name") for it in (d.get("items") or []) if it.get("type") != "交通"]
        names.extend(dnames)
        print(f"  D{d.get('day')}: {dnames}", flush=True)
    keys = [norm(n) for n in names]
    dups = sorted({k for k in keys if k and keys.count(k) > 1})
    print(f"[{days}天] 耗时 {dt:.1f}s 天数={len(plan.get('days') or [])} "
          f"条目={len(names)} 重复={dups}", flush=True)


if __name__ == "__main__":
    targets = [int(x) for x in sys.argv[1:]] or [3, 5]
    for d in targets:
        run(d)
