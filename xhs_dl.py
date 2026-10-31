"""小红书博主图片/视频批量下载工具.

用法:
    python xhs_dl.py
首次运行会弹出浏览器扫码登录, 登录态保存在 xhs_state.json, 之后无需重复扫码.
"""

import json
import os
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import httpx
from playwright.sync_api import sync_playwright, Response, BrowserContext, Page

# 打包成 exe 后 __file__ 指向临时解压目录, 数据必须放在 exe 旁边, 否则
# 登录态和下载文件会随临时目录一起被删.
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "xhs_state.json"
DOWNLOAD_DIR = BASE_DIR / "downloads"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
HOME = "https://www.xiaohongshu.com"


def _ensure_browser() -> None:
    """优先使用随包分发的浏览器, 其次系统已装的 Edge/Chrome, 避免打包 Chromium."""
    bundled = BASE_DIR / "ms-browsers"
    if (bundled / "chromium").is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled)
        return
    # 无随包浏览器时, 尝试系统 Edge / Chrome (Win10/11 预装 Edge)
    for channel in ("msedge", "chrome"):
        try:
            exe = _channel_executable(channel)
            if exe and Path(exe).is_file():
                os.environ["XHS_CHANNEL"] = channel
                return
        except Exception:
            pass


def _channel_executable(channel: str) -> str:
    import winreg
    key_path = (r"SOFTWARE\Microsoft\Windows\CurrentVersion"
                r"\App Paths\%s.exe" % ("msedge" if channel == "msedge" else "chrome"))
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
        return winreg.QueryValueEx(key, "")[0]


def rand_sleep(a: float = 1.0, b: float = 3.0) -> None:
    time.sleep(random.uniform(a, b))


def extract_note_id(text: str) -> str | None:
    """识别单篇帖子链接, 返回 note_id; 不是帖子链接则返回 None."""
    m = re.search(r"/explore/([0-9a-f]+)", text) or \
        re.search(r"/discovery/item/([0-9a-f]+)", text)
    if m:
        return m.group(1)
    m = re.search(r"xhslink\.(cn|com)/\S+", text)
    if m:
        try:
            url = text if text.startswith("http") else "https://" + text
            r = httpx.get(url, follow_redirects=True, timeout=15,
                         headers={"User-Agent": UA})
            from urllib.parse import unquote
            final = unquote(str(r.url))
            m2 = re.search(r"/explore/([0-9a-f]+)", final) or \
                re.search(r"/discovery/item/([0-9a-f]+)", final)
            if m2:
                return m2.group(1)
        except httpx.HTTPError:
            pass
    return None


def extract_user_id(text: str) -> str:
    """从主页链接或纯 ID 中提取 user_id, 短链接自动跟随跳转."""
    text = text.strip()
    m = re.search(r"user/profile/([0-9a-f]+)", text)
    if m:
        return m.group(1)
    if re.search(r"xhslink\.(cn|com)/\S+", text):
        print("检测到分享短链接, 正在解析...")
        try:
            r = httpx.get(text if text.startswith("http") else "https://" + text,
                          follow_redirects=True, timeout=15,
                          headers={"User-Agent": UA})
            from urllib.parse import unquote
            final = unquote(str(r.url))
            m = re.search(r"user/profile/([0-9a-f]+)", final)
            if not m:
                m = re.search(r'"userId":"?([0-9a-f]+)"?', r.text)
            if m:
                return m.group(1)
        except httpx.HTTPError as e:
            print(f"短链接解析失败: {e}")
        print("无法从短链接解析出博主 ID, 请在浏览器打开短链后复制完整主页链接.")
        sys.exit(1)
    if re.fullmatch(r"[0-9a-f]{16,}", text):
        return text
    print("无法识别博主链接或 ID: %r" % text)
    sys.exit(1)


def open_context(p, headless: bool = False) -> BrowserContext:
    channel = os.environ.get("XHS_CHANNEL")
    if channel:
        browser = p.chromium.launch(headless=headless, channel=channel)
    else:
        browser = p.chromium.launch(headless=headless)
    if STATE_FILE.exists():
        context = browser.new_context(storage_state=str(STATE_FILE), user_agent=UA)
    else:
        context = browser.new_context(user_agent=UA)
    return context


def is_logged_in(context: BrowserContext, page: Page) -> bool:
    """打开首页检查 user/me 接口, guest 为 True 视为未登录."""
    result = {"ok": False}
    def on_resp(resp: Response):
        if "/api/sns/web/v2/user/me" in resp.url:
            try:
                result["ok"] = not resp.json().get("data", {}).get("guest", True)
            except Exception:
                pass
    page.on("response", on_resp)
    page.goto(HOME)
    deadline = time.time() + 15
    while not result["ok"] and time.time() < deadline:
        time.sleep(0.5)
    page.remove_listener("response", on_resp)
    return result["ok"]


def login(context: BrowserContext, page: Page) -> None:
    """在已打开的页面上等待扫码登录, 成功后保存登录态.

    被动监听页面的 user/me 接口: 只有它返回非 guest 才算登录完成.
    期间不刷新/不跳转, 避免打断扫码后的短信/滑块验证流程.
    (cookie 里的 web_session 在扫码后就会产生, 不能作为登录完成依据)
    """
    print("请在浏览器中用手机小红书 App 扫码登录...")
    if "user/me" not in page.url and page.url.rstrip("/") != HOME:
        page.goto(HOME)
    result = {"ok": False}

    def on_resp(resp: Response):
        if "/api/sns/web/v2/user/me" in resp.url:
            try:
                result["ok"] = not resp.json().get("data", {}).get("guest", True)
            except Exception:
                pass

    page.on("response", on_resp)
    deadline = time.time() + 300
    while time.time() < deadline and not result["ok"]:
        time.sleep(1)
    page.remove_listener("response", on_resp)
    if not result["ok"]:
        print("5 分钟内未完成登录, 退出.")
        sys.exit(1)
    context.storage_state(path=str(STATE_FILE))
    print("登录成功, 登录态已保存到 xhs_state.json")


def crawl_user_notes(page: Page, user_id: str) -> list[dict]:
    """打开博主主页, 自动滚动加载全部笔记, 返回笔记列表."""
    notes: list[dict] = []
    seen: set[str] = set()
    payload: dict = {}

    def on_response(resp: Response):
        if "api/sns/web/v1/user_posted" in resp.url:
            try:
                data = resp.json()
            except Exception:
                return
            payload.update(data.get("data", {}))
            for item in data.get("data", {}).get("notes", []):
                nid = item.get("note_id")
                if nid and nid not in seen:
                    seen.add(nid)
                    notes.append(item)

    page.on("response", on_response)
    page.goto(f"{HOME}/user/profile/{user_id}")
    # 等 user_posted 接口返回数据 (不依赖 DOM 结构, 小红书 class 常变)
    deadline = time.time() + 30
    while not notes and time.time() < deadline:
        time.sleep(0.5)
    if not notes:
        title = page.title()
        print(f"未获取到笔记列表. 页面标题: {title}")
        if "扫码" in title or "登录" in title:
            print("登录态可能已失效, 删除 xhs_state.json 后重试.")
        sys.exit(1)
    # 反复滚动直到接口返回 has_more == False 且无新内容
    same_rounds = 0
    last_count = 0
    while same_rounds < 3:
        page.mouse.wheel(0, 4000)
        time.sleep(1.5)
        if not payload.get("has_more", False) and len(notes) == last_count:
            same_rounds += 1
        else:
            same_rounds = 0
            last_count = len(notes)
    page.remove_listener("response", on_response)
    nickname = page.evaluate(
        "() => document.querySelector('.user-name, .username')?.innerText || "
        "document.title")
    return notes, nickname


def crawl_note_media(page: Page, note_id: str) -> dict:
    """打开笔记详情页, 从 feed 接口响应中提取图片和视频地址."""
    media: dict = {"images": [], "video": None}
    got = {"done": False}

    def on_response(resp: Response):
        if "api/sns/web/v1/feed" in resp.url and not got["done"]:
            try:
                data = resp.json()
            except Exception:
                return
            items = data.get("data", {}).get("items") or []
            for it in items:
                if it.get("note_id") != note_id:
                    continue
                for img in it.get("image_list", []):
                    url = (img.get("url_default")
                           or img.get("info_list", [{}])[-1].get("url"))
                    if url and url.startswith("//"):
                        url = "https:" + url
                    if url:
                        media["images"].append(url)
                vid = it.get("video", {}).get("media", {}).get("stream", {})
                for codec in ("h264", "h265", "av1"):
                    s = vid.get(codec)
                    if s:
                        media["video"] = s[0].get("master_url")
                        break
                got["done"] = True

    page.on("response", on_response)
    try:
        page.goto(f"{HOME}/explore/{note_id}", wait_until="domcontentloaded")
        deadline = time.time() + 15
        while not got["done"] and time.time() < deadline:
            time.sleep(0.5)
    finally:
        page.remove_listener("response", on_response)
    return media


def sanitize(name: str, fallback: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\n\r\t]', "_", name).strip(" .")
    return name[:60] or fallback


def download(client: httpx.Client, urls: list[str], folder: Path) -> None:
    for i, url in enumerate(urls, 1):
        ext = Path(urlparse(url).path).suffix or ".jpg"
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".mp4"):
            ext = ".jpg"
        path = folder / f"{i:02d}{ext}"
        if path.exists() and path.stat().st_size > 0:
            continue
        tmp = path.with_suffix(path.suffix + ".part")
        with client.stream("GET", url) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes(1 << 16):
                    f.write(chunk)
        tmp.rename(path)
        print(f"    已下载 {path.name}")


def _log(*args) -> None:
    """所有输出同时写入 exe 旁的 xhs_log.txt, 便于排查问题."""
    line = " ".join(str(a) for a in args)
    print(line)
    try:
        with open(BASE_DIR / "xhs_log.txt", "a", encoding="utf-8") as f:
            import datetime
            f.write(f"[{datetime.datetime.now():%m-%d %H:%M:%S}] {line}\n")
    except Exception:
        pass


def run(target: str) -> None:
    """核心下载流程: target 为主页链接/帖子链接/博主ID. 供命令行与 GUI 共用."""
    _ensure_browser()
    _log(f"=== 开始运行, BASE_DIR={BASE_DIR}, 登录态文件存在={STATE_FILE.exists()}")
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    with sync_playwright() as p:
        context = open_context(p)
        page = context.new_page()
        if not is_logged_in(context, page):
            login(context, page)
        else:
            _log("已有登录态, 跳过扫码.")

        headers = {"Referer": HOME, "User-Agent": UA}
        with httpx.Client(headers=headers, follow_redirects=True,
                          timeout=60) as client:
            note_id = extract_note_id(target)
            if note_id:
                _log(f"检测到单篇帖子链接: {note_id}")
                media = crawl_note_media(page, note_id)
                if not media["images"] and not media["video"]:
                    _log("未获取到媒体内容.")
                    sys.exit(1)
                folder = DOWNLOAD_DIR / f"note_{note_id}"
                folder.mkdir(parents=True, exist_ok=True)
                if media["images"]:
                    download(client, media["images"], folder)
                if media["video"]:
                    download(client, [media["video"]], folder)
            else:
                user_id = extract_user_id(target)
                _log("正在获取博主全部笔记列表...")
                notes, nickname = crawl_user_notes(page, user_id)
                nickname = sanitize(str(nickname).strip().split("\n")[0], user_id)
                _log(f"博主 [{nickname}] 共 {len(notes)} 篇笔记")

                for idx, note in enumerate(notes, 1):
                    nid = note["note_id"]
                    title = sanitize(note.get("display_title") or "", nid)
                    folder = DOWNLOAD_DIR / nickname / title
                    _log(f"[{idx}/{len(notes)}] {title}")
                    media = crawl_note_media(page, nid)
                    if not media["images"] and not media["video"]:
                        _log("    未获取到媒体内容, 跳过")
                        continue
                    folder.mkdir(parents=True, exist_ok=True)
                    if media["images"]:
                        download(client, media["images"], folder)
                    if media["video"]:
                        download(client, [media["video"]], folder)
                    rand_sleep()

        context.close()
    _log("完成. 文件保存在 downloads/ 目录.")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1:
        target = " ".join(sys.argv[1:])
    else:
        target = input("请粘贴博主主页链接、帖子链接或博主 ID: ")
    run(target)


if __name__ == "__main__":
    main()
