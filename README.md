# 小红书博主图片批量下载工具

参考 MediaCrawler 的核心做法实现的轻量工具：Playwright 打开小红书网页 →
扫码登录 → 调用 web API 翻页抓取指定博主的全部笔记 → 下载图片和视频。

## 功能

- 扫码登录，登录态保存到本地（`xhs_cookies.json`），之后无需重复扫码
- 支持粘贴博主主页链接、单篇帖子链接（`/explore/...`）、或直接输入博主 ID
- 下载全部笔记的图片 + 视频（无水印原图）
- 每篇笔记一个文件夹：`downloads/<博主昵称>/<笔记标题>/`
- 随机延时 1~3 秒防风控；已下载的文件自动跳过，支持断点续传
- cookie 失效时自动重新弹出浏览器扫码

## 安装（在新电脑上）

需要 Python 3.9+。在项目文件夹打开命令行：

```bash
pip install -r requirements.txt
playwright install chromium
```

## 使用

双击 `start.bat`，或运行：

```bash
python xhs_dl.py
```

1. 首次运行会弹出浏览器，用手机小红书 App 扫码登录，登录成功后浏览器自动关闭
2. 按提示粘贴博主主页链接（`https://www.xiaohongshu.com/user/profile/xxxxxxxx`）或直接输入博主 ID
3. 等待下载完成，结果保存在 `downloads/` 目录

## 注意

- 请求间有随机延时，博主笔记多时耗时较长，属正常现象
- 仅供个人学习/备份用途，图片视频版权归博主所有，请勿商用或二次传播
- 抓取过快可能触发风控导致账号异常，请勿自行调低延时
