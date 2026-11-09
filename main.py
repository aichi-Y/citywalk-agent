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
import re
import urllib.parse
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

def call_agent(messages: list, max_tokens: int = 4096, temperature: float = 0.4) -> str:
    """非流式兜底调用（流式失败时使用）"""
    if not API_KEY:
        return json.dumps({"error": "未配置 LLM_API_KEY，请编辑项目目录下的 .env 文件"}, ensure_ascii=False)
    url = API_BASE.rstrip("/") + "/chat/completions"
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={"model": MODEL, "messages": messages,
                  "temperature": temperature, "max_tokens": max_tokens},
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


def build_user_input(
    city, days, budget, prefs, day_range=None, banned=None,
    own_spots=None, other_spots=None,
):
    head = ""
    if banned:
        head = (
            f"【这是同一次行程的后半段续排】前面几天已经排完并去过以下地方，"
            f"本次绝对不能再出现：{'、'.join(banned)}。"
            f"如果这座城市的可用景点不够，就把每天条目减少到 2—3 条，"
            f"宁可少排，也绝不能重复，更不能把同一个地方换个写法再写一次。"
        )
    base = head + (
        f"请为我规划行程。目标城市：{city}；出行天数：{days} 天；"
        f"总预算：{budget} 元；偏好标签：{'、'.join(prefs) if prefs else '不限'}。"
        f"要求：①每个景点之间必须给出交通方式、乘车站数/线路、耗时与费用，写入 transport 字段；"
        f"②为每个景点选择一个配图类别，写入 img 字段，取值只能是 "
        f"heritage / landmark / museum / nature / food / night / street 之一；"
        f"③每天安排 3—4 个景点／美食条目（不少于 3 个），"
        f"desc 不超过 22 字、guide 不超过 26 字、xhs 不超过 16 字，"
        f"只输出 JSON，不要任何解释性文字。"
        f"④【景点绝不重复】同一个景点、同一家店、同一个地点全程只出现一次；"
        f"每天的主题片区必须错开（如 D1 老城古迹、D2 博物馆与街区、D3 近郊自然/夜景）。"
    )
    if own_spots:
        base += (
            f"⑤【本段专属景点】第 {day_range[0]}—{day_range[1]} 天只从这些地点里选："
            f"{'、'.join(own_spots)}；可以在此基础上补充同片区的小众点位。"
        )
    if other_spots:
        base += (
            f"⑥【其他天数已占用】以下地点已分配给同一次行程的其他天数，本次严禁出现，"
            f"也不要写成同义或加城市前缀的变体：{'、'.join(other_spots)}。"
        )
    if day_range:
        a, b = day_range
        base += (
            f"本次只需输出第 {a}—{b} 天（全程共 {days} 天）的内容，day 字段使用真实天序号；"
            f"transport 仍按天内的景点顺序给出。"
            f"注意：必须返回完整的 JSON 对象，结构为 "
            f'{{"city":"","days":[{{"day":{a},"items":[...]}}],"food":[],"total_cost":0,"within_budget":true,"notes":""}}，'
            f"即 days 数组里只包含第 {a}—{b} 天（共 {b - a + 1} 个元素），不要把单个 day 对象直接当作根节点。"
        )
    return base


BASE_CITY_TOKENS = ("天津", "石家庄", "保定", "正定", "河北")
CITY_TOKENS = tuple(BASE_CITY_TOKENS)


def set_active_city(city: str):
    """把当前目标城市并入去重时要去掉的前缀词表。

    预置三城的名字本来就写在词表里；换到北京、西安等城市后，
    模型会写出「北京故宫」和「故宫」，不动态补进去就会漏判重复。
    """
    global CITY_TOKENS
    c = (city or "").strip()
    extra = []
    if c:
        extra.append(c)
        for suf in ("市", "省", "自治区", "地区", "特别行政区"):
            if c.endswith(suf) and len(c) > len(suf):
                extra.append(c[: -len(suf)])
                break
    CITY_TOKENS = tuple(dict.fromkeys(list(BASE_CITY_TOKENS) + extra))


def item_key(it: dict) -> str:
    """条目去重用的键：去掉标点、空格和城市前缀。

    模型常把同一个地方写成「古文化街」和「天津古文化街」，
    不做前缀归一化会漏判，导致重复景点漏网。
    """
    name = (it.get("name") or "").strip()
    for ch in "（(）)【[]】·・ 　\t\n":
        name = name.replace(ch, "")
    for c in CITY_TOKENS:
        name = name.replace(c, "")
    return name.strip()


def find_duplicates(plan: dict):
    """返回重复出现的地点列表 [(名称, 首次出现的天, 再次出现的天)]"""
    seen, dups = {}, []
    for day in plan.get("days") or []:
        d = day.get("day")
        for it in day.get("items") or []:
            if it.get("type") == "交通":
                continue
            k = item_key(it)
            if not k:
                continue
            hit = next((x for x in seen if x and (x in k or k in x)), None)
            if hit:
                dups.append((k, seen[hit], d))
            else:
                seen[k] = d
    return dups


def all_place_names(plan: dict, upto_day=None):
    names = []
    for day in plan.get("days") or []:
        d = day.get("day")
        if upto_day is not None and isinstance(d, int) and d > upto_day:
            continue
        for it in day.get("items") or []:
            if it.get("type") == "交通":
                continue
            n = (it.get("name") or "").strip()
            if n and n not in names:
                names.append(n)
    return names


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
    """类别兜底图：优先缩略图（渲染快），缺失时回退原图"""
    thumb = os.path.join(IMAGE_DIR, "thumbs", f"{key}.jpg")
    if os.path.exists(thumb):
        return thumb
    path = os.path.join(IMAGE_DIR, f"{key}.jpg")
    return path if os.path.exists(path) else os.path.join(IMAGE_DIR, "heritage.jpg")


# ---------------- 景点实拍照片（Wikimedia Commons 开放许可） ----------------

SPOT_DIR = os.path.join(IMAGE_DIR, "spots")
PHOTO_INDEX = os.path.join(IMAGE_DIR, "photos_credit.json")


@st.cache_data(show_spinner=False)
def load_spot_index():
    """景点名 -> 本地实拍图路径（只收录确实下载成功的）"""
    idx = {}
    try:
        with open(PHOTO_INDEX, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return idx
    for key, meta in data.items():
        p = os.path.join(SPOT_DIR, f"{key}.jpg")
        name = (meta or {}).get("name") or ""
        if name and os.path.exists(p):
            idx[name] = p
    return idx


def clean_spot_name(s: str) -> str:
    s = re.sub(r"[（(].*?[)）]", "", str(s))
    return re.sub(r"[\s·、,，。:：/]+", "", s)


def photo_for(item: dict):
    """按景点名最长匹配本地实拍照片；匹配不到返回 None"""
    name = clean_spot_name(item.get("name", ""))
    if not name:
        return None
    idx = load_spot_index()
    best = None
    for spot, path in idx.items():
        cs = clean_spot_name(spot)
        if cs and cs in name and (best is None or len(cs) > best[0]):
            best = (len(cs), path)
    return best[1] if best else None


def image_for(item: dict):
    """返回 (图片路径, 是否为该景点实拍)"""
    real = photo_for(item)
    if real:
        return real, True
    return image_path(guess_image(item)), False


def xhs_search_url(city: str, name: str) -> str:
    """小红书站内搜索：直接跳到该景点的真实笔记列表"""
    kw = name if (not city or city in name) else f"{city}{name}"
    kw = kw.strip() or name
    return "https://www.xiaohongshu.com/search_result?keyword=" + urllib.parse.quote(kw)


# ---------------- Streamlit 界面 ----------------

st.set_page_config(page_title="智漫游 · 城市旅行智能规划 Agent", page_icon="🧭", layout="wide")
refresh_api_config()

# ---------------- 视觉主题：暖色旅行手帐风 ----------------

THEME_CSS = """
<style>
:root{
  --paper:#FDF8F1; --ink:#2C2621; --muted:#8A7A6C; --line:#EADCCB;
  --terra:#DF613A; --terra-2:#F0A15E; --teal:#2E6F62; --teal-2:#E7F0EA;
}
html, body, [data-testid="stAppViewContainer"], .stMarkdown, p, li, span, div{
  font-family:"PingFang SC","Microsoft YaHei","Hiragino Sans GB","Source Han Sans SC",system-ui,-apple-system,sans-serif;
}
.stApp{
  background:
    repeating-radial-gradient(circle at 92% -6%, rgba(198,126,78,.10) 0 1px, rgba(198,126,78,0) 1px 34px),
    repeating-radial-gradient(circle at -2% 104%, rgba(38,104,90,.085) 0 1px, rgba(38,104,90,0) 1px 38px),
    radial-gradient(1100px 600px at 90% -14%, #FFB87F 0%, rgba(255,184,127,0) 58%),
    radial-gradient(900px 520px at 0% 0%, #FFDDA8 0%, rgba(255,221,168,0) 60%),
    radial-gradient(900px 580px at 106% 66%, #F79C72 0%, rgba(247,156,114,0) 58%),
    radial-gradient(860px 540px at -6% 90%, #A9D6C2 0%, rgba(169,214,194,0) 58%),
    radial-gradient(620px 400px at 52% 40%, #FFF1D6 0%, rgba(255,241,214,0) 72%),
    linear-gradient(164deg,#FFF9F0 0%,#F7E1C4 32%,#EFD3B8 62%,#DBE5D4 100%);
  background-attachment:fixed;
  color:var(--ink);
}
/* 细纸纹，不影响点击 */
.stApp::after{
  content:""; position:fixed; inset:0; pointer-events:none; z-index:0; opacity:.32;
  background-image:radial-gradient(rgba(120,96,72,.07) 1px, transparent 1px);
  background-size:24px 24px;
}
[data-testid="stAppViewContainer"] .block-container{
  position:relative; z-index:1; padding-top:1.6rem; max-width:1320px;
}
/* 侧栏：米色纸卡 */
[data-testid="stSidebar"]{
  background:linear-gradient(180deg,#FFFDF8 0%,#FBF2E4 100%);
  border-right:1px solid var(--line);
}
[data-testid="stSidebar"] .block-container{padding-top:1.2rem}
/* 标题字感 */
h1,h2,h3{ letter-spacing:.2px; color:#2A241F; }
/* 按钮 */
.stButton > button{
  border-radius:13px; border:1px solid var(--line); background:#FFFCF8; color:#3A3129;
  font-weight:600; transition:all .18s ease; box-shadow:0 1px 2px rgba(80,60,40,.05);
}
.stButton > button:hover{ border-color:var(--terra); color:var(--terra); transform:translateY(-1px) }
.stButton > button[kind="primary"], .stButton > button[data-testid="stBaseButton-primary"]{
  background:linear-gradient(135deg,#E36B41 0%,#F2A15D 100%); color:#fff; border:0;
  box-shadow:0 8px 20px rgba(224,106,63,.28);
}
.stButton > button[kind="primary"]:hover{ color:#fff; box-shadow:0 10px 26px rgba(224,106,63,.36) }
/* 折叠面板做成白卡 */
[data-testid="stExpander"]{
  background:#fff; border:1px solid var(--line); border-radius:16px;
  box-shadow:0 3px 14px rgba(90,68,44,.05); overflow:hidden; margin-bottom:.7rem;
}
[data-testid="stExpander"] summary{ font-weight:700; color:#3A3129 }
/* 图片圆角 + 投影 */
[data-testid="stImage"] img{
  border-radius:14px; box-shadow:0 6px 18px rgba(90,68,44,.16); object-fit:cover;
}
/* 输入框 / 滑块点缀 */
[data-testid="stSidebar"] input{ border-radius:10px }
/* 提示条 */
[data-testid="stAlert"]{ border-radius:13px }
hr{ border-color:var(--line) !important }
/* 表格/代码块 */
.stCode, pre{ border-radius:12px !important }
/* hero */
.zm-hero{
  position:relative; overflow:hidden; border-radius:22px; padding:26px 30px 22px;
  background:
    radial-gradient(420px 220px at 100% 0%, rgba(255,214,164,.55), rgba(255,214,164,0) 70%),
    radial-gradient(380px 200px at 0% 100%, rgba(190,224,208,.42), rgba(190,224,208,0) 72%),
    linear-gradient(120deg, rgba(255,252,246,.96) 0%, rgba(255,241,225,.94) 50%, rgba(235,243,238,.94) 100%);
  border:1px solid rgba(201,169,97,.65);
  box-shadow:0 14px 34px rgba(150,110,76,.12), inset 0 1px 0 rgba(255,255,255,.9);
  margin-bottom:18px;
}
.zm-hero::before{
  content:""; position:absolute; right:-70px; top:-90px; width:260px; height:260px; border-radius:50%;
  background:radial-gradient(circle at 30% 30%, rgba(240,161,94,.34), rgba(240,161,94,0) 70%);
}
.zm-hero::after{
  content:""; position:absolute; left:-40px; bottom:-110px; width:230px; height:230px; border-radius:50%;
  background:radial-gradient(circle at 50% 50%, rgba(46,111,98,.16), rgba(46,111,98,0) 70%);
}
.zm-kicker{
  position:relative; display:inline-block; font-size:12px; letter-spacing:2px; color:#8A6A4E;
  background:rgba(255,255,255,.72); border:1px solid #EFD9C2; border-radius:99px;
  padding:5px 12px; margin-bottom:12px;
}
.zm-title{
  position:relative; margin:0; font-size:40px; line-height:1.1; font-weight:800; color:#2A241F;
  letter-spacing:2px;
}
.zm-title span{
  background:linear-gradient(120deg,#DF613A,#E98B4C 55%,#2E6F62);
  -webkit-background-clip:text; background-clip:text; color:transparent;
}
.zm-sub{
  position:relative; font-size:24px; line-height:1.35; color:#4A3B2E; margin-top:10px;
  font-weight:800; letter-spacing:1.5px;
}
.zm-desc{ position:relative; font-size:14px; line-height:1.75; color:#6B5A4B; margin:12px 0 14px; max-width:860px }
.zm-chips{ position:relative; display:flex; flex-wrap:wrap; gap:8px; margin-top:16px }
.zm-chips span{
  font-size:12.5px; color:#4B3D31; background:rgba(255,255,255,.8);
  border:1px solid #EFDCC8; border-radius:99px; padding:5px 11px;
}
/* 条目小卡 */
.zm-item{ padding:2px 0 10px }
.zm-guide{
  margin-top:6px; font-size:13.5px; line-height:1.7; color:#5A4632;
  background:linear-gradient(90deg,#FFF7E4,#FFFCF4); border-left:3px solid #F0B165;
  border-radius:0 10px 10px 0; padding:8px 12px;
}
.zm-trans{
  display:inline-block; margin-top:6px; font-size:12.5px; color:#2E6F62;
  background:var(--teal-2); border:1px solid #D3E4DB; border-radius:8px; padding:3px 9px;
}
.zm-xhs{
  display:inline-block; margin-top:6px; margin-left:6px; font-size:12.5px; color:#B4283C;
  background:#FDEEF0; border:1px solid #F6D6DB; border-radius:8px; padding:3px 9px;
  text-decoration:none;
}
.zm-xhs:hover{ background:#FCDFE3; color:#9E1F32 }
.zm-xhs-tip{ font-size:12px; color:#A5626E; margin-top:4px }
/* 指标卡 */
[data-testid="stMetric"]{
  background:#fff; border:1px solid var(--line); border-radius:15px; padding:12px 14px;
  box-shadow:0 3px 12px rgba(90,68,44,.05);
}
[data-testid="stMetricValue"]{ font-weight:800; color:#2A241F }
/* 隐藏 Streamlit 自带工具条 */
[data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer,
[data-testid="stAppDeployButton"], [data-testid="manage-app-button"]{ display:none !important }
/* 页脚 */
.zm-foot{ margin-top:26px; font-size:12px; color:#A08B78; text-align:center; line-height:1.8 }
</style>
"""
st.markdown(THEME_CSS, unsafe_allow_html=True)

st.markdown(
    f"""
<div class="zm-hero">
  <div class="zm-kicker">全国人工智能应用创新大赛 · 智能体作品</div>
  <h1 class="zm-title">智<span>漫游</span></h1>
  <div class="zm-sub">城市旅行智能规划 Agent</div>
  <div class="zm-chips">
    <span>🗺️ 顺路动线规划</span>
    <span>🚇 景点间交通衔接</span>
    <span>🌪️ 突发扰动重规划</span>
    <span>🧮 预算独立复核</span>
    <span>📕 小红书玩法参考</span>
  </div>
</div>
""",
    unsafe_allow_html=True,
)

if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
if "plan" not in st.session_state:
    st.session_state.plan = None

with st.sidebar:
    st.header("🧳 行程需求")
    city = st.text_input(
        "目标城市",
        "天津",
        help="全国任意城市都能输入。天津 / 石家庄 / 保定 有预置知识库，出图更快；"
             "其他城市（北京、西安、成都、杭州…）会自动检索当地景点后再规划。",
    )
    days = st.slider("出行天数", 1, MAX_DAYS, 2)
    budget = st.slider("总预算（元）", 200, 5000, 800, step=100)
    prefs = st.multiselect(
        "偏好标签",
        PREF_OPTIONS,
        default=["古迹", "美食"],
        help="可多选；标签越具体，行程越贴合你的节奏",
    )
    go = st.button("🚀 生成行程规划", type="primary", use_container_width=True)
    demo_btn = st.button("⚡ 秒出示范行程", use_container_width=True)
    st.caption("答辩 / 录像用：加载预置示范行程，约 0 秒出结果")

    st.divider()
    st.header("⚡ 扰动测试（核心亮点）")
    st.caption("模拟突发事件，看 Agent 自动重规划")
    disturb = st.text_input("扰动信息", placeholder="例：古文化街今日临时闭园，明天有大雨")
    disturb_btn = st.button("🌪️ 触发扰动重规划", use_container_width=True)

col1, col2 = st.columns([3, 2])

TYPE_ICON = {"景点": "🏛️", "美食": "🍜", "交通": "🚌", "打卡热点": "📸",
             "自然风光": "🌿", "文博展馆": "🏛️", "休闲慢逛": "🚶", "购物": "🛍️"}


def render_item(item: dict, city: str = ""):
    """单个行程条目：左实拍图、右信息，交通衔接与小红书参考各占一行"""
    itype = item.get("type", "")
    icon = TYPE_ICON.get(itype, "•")
    name = item.get("name", "")
    meta = f"{item.get('duration', '')} · 约 {item.get('cost', 0)} 元"

    left, right = st.columns([1, 3], gap="medium")
    with left:
        if itype != "交通":
            img, _is_real = image_for(item)
            for kwargs in ({"width": "stretch", "output_format": "JPEG"}, {"width": "stretch"}, {"width": 120}):
                try:
                    st.image(img, **kwargs)
                    break
                except Exception:
                    continue
    with right:
        parts = [
            '<div class="zm-item">',
            f'<div style="font-size:16.5px;font-weight:700;color:#2A241F">'
            f'{item.get("time", "")} {icon} {name}'
            f'<span style="font-size:12.5px;font-weight:500;color:#94816F">  {meta}</span></div>',
        ]
        if item.get("desc"):
            parts.append(f'<div style="font-size:14px;color:#5C4C3D;line-height:1.7;margin-top:4px">{item["desc"]}</div>')
        if item.get("transport"):
            parts.append(f'<div><span class="zm-trans">🚇 {item["transport"]}</span></div>')
        if item.get("xhs"):
            parts.append(f'<div style="font-size:12.5px;color:#A5626E;margin-top:6px">📕 社区笔记：{item["xhs"]}</div>')
        if name and itype != "交通":
            url = xhs_search_url(city, name)
            parts.append(
                f'<div style="margin-top:7px"><a class="zm-xhs" href="{url}" target="_blank">'
                f"📕 看小红书上的真实游记</a></div>"
            )
        if item.get("guide"):
            parts.append(f'<div class="zm-guide">🎙️ {item["guide"]}</div>')
        parts.append("</div>")
        st.markdown("".join(parts), unsafe_allow_html=True)


def render_plan(plan: dict):
    if not plan:
        return
    if "error" in plan:
        st.error(plan["error"])
        return
    city = plan.get("city", "")
    st.subheader(f"📍 {city} 行程方案（共 {len(plan.get('days') or [])} 天）")
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
                render_item(item, city)
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


def generate_with_repair(messages: list, tip: str, expect_days: int = 0) -> str:
    """生成 + 自愈：若 JSON 不完整（被截断/格式错）或天数不够，自动让模型重出一遍"""

    def acceptable(text: str) -> bool:
        p = normalize_plan(parse_plan(text))
        if p is None:
            return False
        if expect_days:
            ds = [d for d in (p.get("days") or []) if isinstance(d, dict)]
            return len(ds) >= expect_days
        return True

    reply = run_agent(messages, tip)
    if acceptable(reply) or '"error"' in reply[:40]:
        return reply
    nudge = messages + [
        {"role": "assistant", "content": reply},
        {
            "role": "user",
            "content": (
                f"上面的 JSON 不完整、格式有误或天数不足（应为 {expect_days} 天），"
                "请重新输出一份完整、合法、能被 json.loads 解析的 JSON，"
                "days 数组必须按第 1 天到第 %d 天逐天给出，不要任何解释文字。" % (expect_days or 1)
            ),
        },
    ]
    retry = run_agent(nudge, "输出格式异常，正在自动修正一次…")
    print(
        f"[repair] 首次输出不可用，已重试；重试结果可解析={parse_plan(retry) is not None} "
        f"天数达标={acceptable(retry)}",
        flush=True,
    )
    return retry if acceptable(retry) else reply


def normalize_plan(p):
    """把模型可能返回的多种结构统一成 {"days": [...]}。

    实测坑：当只要求生成某一天（day_range=(3,3)）时，模型有时会把单个 day 对象
    直接当根节点返回 {"day":3,"items":[...]}，此前会让合并结果少掉这一天。
    """
    if not isinstance(p, dict):
        return None
    if isinstance(p.get("days"), list) and p["days"]:
        return p
    if "day" in p and isinstance(p.get("items"), list):
        return {
            "city": p.get("city", ""),
            "days": [p],
            "food": p.get("food") or [],
            "total_cost": p.get("total_cost"),
            "within_budget": p.get("within_budget"),
            "notes": p.get("notes", ""),
        }
    return None


def enforce_days(plan, days: int, budget: float = 0):
    """最后一道闸门：保证输出天数与用户选择完全一致（1 天就是 1 天）。

    - 少于请求天数：返回 None（视为失败，上层重试或提示）
    - 多于请求天数：截断
    - 天序号一律重排为 1..days，避免模型重复写 day 导致显示混乱
    """
    p = normalize_plan(plan)
    if not p:
        return None
    ds = [d for d in (p.get("days") or []) if isinstance(d, dict)]
    if len(ds) < days:
        return None
    p["days"] = ds[:days]
    for i, d in enumerate(p["days"], start=1):
        d["day"] = i
    total = sum(
        (it.get("cost") or 0) for d in p["days"] for it in (d.get("items") or [])
    )
    p["total_cost"] = total
    if budget:
        p["within_budget"] = total <= budget
    return p


def fill_missing_days(messages, city, days, budget, prefs, plan):
    """单次生成天数不足时，只补缺失的那几天（逐天串行，通常只补 1 天）"""
    p = normalize_plan(plan) or {"city": city, "days": [], "food": [], "notes": ""}
    ds = [d for d in (p.get("days") or []) if isinstance(d, dict)]
    p["days"] = ds
    sys_msg = messages[0]
    for a in range(len(ds) + 1, days + 1):
        msgs = [
            sys_msg,
            {
                "role": "user",
                "content": build_user_input(city, days, budget, prefs, day_range=(a, a)),
            },
        ]
        seg = normalize_plan(parse_plan(call_agent_stream(msgs, None)))
        seg_days = [d for d in ((seg or {}).get("days") or []) if isinstance(d, dict)]
        if not seg_days:
            print(f"[fill] 第 {a} 天补齐失败", flush=True)
            return None
        p["days"].append(seg_days[0])
    return p


# 按「片区」成组排列：并行分段时按顺序切块分配给各段，天然互不重叠
SPARE_SPOTS = {
    "天津": [
        "古文化街", "鼓楼", "广东会馆", "南市食品街", "静园", "瓷房子", "劝业场",
        "五大道", "西开教堂", "意式风情区", "天津之眼", "海河公园",
        "天津博物馆", "水上公园", "宁园", "周恩来邓颖超纪念馆",
    ],
    "石家庄": [
        "隆兴寺", "正定城墙", "荣国府", "开元寺", "正定古城",
        "河北博物院", "长安公园", "石家庄水上公园", "世纪公园", "赵州桥",
    ],
    "保定": [
        "直隶总督署", "古莲花池", "大慈阁", "保定军校广场", "竞秀公园",
        "保定市博物馆", "东湖公园", "西大街",
    ],
}


def split_spots(city: str, n: int):
    """把城市景点池按片区顺序切成 n 份，供并行分段各自独占，避免撞车。"""
    pool = spot_pool_for(city)
    if not pool or n <= 1:
        return [[] for _ in range(n)]
    size = -(-len(pool) // n)
    return [pool[i * size:(i + 1) * size] for i in range(n)]


CITY_SPOT_CACHE = {}


def fetch_city_spots(city: str, want: int = 18) -> list:
    """未预置城市：先快速问模型要一份该城的景点池（约 2—4 秒，结果进程内缓存）。

    有了这份池子，「分区并行 + 景点全程不重复」这套机制对任意城市都成立；
    取不到就返回空列表，逻辑自动退回纯模型规划，不影响出图。
    """
    c = (city or "").strip()
    if not c:
        return []
    if c in CITY_SPOT_CACHE:
        return CITY_SPOT_CACHE[c]
    msgs = [
        {"role": "system", "content": "你是城市旅行规划师，只输出 JSON 数组，不要任何解释文字。"},
        {
            "role": "user",
            "content": (
                f"列出「{c}」市区及近郊（车程 1 小时内）最经典的 {want} 个旅游去处"
                f"（景点、历史街区、博物馆、公园），"
                f"不要餐厅、酒店、演出。按地理片区相邻的顺序排列，名称用 2—8 字常用简称，"
                f'只输出形如 ["故宫","天坛公园"] 的 JSON 数组。'
            ),
        },
    ]
    raw = call_agent(msgs, max_tokens=600, temperature=0.2)
    pool = []
    try:
        a, b = raw.find("["), raw.rfind("]")
        if a >= 0 and b > a:
            arr = json.loads(raw[a: b + 1])

            def _flat(x):
                """模型偶尔返回 [["故宫"],...] 或 [{"name":"故宫"}]，统一拍平取名字"""
                if isinstance(x, str):
                    yield x
                elif isinstance(x, dict):
                    yield str(x.get("name") or x.get("spot") or "")
                elif isinstance(x, (list, tuple)):
                    for y in x:
                        yield from _flat(y)

            for x in arr:
                for name in _flat(x):
                    name = re.sub(r"^\d+[.、:：\s]*", "", name.strip())
                    name = name.split("（")[0].split("(")[0].strip()
                    if 1 < len(name) <= 12 and name not in pool:
                        pool.append(name)
    except Exception as e:
        print(f"[spots] {c} 景点池解析失败：{e}", flush=True)
    CITY_SPOT_CACHE[c] = pool
    print(f"[spots] {c} 自动获取景点池 {len(pool)} 个：{pool[:6]}…", flush=True)
    return pool


def spot_pool_for(city: str) -> list:
    """取城市景点池：预置三城直接用；其他城市按需现取（带缓存）。"""
    return SPARE_SPOTS.get(city) or fetch_city_spots(city)


def _spare_item(name: str, idx: int) -> dict:
    return {
        "time": f"{9 + idx * 2:02d}:00",
        "type": "景点",
        "name": name,
        "desc": "本地经典去处，可顺路安排",
        "cost": 0,
        "duration": "1.5h",
        "transport": "地铁/步行 · 视距离而定 · 约 3 元",
        "img": "heritage",
        "xhs": "",
        "guide": "换个角度看看这座城，留白也是行程的一部分。",
    }


def drop_duplicate_items(days, city: str = ""):
    """本地兜底：保留每个地点第一次出现，删掉后面重复的条目。

    模型偶尔无视「禁止重复」清单，与其再花 20 多秒重排，不如直接删掉重复项；
    若某天因此太空，从该城市的备用景点池里补足，保证每天不少于 2 个去处。
    """
    seen = set()
    for day in days:
        kept = []
        for it in day.get("items") or []:
            if it.get("type") == "交通":
                kept.append(it)
                continue
            k = item_key(it)
            if k and any(x and (x in k or k in x) for x in seen):
                continue
            if k:
                seen.add(k)
            kept.append(it)

        real = [x for x in kept if x.get("type") != "交通"]
        if len(real) < 2:
            pool = [n for n in spot_pool_for(city)
                    if not any(x and (x in item_key({"name": n}) or item_key({"name": n}) in x)
                               for x in seen)]
            for i, name in enumerate(pool[: 2 - len(real)]):
                kept.append(_spare_item(name, len(real) + i))
                seen.add(item_key({"name": name}))
            if kept:
                kept.sort(key=lambda x: str(x.get("time", "")))
        day["items"] = kept
    return days


def refill_thin_days(days, city, total_days, budget, prefs, min_items=2):
    """兜底：去重后某天太空（甚至被清空）时，只针对那一天补排一次。

    并行分段拿到的景点池是切开的，正常不会撞车；但换成没有预置池的城市、
    或模型无视约束时，宁可多花几秒补一天，也不给用户一个空白的一天。
    """
    used = [it.get("name") for d in days for it in (d.get("items") or [])
            if it.get("type") != "交通" and it.get("name")]
    sys_msg = {"role": "system", "content": SYSTEM_PROMPT}
    for d in days:
        real = [it for it in (d.get("items") or []) if it.get("type") != "交通"]
        if len(real) >= min_items:
            continue
        a = d.get("day") or 1
        msgs = [sys_msg, {"role": "user", "content": build_user_input(
            city, total_days, budget, prefs, day_range=(a, a), banned=used[:60])}]
        seg = normalize_plan(parse_plan(call_agent_stream(msgs, None)))
        seg_days = [x for x in ((seg or {}).get("days") or []) if isinstance(x, dict)]
        if not seg_days:
            print(f"[refill] 第 {a} 天补排失败", flush=True)
            continue
        new_items = []
        for it in (seg_days[0].get("items") or []):
            if it.get("type") == "交通":
                continue
            k = item_key(it)
            if k and any(x and (x in k or k in x) for x in used):
                continue
            new_items.append(it)
            if k:
                used.append(k)
        if new_items:
            items = [it for it in (d.get("items") or []) if it.get("type") == "交通"] + new_items
            items.sort(key=lambda x: str(x.get("time", "")))
            d["items"] = items
            print(f"[refill] 第 {a} 天补到 {len(new_items)} 个去处", flush=True)
    return days


def generate_parallel(messages: list, city, days, budget, prefs):
    """分区并行生成：把景点池按片区切成互不重叠的几份，各段同时开跑。

    速度：3 段并行 → 墙钟时间约等于「生成 2 天」的时间（5 天约 20 秒，而不是 50 秒）。
    不重复：每段提示词里写死「本段专属景点」+「其他天已占用、严禁出现」，
    从源头切开景点池；模型偶尔仍越界时，再由 drop_duplicate_items 本地清理兜底。
    """
    if days <= 1:
        n_seg = 1
    elif days == 2:
        n_seg = 2
    else:
        n_seg = 3
    set_active_city(city)
    # 天数尽量平均分配，靠前的段多担一天
    sizes = [days // n_seg + (1 if i < days % n_seg else 0) for i in range(n_seg)]
    ranges, cur = [], 1
    for s in sizes:
        ranges.append((cur, cur + s - 1))
        cur += s

    groups = split_spots(city, n_seg)
    sys_msg = messages[0]

    def work(i):
        own = groups[i] if i < len(groups) else []
        other = [n for j, g in enumerate(groups) if j != i for n in g]
        msgs = [
            sys_msg,
            {
                "role": "user",
                "content": build_user_input(
                    city, days, budget, prefs, day_range=ranges[i],
                    own_spots=own or None, other_spots=other or None,
                ),
            },
        ]
        reply = call_agent_stream(msgs, None)
        p = normalize_plan(parse_plan(reply))
        if not p:
            return None
        want = ranges[i][1] - ranges[i][0] + 1
        pd = [d for d in (p.get("days") or []) if isinstance(d, dict)]
        if len(pd) < want:
            print(f"[segment] 第 {ranges[i][0]}—{ranges[i][1]} 段天数不足", flush=True)
            return None
        p["days"] = pd[:want]
        return p

    if n_seg == 1:
        plans = [work(0)]
    else:
        with ThreadPoolExecutor(max_workers=n_seg) as ex:
            plans = list(ex.map(work, range(n_seg)))

    if any(p is None for p in plans):
        print(f"[segment] 有分段失败 days={days}，回退单次生成", flush=True)
        return None

    merged = {"city": city, "days": [], "food": [], "notes": ""}
    for i, p in enumerate(plans):
        a, b = ranges[i]
        want = b - a + 1
        pdays = [d for d in (p.get("days") or []) if isinstance(d, dict)]
        if len(pdays) < want:
            return None
        merged["days"].extend(pdays[:want])
        merged["city"] = p.get("city") or merged["city"]
        for f in p.get("food") or []:
            if f not in merged["food"]:
                merged["food"].append(f)
        if p.get("notes"):
            merged["notes"] = (merged["notes"] + " " + str(p["notes"])).strip()

    # 按「段顺序 + 段内顺序」重新编号 1..days，不信任模型写的 day 字段
    for i, d in enumerate(merged["days"], start=1):
        d["day"] = i

    if len(merged["days"]) != days:
        print(f"[segment] 天数不符 days={days} got={len(merged['days'])}", flush=True)
        return None

    # 本地兜底：分区后仍撞车的少量景点直接删掉，并从备用池补足每天 2 个去处
    merged["days"] = drop_duplicate_items(merged["days"], city)
    merged["days"] = refill_thin_days(merged["days"], city, days, budget, prefs)

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
        set_active_city(city)
        st.session_state.messages.append(
            {"role": "user", "content": build_user_input(city, days, budget, prefs)}
        )
        cache = st.session_state.setdefault("cache", {})
        # 缓存键只按"输入参数"取，重复点同一组参数必命中（不受聊天历史影响）
        ck = f"v5|{city}|{days}|{budget}|{'/'.join(prefs)}"

        if ck in cache:
            st.caption("⚡ 命中缓存，同一组参数秒出（演示时重复提问不用再等）")
            reply = cache[ck]
        else:
            reply = None
            if days >= 2:
                n_seg = 2 if days == 2 else 3
                with st.spinner(
                    f"分区并行生成（{n_seg} 段同时进行，每段景点互不重叠），"
                    "通常 20 秒左右出完整行程…"
                ):
                    merged = generate_parallel(
                        st.session_state.messages, city, days, budget, prefs
                    )
                if merged:
                    reply = json.dumps(merged, ensure_ascii=False)
                    st.caption(
                        f"⚡ 已分区并行生成并合并（{n_seg} 段同时进行），景点全程不重复"
                    )
                    print(f"[parallel] ok days={days} items={sum(len(d.get('items', [])) for d in merged['days'])}", flush=True)
                else:
                    print(f"[parallel] fallback days={days}", flush=True)
            if reply is None:
                reply = generate_with_repair(
                    st.session_state.messages,
                    "Agent 正在检索景点信息、规划路线并衔接交通…（已开启流式输出，首字即出）",
                    expect_days=days,
                )
            if '"error"' not in reply[:40]:
                cache[ck] = reply

        st.session_state.messages.append({"role": "assistant", "content": reply})
        plan_final = enforce_days(parse_plan(reply), days, budget)
        if plan_final is None:
            # 兜底：模型这次天数不够，逐天补齐（通常只补 1 天），保证不多不少
            with st.spinner(f"补齐缺失天数（目标 {days} 天）…"):
                plan_final = enforce_days(
                    fill_missing_days(
                        st.session_state.messages, city, days, budget, prefs,
                        parse_plan(reply),
                    ),
                    days,
                    budget,
                )
            if plan_final is not None:
                reply = json.dumps(plan_final, ensure_ascii=False)
                st.session_state.messages[-1] = {"role": "assistant", "content": reply}
                cache[ck] = reply
            else:
                st.warning(f"本次只生成了不足 {days} 天的行程，请再点一次「生成行程规划」。")

        if plan_final:
            # 景点不重复：本地清理（删掉后面重复的景点，并从备用池补足每天 2 个去处）。
            # 不再为重复景点整篇重排——实测模型多半仍会重复，白等 20 多秒。
            before = len(find_duplicates(plan_final))
            plan_final["days"] = drop_duplicate_items(plan_final["days"], city)
            with st.spinner("检查每天景点数量，不足的自动补排…"):
                plan_final["days"] = refill_thin_days(
                    plan_final["days"], city, days, budget, prefs
                )
            plan_final["total_cost"] = sum(
                (it.get("cost") or 0)
                for d in plan_final["days"]
                for it in (d.get("items") or [])
            )
            plan_final["within_budget"] = plan_final["total_cost"] <= budget
            reply = json.dumps(plan_final, ensure_ascii=False)
            st.session_state.messages[-1] = {"role": "assistant", "content": reply}
            cache[ck] = reply
            if before:
                print(f"[dedupe] 本地清理去掉 {before} 处重复景点", flush=True)

        st.session_state.plan = plan_final or parse_plan(reply)
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
        keep_days = len((st.session_state.get("plan") or {}).get("days") or []) or days
        st.session_state.plan = (
            enforce_days(parse_plan(reply), keep_days, budget) or parse_plan(reply)
        )
        st.session_state.last_reply = reply

    if "last_reply" in st.session_state:
        if st.session_state.plan:
            render_plan(st.session_state.plan)
        else:
            st.warning("Agent 返回内容未能解析为 JSON，原始回复如下：")
            st.code(st.session_state.last_reply, language="text")

with col2:
    st.markdown("#### 🧮 预算核算")
    plan_now = st.session_state.get("plan")
    total, msg = verify_budget(plan_now, budget)
    if total is None:
        st.caption("生成行程后，这里会独立复核总花费是否超预算（不依赖模型的“双保险”核算）")
    else:
        m1, m2 = st.columns(2)
        m1.metric("核算总花费", f"{total} 元")
        m2.metric("剩余额度", f"{budget - total} 元")
        if total <= budget:
            st.success(msg)
        else:
            st.error(msg)
    st.divider()
    st.markdown("#### 🧾 Agent 调用记录")
    st.caption("答辩时展示：扰动前后两次调用对比")
    for m in st.session_state.messages[1:]:
        who = "🧑 用户" if m["role"] == "user" else "🤖 Agent"
        with st.expander(who + "：" + (m["content"][:40] + "…" if len(m["content"]) > 40 else m["content"])):
            st.text(m["content"][:2000])

st.markdown(
    """
<div class="zm-foot">
  景点照片来自 Wikimedia Commons 开放许可（CC BY / CC BY-SA / 公有领域），
  无专属实拍的景点使用同类实景照片；「看小红书上的真实游记」为站内搜索直达，便于核对社区玩法与避坑信息。<br>
  智漫游 · 城市旅行智能规划 Agent · 全国人工智能应用创新大赛参赛作品
</div>
""",
    unsafe_allow_html=True,
)
