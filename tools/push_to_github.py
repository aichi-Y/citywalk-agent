# -*- coding: utf-8 -*-
"""
用 GitHub Contents API 批量推送文件（git push 走 443 被代理拦截时的替代方案）。
凭据从 git 凭据管理器读取，不落盘。
用法：python tools/push_to_github.py "提交说明"
"""
import base64
import glob
import json
import subprocess
import sys
import time
import urllib.request

REPO = "aichi-Y/citywalk-agent"
BRANCH = "main"
API = f"https://api.github.com/repos/{REPO}"
UA = "ZhiManyou-Pusher/1.0"


def token():
    p = subprocess.run(
        ["bash", "-lc", 'printf "protocol=https\\nhost=github.com\\n\\n" | git credential fill'],
        capture_output=True, text=True,
    )
    for line in p.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    sys.exit("未能读取 GitHub 凭据")


TOK = token()
HDR = {
    "Authorization": f"Bearer {TOK}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": UA,
}


def req(method, url, data=None):
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(url, data=body, headers=HDR, method=method)
    with urllib.request.urlopen(r, timeout=120) as resp:
        return json.loads(resp.read().decode() or "{}")


def collect():
    files = ["main.py", "agent_config.json", "README.md",
             "output/demo_tianjin_2d.json", "static/images/photos_credit.json"]
    files += sorted(glob.glob("static/images/spots/*.jpg"))
    files += sorted(glob.glob("static/images/thumbs/*.jpg"))
    files += sorted(glob.glob("tools/*.py"))
    return [f.replace("\\", "/") for f in files]


def main():
    msg = sys.argv[1] if len(sys.argv) > 1 else "更新"
    ok = skip = fail = 0
    for f in collect():
        try:
            content = open(f, "rb").read()
            sha = None
            try:
                cur = req("GET", f"{API}/contents/{f}?ref={BRANCH}")
                sha = cur.get("sha")
                if cur.get("content") and base64.b64decode(cur["content"]) == content:
                    print(f"= {f} 无变化", flush=True)
                    skip += 1
                    continue
            except Exception:
                sha = None
            payload = {"message": msg, "content": base64.b64encode(content).decode(), "branch": BRANCH}
            if sha:
                payload["sha"] = sha
            req("PUT", f"{API}/contents/{f}", payload)
            print(f"✓ {f} ({len(content)//1024}KB)", flush=True)
            ok += 1
        except Exception as e:
            print(f"✗ {f} {str(e)[:70]}", flush=True)
            fail += 1
            time.sleep(2)
        time.sleep(0.35)
    print(f"\n完成：更新 {ok}，跳过 {skip}，失败 {fail}")


if __name__ == "__main__":
    main()
