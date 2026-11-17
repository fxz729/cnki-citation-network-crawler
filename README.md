# CNKI Citation Network Crawler

面向 CSSCI 来源刊的授权文献与引文网络采集器，支持分片、断点续跑、质量校验和结构化导出。

项目范围与公开发布说明见 [`REPOSITORY_OVERVIEW.md`](REPOSITORY_OVERVIEW.md)。

## 目标

- 读取项目内 `data/CSSCI来源刊公众号主页链接.md` 的 126 本期刊清单
- 按“文献来源”抓取文章
- 输出题名、作者、来源、发表时间、摘要、学校 / 单位、关键词、基金资助、专辑、专题、分类号、被引次数、下载次数
- 抓取摘要页“引文网络”六类数据
- 优先使用 HTML reader 专用参考文献接口获取完整参考文献，失败时回退旧 citation API
- 支持按期刊流水线增量落盘和三个静态 shared 并行分工

## 结构

- `main.py`：主入口
- `config.py`：路径、Cookie、接口常量
- `data/CSSCI来源刊公众号主页链接.md`：126 本已匹配公众号的 CSSCI 期刊清单；爬虫从“期刊原名”列读取 CNKI 检索来源
- `scrapers/`：抓取、解析、checkpoint、shard 分工
- `exporters.py`：JSON / CSV 导出
- `tests/`：单元测试

## 环境

创建虚拟环境：

```bash
# Windows
cd <project-dir>
python -m venv .venv
.venv\Scripts\activate

# Linux
cd <project-dir>
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

安装依赖：

```bash
pip install -r requirements.txt
```

## Cookie

复制模板：

```bash
# Windows
copy .env.example .env

# Linux
cp .env.example .env
```

在 `.env` 中填入：

```env
CNKI_COOKIE=...完整Cookie...
```

程序优先读取：

1. 显式传入的 `cookie`
2. 环境变量 `CNKI_COOKIE`
3. 项目 `.env` 中与当前分片对应的 `CNKI_COOKIE_SHARED_N`
4. 项目 `.env` 中的兜底 `CNKI_COOKIE`

在同一个 `.env` 中为三个 shared 分别填入不同的有效 Cookie：

```env
CNKI_COOKIE_SHARED_1=...shared-1的完整Cookie...
CNKI_COOKIE_SHARED_2=...shared-2的完整Cookie...
CNKI_COOKIE_SHARED_3=...shared-3的完整Cookie...
```

运行 `--shard-id N` 时，程序会自动选择 `CNKI_COOKIE_SHARED_N`；若该项为空，才使用 `CNKI_COOKIE` 兜底。

## 流水线

当前运行顺序是按期刊独立流水线，不再“先搜完全部期刊，再统一增强”：

```text
for journal in assigned_journals:
    1. 请求 2016–2026 完整日期范围第 1 页
    2. 若总数 > 50，递归二分为无重叠日期区间
    3. 验证父区间总数等于两个子区间总数之和
    4. 搜索树全部验证后，逐个叶区间 append raw.jsonl 并增强文章
    5. 期刊完成后重建 articles.json / articles.csv / citation_relations.csv
    6. 写 checkpoint，记录该期刊完成
```

`--resume` 默认开启：

- `checkpoint.json` 中已完成的期刊会整刊跳过。
- `enriched.jsonl` 中已有 `success` 的文章会跳过。
- `failed` / `partial` 文章会在下次运行中重新尝试。

## Reader 参考文献优先级

对 `references/CJFDREF`，程序优先走 HTML reader 专用接口：

```text
articles/detail -> metadata.vid -> original-references-auth?size=50&start=1&v=<vid>
```

如果 `articles/detail` 因 reader 会话返回 `401` / “登录已过期”，程序会改用详情页 `detail_context.kcms_link` 中的 `v` 参数继续请求 `original-references-auth`。真实验证中，同一篇文章旧 citation API 返回 `23` 条参考文献，而 `original-references-auth` 通过详情页 `v` 返回 `32/32` 条完整参考文献。

说明：

- `vid` 会做 URL 编码。
- reader references 支持超过 50 条继续分页。
- reader detail / references 失败时自动回退旧 `citation-api/v1/literature/references`。
- 回退接口若后续页失败但已有部分结果，会保留 partial 结果并标记 `citation_network_status=partial`。
- 不硬编码个人 Cookie，所有请求使用运行时 session / `.env` Cookie。

## 运行

### 查看三份期刊分工

126 本期刊按稳定轮转规则均分为 3 个 shared，每个 shared 42 本；分片间不重叠。

```bash
python main.py --print-shards --shard-count 3
```

### 全量运行（推荐：自动探测年份范围，避免翻页验证码）

> **重要**：CNKI `kns8s/brief/grid` 接口在翻到第 2 页时可能触发验证码页面。推荐使用 `--auto-detect-years`：
> 1. 对每本期刊先请求 `--year-start-floor-01-01` 至 `--year-end-12-31` 的完整日期范围第 1 页。
> 2. 总数不超过 50 时直接复用该页；超过 50 时递归二分为无重叠日期区间，所有叶区间仍只取第 1 页。
> 3. 每次二分都验证父区间总数等于左右子区间总数之和，叶区间验证解析行数等于总数；不满足即停止且不写完成 checkpoint。

```bash
# Windows shared 1
.venv\Scripts\python.exe -u main.py --shard-id 1 --shard-count 3 --auto-detect-years --page-size 50 --max-pages 0 --resume

# Linux shard 1 后台
mkdir -p logs/shard-1
nohup .venv/bin/python -u main.py --shard-id 1 --shard-count 3 --auto-detect-years --page-size 50 --max-pages 0 --resume > logs/shard-1/run.log 2>&1 &
```

> `-u` 禁用 Python 输出缓冲，确保日志实时写入。

### 三个 shared 的分工命令

```bash
nohup .venv/bin/python -u main.py --shard-id 1 --shard-count 3 --auto-detect-years --page-size 50 --max-pages 0 --resume > logs/shard-1/run.log 2>&1 &
nohup .venv/bin/python -u main.py --shard-id 2 --shard-count 3 --auto-detect-years --page-size 50 --max-pages 0 --resume > logs/shard-2/run.log 2>&1 &
nohup .venv/bin/python -u main.py --shard-id 3 --shard-count 3 --auto-detect-years --page-size 50 --max-pages 0 --resume > logs/shard-3/run.log 2>&1 &
```

安全建议：先启动 shard 1，稳定观察 10–20 分钟后，再启动其他机器 / shard。

### 单期刊 / 小样本验证

```bash
# 切片模式小样本
.venv\Scripts\python.exe main.py --journal “北京大学教育评论” --slice-by-year --year-start 2025 --year-end 2026 --page-size 50 --limit-articles 5

# 传统模式（仅第 1 页，不翻页）
.venv\Scripts\python.exe main.py --journal “北京大学教育评论” --limit-articles 3 --page-size 20 --max-pages 1

# shard dry run
python main.py --shard-id 1 --shard-count 3 --limit-journals 1 --limit-articles 2 --max-pages 1 --resume
```

### 参数说明

- `--journal`：指定 1 本或多本期刊；可重复传入
- `--limit-journals`：只跑前 N 本期刊；shard 拆分前生效
- `--limit-articles`：每本期刊最多增强前 N 篇文章；最终全量输出不要使用该参数
- `--page-size`：搜索页每页条数，默认 `20`；知网当前接口上限为 `50`，程序会自动限制到 `1–50`，切片模式建议设为 `50`
- `--max-pages`：每本期刊最多抓几页；传 `0` 表示按搜索结果总数自动翻页到结束。自动探测 / 年份切片模式下切片内只取第 1 页
- `--auto-detect-years`：启用自适应日期范围。先查询 `--year-start-floor-01-01` 至 `--year-end-12-31`，超过单页上限才递归二分
- `--year-end`：自动探测的起始年份，默认 `2026`
- `--year-start-floor`：自动探测的最早年份下限，默认 `2016`
- `--slice-by-year`：旧模式，需同时指定 `--year-start` 和 `--year-end` 的固定年份范围；请优先使用 `--auto-detect-years`
- `--relation-page-size`：旧 citation API 每页条数，默认 `10`；reader references 固定按 `50` 抓取
- `--max-relation-pages`：参考文献 / 引证文献明细最多抓几页，默认 `0` 表示抓完全部分页
- `--delay-min` / `--delay-max`：每次请求前的随机等待秒数，默认 `5` 到 `8`
- `--max-retries`：遇到 `429`、`403`、`5xx` 时的最大重试次数，默认 `3`
- `--backoff-factor`：重试指数退避基数，默认 `12`
- `--resume` / `--no-resume`：默认开启断点续跑
- `--shard-id`：当前分工编号，1-based，默认 `1`
- `--shard-count`：总分工数，默认 `3`
- `--shard-output-root`：shared 输出根目录，默认 `output`；生产三 shared 可使用 `output-3`
- `--shard-cache-root`：shared 缓存根目录，默认 `cache`；生产三 shared 可使用 `cache-3`
- `--shard-log-root`：shared 日志根目录，默认 `logs`；生产三 shared 可使用 `logs-3`
- `--print-shards`：只打印期刊分工，不运行爬虫

### 延时配置建议

- Safe：`--delay-min 10 --delay-max 15`
- Balanced default：`--delay-min 5 --delay-max 8`
- Faster test only：`--delay-min 3 --delay-max 5`

程序仍保持串行搜索和串行详情增强，不做并发 HTTP 请求。

### 自适应切片模式工作原理

1. 对每本期刊先请求目标完整日期范围（默认 `2016-01-01..2026-12-31`）第 1 页
2. 若总数不超过知网单页上限 50，要求解析行数严格等于总数并直接使用
3. 若总数超过 50，将日期区间二分为两个无重叠、无缺口的子区间并递归处理
4. 每次拆分都要求父区间总数等于两个子区间总数之和；不守恒即停止，不完成该期刊
5. 搜索树全部验证后再逐叶片增强；文章按 `file_name:db_name` 去重
6. 明确 `no-content` 可作为合法空结果；验证码、空白或结构异常响应会刷新会话重试，仍失败则停止

参考文献 / 引证文献默认全量抓取明细；共引 / 同被引 / 二级参考 / 二级引证可能有上千到上万条，当前只导出 CNKI metrics 总数，不抓取关系明细。

## 输出

当前正式运行以 shard 目录为准：三 shared 建议使用独立根目录 `output-3/shard-1`、`output-3/shard-2`、`output-3/shard-3`；缓存和日志分别使用 `cache-3/shard-N`、`logs-3/shard-N`，不要复用旧九分片目录。

- `raw.jsonl`：搜索阶段原始文章记录，逐篇 append
- `enriched.jsonl`：增强后的文章记录，逐篇 append
- `articles.jsonl`：文章级增量 JSONL，逐篇 append，便于中断恢复和快速检查
- `articles.json`：文章级聚合 JSON，每本期刊完成后原子重写
- `articles.csv`：文章级 CSV，每本期刊完成后重建
- `citation_relations.csv`：关系级 CSV，每本期刊完成后重建
- `checkpoint.json`：当前 shard 的期刊级断点状态

### `articles.csv` 字段说明

- `article_id`：文章唯一标识，优先使用 `file_name:db_name`
- `source_journal_input`：本次检索使用的来源期刊名
- `title`：文章题名
- `authors`：作者列表，使用分号连接
- `journal`：来源期刊名称
- `publicationTime`：发表时间，通常为日期字符串
- `abstract`：详情页解析得到的完整摘要
- `institution`：详情页解析得到的学校 / 作者单位
- `keywords`：详情页解析得到的关键词
- `funding`：详情页解析得到的基金资助
- `album`：详情页解析得到的专辑
- `special_topic`：详情页解析得到的专题
- `classification_number`：详情页解析得到的分类号
- `quote_frequency`：搜索结果页显示的被引次数
- `download_count`：搜索结果页显示的下载次数
- `citation_network_status`：引文网络抓取状态，取值通常为 `success`、`partial`、`failed`
- `citation_network_error`：引文网络抓取错误信息
- `references_count`：CNKI metrics 返回的参考文献总数
- `references_fetched_count`：本地实际抓取并写入关系明细的参考文献条数
- `citing_articles_count`：CNKI metrics 返回的引证文献总数
- `citing_articles_fetched_count`：本地实际抓取并写入关系明细的引证文献条数
- `co_cited_articles_count`：CNKI metrics 返回的共引文献总数
- `co_cited_articles_fetched_count`：共引文献明细本地抓取数；当前默认不抓明细，通常为 `0`
- `co_citing_articles_count`：CNKI metrics 返回的同被引文献总数
- `co_citing_articles_fetched_count`：同被引文献明细本地抓取数；当前默认不抓明细，通常为 `0`
- `secondary_references_count`：CNKI metrics 返回的二级参考文献总数
- `secondary_references_fetched_count`：二级参考文献明细本地抓取数；当前默认不抓明细，通常为 `0`
- `secondary_citing_articles_count`：CNKI metrics 返回的二级引证文献总数
- `secondary_citing_articles_fetched_count`：二级引证文献明细本地抓取数；当前默认不抓明细，通常为 `0`

### `citation_relations.csv` 字段说明

- `article_id`：源文章唯一标识，优先使用 `file_name:db_name`
- `source_journal_input`：本次检索使用的来源期刊名
- `source_title`：源文章题名
- `source_authors`：源文章作者
- `source_journal`：源文章来源期刊
- `source_publication_time`：源文章发表时间
- `relation_type`：关系类型，取值为 `references`、`citing_articles`
- `target_title`：目标文献题名
- `target_authors`：目标文献作者
- `target_journal`：目标文献来源期刊 / 出版源
- `target_year`：目标文献年份
- `target_file_name`：目标文献文件名
- `target_db_name`：目标文献数据库名
- `target_db_type`：目标文献资源类型
- `relation_rank`：目标文献在该关系列表中的顺序号

## 正式启动门禁

正式运行前确认：

- 本地单元测试通过：`python -m unittest discover -s tests -p "test_*.py"`
- `python main.py --print-shards --shard-count 3` 已检查
- 本地 dry run 通过：`python main.py --shard-id 1 --shard-count 3 --limit-journals 1 --limit-articles 2 --max-pages 1 --resume`
- dry run 在 `output/shard-1/` 产出 `raw.jsonl`、`enriched.jsonl`、`articles.jsonl`、`articles.json`、`articles.csv`、`citation_relations.csv`
- 服务器 `.env` 存在，且同步代码时不覆盖 `.env`
- 服务器测试和 dry run 通过
- 启动正式 shard 前确认当前是否已有运行中的 crawler 进程

## 服务器同步与启动

不要覆盖服务器 `.env`、`output-3/`、`cache-3/`、`logs-3/`、`.venv/`：

```bash
rsync -av --exclude .env --exclude output --exclude output-3 --exclude cache --exclude cache-3 --exclude logs --exclude logs-3 --exclude .venv ./ root@<remote-host>:<project-dir>/
```

服务器 systemd 模板：`deploy/cnki-scraper3@.service`。安装后先只启动 `cnki-scraper3@1.service`，确认稳定后再启动 2、3。

### Cookie 失效邮件通知（可选）

在服务器项目 `.env` 中配置 QQ 邮箱 SMTP 授权码：

```env
CNKI_ALERT_EMAIL_FROM=发件QQ邮箱
CNKI_ALERT_EMAIL_AUTH_CODE=QQ邮箱SMTP授权码
CNKI_ALERT_EMAIL_TO=收件邮箱
```

通知只在 CNKI 稳定搜索接口返回 `401`，并且程序从 `.env` 重载完整 Cookie 后重试仍为 `401` 时触发。详情页临时 `v` 令牌过期、Reader“请求已过期”、验证页、网络异常、限流以及普通进程重启都不会触发邮件。

同一 Cookie 只发送一次（生产 `--resume` 模式）；检查点仅保存 Cookie 的部署本地 HMAC 指纹，不保存 Cookie 本身。新 Cookie 将来再次确认失效时会重新发送。该终态退出码为 `76`，systemd 不会自动重启，避免重复请求和重复邮件。若邮件未配置或发送失败，抓取仍会安全暂停并在日志中记录原因。

QQ 邮箱应使用单独的 SMTP 授权码，不要填写 QQ 登录密码，也不要把真实授权码提交到 Git。

服务器测试：

```bash
ssh root@<remote-host> 'cd <project-dir> && .venv/bin/python -m unittest discover -s tests -p "test_*.py"'
```

服务器 dry run：

```bash
ssh root@<remote-host> 'cd <project-dir> && .venv/bin/python -u main.py --shard-id 1 --shard-count 3 --shard-output-root output-3 --shard-cache-root cache-3 --shard-log-root logs-3 --limit-journals 1 --limit-articles 2 --max-pages 1 --resume'
```

安装并启动 shared 1：

```bash
ssh root@<remote-host> 'cd <project-dir> && install -m 0644 deploy/cnki-scraper3@.service /etc/systemd/system/cnki-scraper3@.service && systemctl daemon-reload && systemctl enable --now cnki-scraper3@1.service'
```

启动 shared 1：

```bash
ssh root@<remote-host> 'systemctl start cnki-scraper3@1.service'
```

监控日志：

```bash
ssh root@<remote-host> 'journalctl -u cnki-scraper3@1.service -f'
```

## 说明

- 当前项目默认不再依赖项目外部的期刊清单文件
- 遇到 `verification_required` 时先暂停并由 systemd recovery timer 按 30、60、120 分钟低频探测；只有新鲜搜索得到的详情页返回有效文章结构时才自动恢复；验证页不发送 Cookie 更换通知。
- 遇到 `429`、`403`、`500`、`502`、`503`、`504` 会自动退避重试；这只能降低限流风险，不能保证 CNKI 长期绝对稳定放行
- 三机器并行时必须使用不同 shard；不要共享同一个 live 输出目录
- 后续合并三份 shard 输出应使用单独 reducer 去重，不要在运行中互相写同一目录
