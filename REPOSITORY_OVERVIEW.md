# CNKI Citation Network Crawler

这是一个面向 CSSCI 来源刊的 CNKI 文章与引文网络采集项目。项目按期刊分片运行，支持断点续跑、日期区间自适应切分、文章元数据增强、参考文献与引证文献抓取，以及 JSONL/JSON/CSV 交付。

## 项目定位

- 适用场景：经过授权的学术文献检索、计量分析和引文网络构建。
- 运行方式：Python 单进程任务，可按 shard 划分期刊；生产环境可使用 systemd 模板部署。
- 质量控制：父子日期区间计数守恒、文章 ID 去重、分页状态记录和失败状态保留。
- 交付格式：文章级 JSONL/JSON/CSV，以及参考文献和引证文献关系 CSV。

## 目录重点

| 路径 | 作用 |
| --- | --- |
| `main.py` | 命令行入口、分片运行和断点续跑 |
| `scrapers/` | CNKI 搜索、详情页、引文网络和分片调度 |
| `exporters.py` | JSON、JSONL、CSV 导出 |
| `deploy/` | systemd 示例和部署辅助脚本 |
| `tests/` | 单元测试与安全回归测试 |
| `docs/` | 设计记录、运行门禁和运维说明 |
| `.env.example` | Cookie 与邮件通知配置模板 |

## 相关工具说明

本仓库只包含 Python 源码、测试、公开配置模板和部署示例。CNKI 登录 Cookie、SMTP 授权码、运行数据库、缓存、日志和抓取结果需要由使用者在本地或服务器单独配置。项目同级目录中的压缩包、临时分析目录和运行产物属于本地交付材料，不是仓库依赖。

## 快速开始

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Windows 可使用 copy
python main.py --print-shards --shard-count 3
python -m unittest discover -s tests -p "test_*.py"
```

实际采集前，请先完成授权、Cookie 有效性检查和小样本 dry run，再逐步扩大范围。详细参数和生产运行流程见 `README.md` 与 `README_TEAM.md`。

## 数据与合规

仅对有权访问和研究使用的数据运行；遵守 CNKI、所在机构和适用法律的使用条款。不要把 Cookie、SMTP 授权码、带会话令牌的 URL、数据库或抓取结果提交到 Git。
