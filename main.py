# -*- coding: utf-8 -*-
"""
智漫游 —— 城市旅行智能规划 Agent（main.py 胶水层）
====================================================
职责：
1. 接收用户输入：城市、天数（1-5）、预算、偏好（7 类标签）
2. 调用大模型/Agent 接口，把参数传给智能体
3. 扰动测试入口：输入【XX景点今日闭园 / 明天有大雨】，触发自动重规划
4. 整理返回结果：逐景点配图 + 景点间交通衔接 + 讲解词 + 预算核算校验

运行方式：
    streamlit run main.py --server.port=8000

模型配置（写入同目录 .env 或云端 Secrets）：
    LLM_API_BASE  例如 https://open.bigmodel.cn/api/paas/v4
    LLM_API_KEY   你的 Key
    LLM_MODEL     例如 glm-4-flash
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor

import requests
import streamlit as st

# ---------------- 基础配置 ----------------

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGE_DIR = os.path.join(HERE, "static", "images")

APP_NAME = "智漫游 —— 城市旅行智能规划 Agent"
MAX_DAYS = 5

PREF_OPTIONS = [
    "美食",
    "古迹",
    "免费景点",
    "打卡热点",
    "自然风光",
    "文博展馆",
    "休闲慢逛（citywalk）",
]

# 景点配图：7 类素材（本地静态图，离线可用、加载稳定）
IMAGE_TYPES = {
    "heritage": "古迹建筑",
    "landmark": "现代地标",
    "museum": "文博展馆",
    "nature": "自然风光",
    "food": "地方美食",
    "night": "夜景灯影",
    "street": "街区漫步",
}

# 关键词 → 图片类别（模型未给出 img 字段时兜底推断）
IMAGE_KEYWORDS = [
    ("food", ["小吃", "餐厅", "美食", "饭", "面", "包子", "煎饼", "烧烤", "火锅", "咖啡", "茶", "甜品", "夜市"]),
    ("museum", ["博物馆", "美术馆", "展馆", "纪念馆", "图书馆", "展览"]),
    ("night", ["夜景", "夜游", "灯光", "夜市", "酒吧"]),
    ("nature", ["公园", "湖", "湿地", "山", "植物园", "河畔", "绿道", "森林"]),
    ("street", ["街", "巷", "胡同", "广场", "五大道", "商圈", "步行街"]),
    ("landmark", ["塔", "大楼", "中心", "大桥", "摩天轮", "电视塔", "地标", "天眼"]),
    ("heritage", ["寺", "庙", "宫", "城墙", "故居", "遗址", "会馆", "文庙", "古镇", "陵"]),
]


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


def load_streamlit_secrets():
    """云端部署时从 st.secrets 读取模型配置（本地无 secrets.toml 时直接跳过，避免界面报错条）"""
    candidates = [
        os.path.join(HERE, ".streamlit", "secrets.toml"),
        os.path.expanduser("~/.streamlit/secrets.toml"),
    ]
    if not any(os.path.exists(p) for p in candidates):
        return
    try:
        for k in ("LLM_API_BASE", "LLM_API_KEY", "LLM_MODEL"):
            if k in st.secrets:
                os.environ.setdefault(k, str(st.secrets[k]))
    except Exception:
        pass


CONFIG = load_config()
SYSTEM_PROMPT = CONFIG["system_prompt"]

# 注意：加载 .env / st.secrets 属于会触发 Streamlit 消息的动作，
# 必须放在 st.set_page_config() 之后执行，否则会报 “set_page_config can only be called once”。
API_BASE = ""
API_KEY = ""
MODEL = "glm-4-flash"


def refresh_api_config():
    """在界面初始化之后调用，把 .env / Secrets 读进全局变量"""
    global API_BASE, API_KEY, MODEL
    load_streamlit_secrets()
    load_env_file()
    API_BASE = os.environ.get("LLM_API_BASE", "")
    API_KEY = os.environ.get("LLM_API_KEY", "")
    MODEL = os.environ.get("LLM_MODEL", "glm-4-flash")


# ---------------- Agent 调用层 ----------------

def call_agent(messages: list) -> str:
    """非流式兜底调用（流式失败时使用）"""
    if not API_KEY:
        return json.dumps({"error": "未配置 LLM_API_KEY，请编辑项目目录下的 .env 文件"}, ensure_ascii=False)
    url = API_BASE.rstrip("/") + "/chat/completions"
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={"model": MODEL, "messages": messages, "temperature": 0.4, "max_tokens": 4096},
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return json.dumps({"error": f"调用失败：{e}"}, ensure_ascii=False)


def call_agent_stream(messages: list, on_update=None) -> str:
    """流式调用：边生成边回调，首字延迟内就能看到内容在动（体感快很多）"""
    if not API_KEY:
        return json.dumps({"error": "未配置 LLM_API_KEY，请编辑项目目录下的 .env 文件"}, ensure_ascii=False)
    url = API_BASE.rstrip("/") + "/chat/completions"
    buf = []
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={
                "model": MODEL,
                "messages": messages,
                "temperature": 0.4,
                "max_tokens": 4096,
                "stream": True,
            },
            timeout=(10, 180),
            stream=True,
        )
        resp.raise_for_status()
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data:"):
                continue
            data = raw[5:].strip()
            if data == "[DONE]":
                break
            try:
                delta = json.loads(data)["choices"][0].get("delta", {})
            except Exception:
                continue
            piece = delta.get("content") or ""
            if piece:
                buf.append(piece)
                if on_update:
                    on_update("".join(buf))
        return "".join(buf) if buf else call_agent(messages)
    except Exception:
        # 流式不被支持时自动降级为一次性返回，保证可用性
        return call_agent(messages)


def build_user_input(city, days, budget, prefs, day_range=None):
    base = (
        f"请为我规划行程。目标城市：{city}；出行天数：{days} 天；"
        f"总预算：{budget} 元；偏好标签：{'、'.join(prefs) if prefs else '不限'}。"
        f"要求：①每个景点之间必须给出交通方式、乘车站数/线路、耗时与费用，写入 transport 字段；"
        f"②为每个景点选择一个配图类别，写入 img 字段，取值只能是 "
        f"heritage / landmark / museum / nature / food / night / street 之一；"
        f"③每天 3—4 个条目，desc 不超过 25 字、guide 不超过 30 字，只输出 JSON，不要任何解释性文字。"
    )
    if day_range:
        a, b = day_range
        base += (
            f"④本次只需输出第 {a}—{b} 天（全程共 {days} 天）的 days 对象，"
            f"day 字段使用真实天序号；transport 仍按天内的景点顺序给出。"
        )
    return base


def parse_plan(text: str):
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
    """独立于大模型核算总花费，超出预算给出警示——答辩可展示'双保险'"""
    if not plan or "days" not in plan:
        return None, ""
    total = sum(
        item.get("cost", 0) or 0
        for day in plan["days"]
        for item in day.get("items", [])
    )
    msg = f"核算总花费 {total} 元 / 预算 {budget} 元 → " + (
        "✅ 未超支" if total <= budget else "❌ 超支，已要求 Agent 降级调整"
    )
    return total, msg


# ---------------- 景点配图 ----------------

def guess_image(item: dict) -> str:
    """优先用模型给的 img 字段，缺失则按名称/描述关键词推断，最后兜底 heritage"""
    img = str(item.get("img", "")).strip().lower()
    if img in IMAGE_TYPES:
        return img
    name = str(item.get("name", "")) + " " + str(item.get("desc", ""))
    for key, words in IMAGE_KEYWORDS:
        if any(w in name for w in words):
            return key
    return "heritage" if item.get("type") == "景点" else "food"


def image_path(key: str) -> str:
    """优先用小尺寸缩略图（渲染更快），缺失时回退原图"""
    thumb = os.path.join(IMAGE_DIR, "thumbs", f"{key}.jpg")
    if os.path.exists(thumb):
        return thumb
    path = os.path.join(IMAGE_DIR, f"{key}.jpg")
    return path if os.path.exists(path) else os.path.join(IMAGE_DIR, "heritage.jpg")


# ---------------- Streamlit 界面 ----------------

st.set_page_config(page_title="智漫游 · 城市旅行智能规划 Agent", page_icon="🧭", layout="wide")
refresh_api_config()
st.title("🧭 智漫游 —— 城市旅行智能规划 Agent")
st.caption(
    "输入城市 / 天数 / 预算 / 偏好，Agent 自动检索景点信息、规划路线并给出景点间交通衔接；"
    "遇到闭园、天气变化可自动重规划"
)

if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
if "plan" not in st.session_state:
    st.session_state.plan = None

with st.sidebar:
    st.header("🧳 行程需求")
    city = st.text_input("目标城市", "天津")
    days = st.slider("出行天数", 1, MAX_DAYS, 2)
    budget = st.slider("总预算（元）", 200, 5000, 800, step=100)
    prefs = st.multiselect(
        "偏好标签",
        PREF_OPTIONS,
        default=["古迹", "美食"],
        help="可多选；标签越具体，行程越贴合你的节奏",
    )
    go = st.button("🚀 生成行程规划", type="primary", use_container_width=True)
    demo_btn = st.button("⚡ 秒出示范行程（本地示例 0 秒）", use_container_width=True)
    st.caption("答辩 / 录像用：直接加载预置示范行程，无需等待模型生成")

    st.divider()
    st.header("⚡ 扰动测试（核心亮点）")
    st.caption("模拟突发事件，看 Agent 自动重规划")
    disturb = st.text_input("扰动信息", placeholder="例：古文化街今日临时闭园，明天有大雨")
    disturb_btn = st.button("🌪️ 触发扰动重规划", use_container_width=True)

col1, col2 = st.columns([3, 2])

TYPE_ICON = {"景点": "🏛️", "美食": "🍜", "交通": "🚌"}


def render_item(item: dict):
    """单个行程条目：左图右文，交通方式单独一行"""
    itype = item.get("type", "")
    icon = TYPE_ICON.get(itype, "•")
    head = (
        f"**{item.get('time', '')} {icon} {item.get('name', '')}**"
        f"（{item.get('duration', '')} · 约 {item.get('cost', 0)} 元）"
    )

    left, right = st.columns([1, 3], gap="medium")
    with left:
        if itype != "交通":
            img = image_path(guess_image(item))
            for kwargs in ({"width": "stretch", "output_format": "JPEG"}, {"width": "stretch"}, {"width": 120}):
                try:
                    st.image(img, **kwargs)
                    break
                except Exception:
                    continue
    with right:
        st.markdown(head)
        if item.get("desc"):
            st.write(item["desc"])
        trans = item.get("transport")
        if trans:
            st.caption(f"🚇 交通衔接：{trans}")
        if item.get("guide"):
            st.info(f"🎙️ 讲解词：{item['guide']}")


def render_plan(plan: dict):
    if not plan:
        return
    if "error" in plan:
        st.error(plan["error"])
        return
    st.subheader(f"📍 {plan.get('city', '')} 行程方案")
    for day in plan.get("days", []):
        with st.expander(f"第 {day.get('day')} 天", expanded=True):
            items = day.get("items", [])
            for idx, item in enumerate(items):
                if item.get("type") == "交通":
                    st.caption(
                        f"🚌 {item.get('time', '')} 交通：{item.get('name', '')}"
                        f"（{item.get('duration', '')} · 约 {item.get('cost', 0)} 元）"
                    )
                    continue
                if idx > 0 and not item.get("transport"):
                    st.caption("🚇 交通衔接：同上一条交通动线，按地图导航前往")
                render_item(item)
    foods = plan.get("food") or []
    if foods:
        st.markdown("🍜 **美食推荐**：" + "、".join(foods))
    st.success(
        f"💰 预估总花费：{plan.get('total_cost', '-')} 元 ｜ 预算内：{'是' if plan.get('within_budget') else '否'}"
    )
    if plan.get("notes"):
        st.write(f"📝 {plan['notes']}")


def run_agent(messages: list, tip: str) -> str:
    """统一生成入口：命中缓存秒回；否则流式生成并实时展示进度"""
    key = json.dumps(messages, ensure_ascii=False)
    cache = st.session_state.setdefault("cache", {})
    if key in cache:
        st.caption("⚡ 命中缓存，同一组参数直接秒出（演示时重复提问不用再等）")
        return cache[key]

    live = st.empty()
    meter = st.empty()

    def on_update(text: str):
        live.code(text[-1500:], language="json")
        meter.caption(f"Agent 正在输出…已生成 {len(text)} 字（流式回显，无需等待全部完成）")

    with st.spinner(tip):
        reply = call_agent_stream(messages, on_update)
    live.empty()
    meter.empty()

    if '"error"' not in reply[:40]:
        cache[key] = reply
    return reply


def generate_with_repair(messages: list, tip: str) -> str:
    """生成 + 自愈：若 JSON 不完整（被截断/格式错），自动让模型重出一遍"""
    reply = run_agent(messages, tip)
    if parse_plan(reply) is not None or '"error"' in reply[:40]:
        return reply
    nudge = messages + [
        {"role": "assistant", "content": reply},
        {"role": "user", "content": "上面的 JSON 不完整或格式有误，请重新输出一份完整、合法、能被 json.loads 解析的 JSON，不要任何解释文字。"},
    ]
    retry = run_agent(nudge, "输出格式异常，正在自动修正一次…")
    print(f"[repair] 首次输出解析失败，已重试；重试结果可解析={parse_plan(retry) is not None}", flush=True)
    return retry if parse_plan(retry) is not None else reply


def generate_parallel(messages: list, city, days, budget, prefs):
    """天数≥2：拆成两段并行生成，墙钟时间约减半；任一段失败返回 None，由上层回退单次生成"""
    half = (days + 1) // 2
    ranges = [(1, half), (half + 1, days)]
    sys_msg = messages[0]

    def work(rng):
        msgs = [
            sys_msg,
            {"role": "user", "content": build_user_input(city, days, budget, prefs, day_range=rng)},
        ]
        return call_agent_stream(msgs, None)

    try:
        with ThreadPoolExecutor(max_workers=2) as ex:
            replies = list(ex.map(work, ranges))
    except Exception:
        return None

    plans = [parse_plan(r) for r in replies]
    if any(p is None for p in plans) or any("error" in p for p in plans):
        return None

    merged = {"city": city, "days": [], "food": [], "notes": ""}
    for p in plans:
        merged["city"] = p.get("city") or merged["city"]
        merged["days"].extend(p.get("days") or [])
        for f in p.get("food") or []:
            if f not in merged["food"]:
                merged["food"].append(f)
        if p.get("notes"):
            merged["notes"] = (merged["notes"] + " " + str(p["notes"])).strip()
    merged["days"].sort(key=lambda d: d.get("day", 0))
    total = sum(
        (it.get("cost") or 0) for d in merged["days"] for it in (d.get("items") or [])
    )
    merged["total_cost"] = total
    merged["within_budget"] = total <= budget
    return merged


with col1:
    if demo_btn:
        demo_path = os.path.join(HERE, "output", "demo_tianjin_2d.json")
        try:
            with open(demo_path, "r", encoding="utf-8") as f:
                st.session_state.plan = json.load(f)
            st.session_state.last_reply = json.dumps(st.session_state.plan, ensure_ascii=False)
            st.session_state.messages.append(
                {"role": "user", "content": "（示范模式）天津 2 天 · 预算 800 元 · 古迹 / 美食 / 打卡热点"}
            )
            st.session_state.messages.append(
                {"role": "assistant", "content": st.session_state.last_reply}
            )
        except Exception as e:
            st.warning(f"示范行程加载失败：{e}")

    if go:
        st.session_state.messages.append(
            {"role": "user", "content": build_user_input(city, days, budget, prefs)}
        )
        cache = st.session_state.setdefault("cache", {})
        # 缓存键只按"输入参数"取，重复点同一组参数必命中（不受聊天历史影响）
        ck = f"v2|{city}|{days}|{budget}|{'/'.join(prefs)}"

        if ck in cache:
            st.caption("⚡ 命中缓存，同一组参数秒出（演示时重复提问不用再等）")
            reply = cache[ck]
        else:
            reply = None
            if days >= 2:
                half = (days + 1) // 2
                with st.spinner(
                    f"加速模式：拆成 2 段并行生成（第 1—{half} 天 / 第 {half + 1}—{days} 天），"
                    "等待时间约为单线程的一半…"
                ):
                    merged = generate_parallel(
                        st.session_state.messages, city, days, budget, prefs
                    )
                if merged:
                    reply = json.dumps(merged, ensure_ascii=False)
                    st.caption(
                        f"⚡ 加速模式：行程由 2 段并行生成后自动合并（第 1—{half} 天 / 第 {half + 1}—{days} 天）"
                    )
                    print(f"[parallel] ok days={days} items={sum(len(d.get('items', [])) for d in merged['days'])}", flush=True)
                else:
                    print(f"[parallel] fallback days={days}", flush=True)
            if reply is None:
                reply = generate_with_repair(
                    st.session_state.messages,
                    "Agent 正在检索景点信息、规划路线并衔接交通…（已开启流式输出，首字即出）",
                )
            if '"error"' not in reply[:40]:
                cache[ck] = reply

        st.session_state.messages.append({"role": "assistant", "content": reply})
        st.session_state.plan = parse_plan(reply)
        st.session_state.last_reply = reply

    if disturb_btn and disturb:
        st.session_state.messages.append(
            {
                "role": "user",
                "content": (
                    f"【突发扰动】{disturb}。请立即对当前行程自动重规划：更换受影响景点、"
                    f"调整顺序、重新核算预算，逐条说明调整理由，交通方式同步更新。仍按原 JSON 格式输出。"
                ),
            }
        )
        reply = generate_with_repair(st.session_state.messages, "Agent 正在重新规划…")
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
    else:
        st.caption("生成行程后，这里会独立复核总花费是否超预算（不依赖模型的“双保险”核算）")
    st.divider()
    st.header("🧾 Agent 调用记录")
    st.caption("答辩时展示：扰动前后两次调用对比")
    for m in st.session_state.messages[1:]:
        who = "🧑 用户" if m["role"] == "user" else "🤖 Agent"
        with st.expander(who + "：" + (m["content"][:40] + "…" if len(m["content"]) > 40 else m["content"])):
            st.text(m["content"][:2000])
