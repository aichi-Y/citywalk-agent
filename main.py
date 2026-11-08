# -*- coding: utf-8 -*-
"""
城墙漫游记 - 城市微旅行智能规划 Agent（main.py 胶水层）
====================================================
职责（对应教程 2.2）：
1. 接收用户输入：城市、天数、预算、偏好
2. 调用大模型/Agent 接口，把参数传给智能体
3. 扰动测试入口：输入【XX景点今日闭园 / 明天有大雨】，触发自动重规划
4. 整理返回结果，美化输出格式 + 预算核算校验

运行方式：
    streamlit run main.py --server.port=8000

模型配置（三选一，写入同目录 .env 或系统环境变量）：
    LLM_API_BASE  例如 https://open.bigmodel.cn/api/paas/v4  (智谱/DeepSeek/通义等 OpenAI 兼容地址)
    LLM_API_KEY   你的 Key
    LLM_MODEL     例如 glm-4-flash / deepseek-chat
"""

import json
import os

import requests
import streamlit as st

# ---------------- 基础配置 ----------------

HERE = os.path.dirname(os.path.abspath(__file__))

def load_config():
    """读取 agent_config.json（与 JoyCode 智能体面板保持一致的提示词）"""
    with open(os.path.join(HERE, "agent_config.json"), "r", encoding="utf-8") as f:
        return json.load(f)

def load_env_file():
    """简易 .env 读取（避免额外依赖）"""
    env_path = os.path.join(HERE, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

CONFIG = load_config()
SYSTEM_PROMPT = CONFIG["system_prompt"]

load_env_file()  # 读取同目录 .env 中的模型配置

API_BASE = os.environ.get("LLM_API_BASE", "")
API_KEY = os.environ.get("LLM_API_KEY", "")
MODEL = os.environ.get("LLM_MODEL", "glm-4-flash")

# ---------------- Agent 调用层 ----------------

def call_agent(messages: list) -> str:
    """调用 OpenAI 兼容接口。若接的是 JoyCode 平台 Agent API，只需替换本函数。"""
    if not API_KEY:
        return json.dumps({"error": "未配置 LLM_API_KEY，请编辑项目目录下的 .env 文件"}, ensure_ascii=False)
    url = API_BASE.rstrip("/") + "/chat/completions"
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={"model": MODEL, "messages": messages, "temperature": 0.4},
            timeout=90,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:  # 网络异常也要给用户明确反馈
        return json.dumps({"error": f"调用失败：{e}"}, ensure_ascii=False)

def build_user_input(city, days, budget, prefs):
    return (
        f"请为我规划行程。目标城市：{city}；出行天数：{days} 天；"
        f"总预算：{budget} 元；偏好标签：{'、'.join(prefs) if prefs else '不限'}。"
        f"请严格按系统规定的 JSON 格式输出。"
    )

def parse_plan(text: str) -> dict | None:
    """从模型返回中解析 JSON（容错：剥掉 markdown 代码块）"""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None

# ---------------- 预算核算（数学校验层） ----------------

def verify_budget(plan: dict, budget: float):
    """独立于大模型核算总花费，超出预算给出警示——答辩时展示'双保险'"""
    if not plan or "days" not in plan:
        return None, "行程数据异常"
    total = sum(
        item.get("cost", 0) or 0
        for day in plan["days"]
        for item in day.get("items", [])
        if item.get("type") != "交通" or item.get("cost", 0)
    )
    msg = f"核算总花费 {total} 元 / 预算 {budget} 元 → " + ("✅ 未超支" if total <= budget else "❌ 超支，已要求 Agent 降级调整")
    return total, msg

# ---------------- Streamlit 界面 ----------------

st.set_page_config(page_title="城墙漫游记 · 城市微旅行规划Agent", page_icon="🏯", layout="wide")
st.title("🏯 城墙漫游记 —— 城市微旅行智能规划 Agent")
st.caption("输入城市 / 天数 / 预算 / 偏好，Agent 自动检索景点信息并规划路线；支持扰动（闭园、天气变化）自动重规划")

if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
if "plan" not in st.session_state:
    st.session_state.plan = None

with st.sidebar:
    st.header("🧳 行程需求")
    city = st.text_input("目标城市", "天津")
    days = st.slider("出行天数", 1, 3, 2)
    budget = st.slider("总预算（元）", 200, 3000, 800, step=100)
    prefs = st.multiselect("偏好标签", ["美食", "古迹", "免费景点"], default=["古迹", "美食"])
    go = st.button("🚀 生成行程规划", type="primary", use_container_width=True)

    st.divider()
    st.header("⚡ 扰动测试（核心亮点）")
    st.caption("模拟突发事件，看 Agent 自动重规划")
    disturb = st.text_input("扰动信息", placeholder="例：古文化街今日临时闭园，明天有大雨")
    disturb_btn = st.button("🌪️ 触发扰动重规划", use_container_width=True)

col1, col2 = st.columns([3, 2])

def render_plan(plan: dict):
    if not plan:
        return
    if "error" in plan:
        st.error(plan["error"])
        return
    st.subheader(f"📍 {plan.get('city', '')} 行程方案")
    for day in plan.get("days", []):
        with st.expander(f"第 {day.get('day')} 天", expanded=True):
            for item in day.get("items", []):
                icon = {"景点": "🏛️", "美食": "🍜", "交通": "🚌"}.get(item.get("type", ""), "•")
                st.markdown(
                    f"**{item.get('time','')} {icon} {item.get('name','')}**"
                    f"（{item.get('duration','')} · 约 {item.get('cost',0)} 元）"
                )
                if item.get("desc"):
                    st.write(item["desc"])
                if item.get("guide"):
                    st.info(f"🎙️ 讲解词：{item['guide']}")
    foods = plan.get("food") or []
    if foods:
        st.markdown("🍜 **美食推荐**：" + "、".join(foods))
    st.success(f"💰 预估总花费：{plan.get('total_cost', '-')} 元 ｜ 预算内：{'是' if plan.get('within_budget') else '否'}")
    if plan.get("notes"):
        st.write(f"📝 {plan['notes']}")

with col1:
    if go:
        st.session_state.messages.append({"role": "user", "content": build_user_input(city, days, budget, prefs)})
        with st.spinner("Agent 正在检索景点信息并规划路线…"):
            reply = call_agent(st.session_state.messages)
        st.session_state.messages.append({"role": "assistant", "content": reply})
        st.session_state.plan = parse_plan(reply)
        st.session_state.last_reply = reply
    if disturb_btn and disturb:
        st.session_state.messages.append(
            {"role": "user", "content": f"【突发扰动】{disturb}。请立即对当前行程自动重规划：更换受影响景点、调整顺序、重新核算预算，并逐条说明调整理由。仍按原 JSON 格式输出。"}
        )
        with st.spinner("Agent 正在重新规划…"):
            reply = call_agent(st.session_state.messages)
        st.session_state.messages.append({"role": "assistant", "content": reply})
        st.session_state.plan = parse_plan(reply)
        st.session_state.last_reply = reply

    if "last_reply" in st.session_state:
        if st.session_state.plan:
            render_plan(st.session_state.plan)
        else:
            st.warning("Agent 返回内容未能解析为 JSON，原始回复如下：")
            st.code(st.session_state.last_reply, language="text")

with col2:
    total, msg = verify_budget(st.session_state.get("plan"), budget)
    st.header("🧮 预算核算")
    if msg:
        st.write(msg)
    st.divider()
    st.header("🧾 Agent 调用记录")
    st.caption("答辩时展示：扰动前后两次调用对比")
    for m in st.session_state.messages[1:]:
        who = "🧑 用户" if m["role"] == "user" else "🤖 Agent"
        with st.expander(who + "：" + (m["content"][:40] + "…" if len(m["content"]) > 40 else m["content"])):
            st.text(m["content"][:2000])
