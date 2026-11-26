# 拾红

检索小红书和抖音上的公开笔记、视频与评论。先给出带原文链接的搜索结果，再按这些材料写一份有编号出处的中文分析。

这不是批量爬虫。默认每次每个平台最多 8 条内容、每条最多 5 条一级评论，平台之间会停几秒。没有登录配置时自动使用演示数据，不访问任何平台。

## 合规与使用边界

- 只处理你本人登录后能够看到的公开内容。不要采集私信、好友、通讯录、手机号或精确位置。
- 遵守小红书、抖音的平台条款和适用法律。控制频率，不要做大规模抓取。
- 平台如果要求验证码、扫码或短信确认，请你自己在浏览器里完成。本程序不识别验证码，也不绕过登录或风控。
- Cookie 等于登录凭证。只放在本机 `.env`，不要发到聊天、截图或仓库里。
- MediaCrawler 是可选的外部工具，平台改版后可能失效。它有自己的非商业学习许可，使用前请阅读它的 README 和免责声明。
- 演示数据里的店名、评测、价格和互动数字都是编写的，不能当成真实推荐。

## 环境

- Python 3.11 或更新版本
- 演示模式不需要浏览器、Cookie 或大模型密钥

## 安装与运行

```shell
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
streamlit run app.py --server.port 8765
```

浏览器打开 `http://127.0.0.1:8765`。

页面上可以输入问题，选择小红书、抖音或全部，以及不限 / 近 7 天 / 近 30 天 / 近 90 天。点「检索」后看原始结果；点「生成深度分析」得到带来源编号的报告。检索记录保存在 SQLite（默认 `data/hongsearch.db`）。

没有配置 `LLM_API_KEY` 时，原始结果仍然可用。深度分析会改成摘录整理，并在报告里说明没有调用模型。

## 演示模式与实时模式

侧边栏有三种模式：

| 模式 | 行为 |
| --- | --- |
| 自动 | 检测到可用的 MediaCrawler 和登录方式时走实时，否则用演示数据 |
| 演示 | 只使用内置样本，不访问平台 |
| 实时 | 调用本机的 MediaCrawler。缺配置时页面会写出缺什么 |

演示问题可以直接点：

- 广州有哪些值得推荐的日料店？
- 最近 RTX 5070 Ti 有哪些用户反馈？
- 小红书上大家对戴森吹风机的评价是什么？
- 最近一个月关于 PQC 的讨论有哪些？
- 帮我找出抖音上城市骑行的热门视频

问题里如果写了「小红书」或「抖音」，并且平台选的是「全部」，会只检索提到的那一边。写了「最近一个月」且时间选的是「不限」时，会按近 30 天过滤。界面上的明确选择优先。

## 历史缓存

一次成功的检索会把结果快照进 SQLite。下次同样方向的问题会先在历史里找。相关结果达到 `MIN_CACHE_RESULTS`（默认 3 条）就直接使用，并标成「历史缓存」。

不够时才采集。勾选「忽略缓存，重新检索」会跳过这一步。实时模式下，完全相同的检索在 10 分钟内会直接复用上一次结果，避免误触重复打开浏览器。

## 检索进行时

点「检索」以后，采集在后台进行，页面不会停在「正在检索」。主栏按步骤显示进度：理解问题、查看缓存、连接 Chrome 远程调试（默认 `127.0.0.1:9222`）、登录、写入笔记和评论。筛选、示例、历史和「取消检索」都可以继续点。

Chrome 若弹出允许调试的确认框，需要在大约 60 秒内点接受。端口还没开时，页面会提示去 `chrome://inspect/#remote-debugging` 勾选远程调试。扫码登录时，页面会提示用小红书或抖音 App 扫描浏览器里的二维码。验证码仍要你自己完成。

每个平台默认最多跑 `CRAWLER_TIMEOUT_SEC`（240 秒）。到时间会停掉这次任务，但运行目录里已经写好的笔记和评论会读回来，并标明这是未完成的结果。进程异常退出时也一样，不会把已经落盘的内容整段丢掉。

## 配置大模型

`.env` 使用 OpenAI 兼容接口，可以接 OpenAI、DeepSeek 或其他兼容服务：

```shell
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=你的密钥
LLM_MODEL=deepseek-chat
```

地址可以写成带 `/v1` 的根路径。程序会请求 `/v1/chat/completions`。密钥不会写入数据库，也不会放进采集命令。修改 `.env` 后，点侧边栏「重新加载 .env」，或重启程序。已经导出的同名环境变量优先于文件里的值；点重新加载后，以文件为准。

深度分析只会看到当前结果列表中的标题、正文摘录、作者显示名、互动数字和评论。模型被要求用 `[1]` 这类编号回到列表，程序会再附上一份参考来源。

## 配置实时检索

实时检索不内置平台客户端。它会启动你本机已经装好的 [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler)，读取它写出的 JSONL，再映射成本程序的结果。请把它克隆到本仓库外面。

```shell
git clone https://github.com/NanmiCoder/MediaCrawler.git ~/MediaCrawler
cd ~/MediaCrawler
uv sync
```

也可以按它自己的 README 使用 `venv` 和 `pip install -r requirements.txt`。抖音部分通常还需要 Node.js 16 或更新版本。它默认通过 Chrome 远程调试复用你已经打开的浏览器；请按它的说明在本机 Chrome 中打开远程调试。不要用无头模式去避开页面确认。

然后在本项目的 `.env` 里写上：

```shell
APP_MODE=auto
MEDIACRAWLER_HOME=/你的路径/MediaCrawler
MEDIACRAWLER_PYTHON=/你的路径/MediaCrawler/.venv/bin/python
# 如果那边用 uv，也可以写成：
# MEDIACRAWLER_PYTHON=uv run python

LOGIN_TYPE=cookie
XHS_COOKIES=
DY_COOKIES=
MAX_NOTES=8
MAX_COMMENTS=5
```

`MEDIACRAWLER_PYTHON` 留空时，会优先用 `MEDIACRAWLER_HOME/.venv/bin/python`。

### Cookie 怎么填

1. 用你自己的账号在浏览器里登录小红书或抖音。
2. 打开开发者工具，在已登录页面的请求头里复制 Cookie 字符串。
3. 贴进 `.env` 的 `XHS_COOKIES` 或 `DY_COOKIES`。不要加引号，也不要写进命令行。
4. 重新加载配置。页面只显示「已填写 / 未填写」，不会把 Cookie 展示出来。

只配了一个平台时，另一个平台会被跳过并给出说明。

不想粘贴 Cookie 时，把 `LOGIN_TYPE` 设为 `qrcode`。检索开始后，在弹出的浏览器里用对应 App 扫码，并自行完成平台要求的验证。这需要本机有图形界面。扫码失败或要求人工验证时，停下来自己处理，不要改程序去绕过。

程序会把 Cookie 放进子进程的环境变量，再交给 MediaCrawler 的配置对象。它不会出现在命令行参数里。采集日志如果意外包含这段字符串，会先打码再显示。用户 ID、头像、IP 属地即使出现在原始文件里，也不会写入本程序的数据库；作者只保留平台给出的显示名。

实时检索会让 MediaCrawler 关闭代理、关闭媒体下载、关闭二级评论，并把并发限制为 1。单次条数在程序内再封顶一次：内容最多 20 条，一级评论最多 10 条。

## 测试

```shell
pytest
```

## 项目结构

```text
app.py                          Streamlit 界面
hongsearch/text.py              问句里的关键词、平台和时间
hongsearch/service.py           缓存、采集、排序
hongsearch/analysis.py          摘录整理和模型分析
hongsearch/collectors/mock.py   演示数据
hongsearch/collectors/mediacrawler.py
                                可选的 MediaCrawler 适配器
data/hongsearch.db              本地检索历史
```

## 已知限制

- 演示模式的链接是示例，打不开真实笔记。
- 实时模式依赖本机 Chrome、MediaCrawler 和你的登录态。这台没有图形界面的环境只能看演示。
- 平台页面、接口或 MediaCrawler 的输出字段变化后，实时检索可能变成空结果或报错。
- 没有向量检索。相关度来自关键词匹配，关键词不全时结果会少。
- 模型可能写得比材料更满。报告末尾的参考来源用来核对；材料不够时，应相信「证据边界」而不是补全后的句子。
- 评论只取一级，而且只保留少数几条，不能代表整条笔记下的讨论。
- 不提供代理池、多账号、签名算法或验证码处理。这些也不在后续计划里。
