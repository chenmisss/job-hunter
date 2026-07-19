# job-hunter

**公司名 → 全渠道在招岗位**:一个命令扫描目标公司在 **官网/招聘 ATS、猎聘、BOSS直聘、拉勾** 四个渠道的在招岗位,统一成结构化 JSON,并一键写入 **飞书多维表格**。

最初为 WAIC 2026(世界人工智能大会)展商求职场景开发:963 家展商名单 → 自动盯它们在招什么岗位。

## 架构

```
公司名单(exhibitors.json / 单个公司名)
        │
        ▼
┌───────────────── 四渠道并行 ─────────────────┐
│ official  官网/ATS(飞书招聘、北森、自建站)   │  Firecrawl search 定位招聘页
│ liepin    猎聘(标题/薪资/城市/经验/日期)     │  + wait 渲染抓取
│ boss      BOSS直聘(风控重,尽力而为)         │  + 渠道专属解析器
│ lagou     拉勾(滑块拦截 → 搜索摘要降级)      │
└──────────────────────────────────────────────┘
        │  统一记录 {title, city, salary, kind, source, url}
        ▼
  jobs.json ──push-base──▶ 飞书多维表格(经 lark-cli,复用本机登录态)
```

- **零依赖**:只用 Python 3 标准库
- **不碰 token**:Firecrawl key 走环境变量,飞书操作全部委托本机 [lark-cli](https://www.npmjs.com/package/@larksuite/lark-cli)
- **诚实降级**:每个渠道返回 `note` 说明抓取状态(风控拦截/未找到/解析为空),绝不把 0 结果说成"该公司不招人"

## 安装

```bash
git clone https://github.com/chenmisss/job-hunter.git
cd job-hunter
export FIRECRAWL_API_KEY=fc-xxx   # firecrawl.dev 注册即送免费额度(1000 点/月)
# 可选(写飞书多维表格):npm i -g @larksuite/lark-cli && lark-cli auth login
```

## 使用

```bash
# 1. 抓一家公司(四渠道)
python3 job_hunter.py hunt --company 商汤科技 --channels official,liepin,boss,lagou --out out/sensetime.json

# 2. 按名单批量抓(exhibitors.json 需含 company/subfield 字段)
python3 job_hunter.py hunt-batch --exhibitors exhibitors.json \
    --subfield 具身智能/人形机器人 --limit 30 --channels official,liepin --out out/jobs.json

# 3. 创建飞书多维表格(返回 base_token / table_id)
python3 job_hunter.py make-base --name "岗位雷达"

# 4. 写入多维表格
python3 job_hunter.py push-base --input out/jobs.json --base-token XXX --table-id tblYYY
```

## 实测(WAIC 2026 展商,2026-07)

| 渠道 | 状态 | 说明 |
|---|---|---|
| 官网/ATS | ✅ 主力 | 飞书招聘/北森 ATS 为 JS 渲染,Firecrawl `wait` 动作可破;宇树一站抓到 26 个岗位 |
| 猎聘 | ✅ 最稳 | 公司页 → `company-jobs/{id}` 列表,含**薪资/城市/经验/日期**;阶跃星辰 37 个、商汤页标 199 |
| BOSS直聘 | ⚠️ 半通 | 企业页部分可抓(月之暗面 5 个),常遇风控只给框架 |
| 拉勾 | ❌ 基本不通 | 滑块验证拦截,降级为搜索结果摘要 |

单家单渠道成本 ≈ 1 search + 1 scrape ≈ **4~5 额度**;免费档 1000/月 ≈ 每月巡检 200+ 家(official+liepin 双渠道约 100 家)。

## 输出格式

```jsonc
[{
  "company": "阶跃星辰",
  "subfield": "AI基础大模型",
  "channels": {"liepin": {"portal": "https://m.liepin.com/company/21236927/", "total": 189, "note": ""}},
  "jobs": [{"title": "大模型推理优化系统工程师", "city": "上海-徐汇区",
            "salary": "45-65k·16薪", "kind": "社招", "source": "猎聘", "url": "..."}]
}]
```

## 作为 Kimi/Claude Code Skill

本仓库同时是一个 agent skill:把目录放到 `~/.agents/skills/job-hunter/`,agent 读取 `SKILL.md` 后会按路由自动调用。

## 边界与免责

- 只读取公开招聘页面,遵守目标网站 robots 与风控策略;被拦截时如实报告,不绕验证
- 抓取结果为抓取时刻快照,职位以各公司官方渠道为准
- 请勿用于批量爬取用户隐私数据或违反目标站点条款的用途
