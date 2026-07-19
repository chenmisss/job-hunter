---
name: job-hunter
version: 1.0.0
description: "公司名 → 全渠道在招岗位。渠道:官网/招聘ATS(飞书招聘、北森)、猎聘、BOSS直聘、拉勾(降级);结果可写入飞书多维表格。当用户要查某公司在招岗位、批量巡检展商/目标公司招聘、把岗位同步到 Base 时使用。Firecrawl key 配置或 lark-cli 认证问题转 lark-shared / 用户自行 export。"
metadata:
  requires:
    bins: ["python3", "lark-cli"]
    env: ["FIRECRAWL_API_KEY"]
---

# job-hunter

输入公司名,自动在四个渠道找在招岗位,统一成结构化记录,可一键写入飞书多维表格。

## 能力边界

- 只做「公司 → 岗位列表」;不做简历匹配打分(拿到 JSON 后由 agent 自己分析)。
- 渠道覆盖:官网/ATS(飞书招聘、北森 zhiye、自建站)、猎聘、BOSS直聘、拉勾。
  - 猎聘最稳(标题/薪资/城市/经验/日期);官网 ATS 需 wait 渲染;BOSS/拉勾风控重,尽力而为并在 note 里说明。
- 不写简历、不投简历;只读公开页面。

## 前置条件

```bash
export FIRECRAWL_API_KEY=fc-xxx   # firecrawl.dev 免费档 1000 额度/月
lark-cli auth status               # 仅写飞书多维表格时需要,user 身份 ready 即可
```

成本:每家公司每渠道 ≈ 1 次 search + 1 次 scrape ≈ 4~5 额度。免费档建议单批 ≤ 30 家。

## 已登录浏览器(CDP)模式:先登录,再逐步找职位

BOSS直聘/拉勾风控严格,Firecrawl 基本拿不到。正确姿势是**用户先手动登录,脚本再接管真人浏览器逐步找职位**:

```bash
# 第一步:启动受控 Chrome(独立 profile,不污染日常浏览器)
python3 job_hunter.py setup-browser

# 第二步:用户在弹出的窗口里手动登录 BOSS直聘(和拉勾)。只需一次,登录态保留在
#         ~/.job-hunter/chrome-profile。这步必须用户本人完成(扫码/短信),agent 不可代劳。

# 第三步:登录后再跑,BOSS/拉勾渠道自动改走 CDP 接管
python3 job_hunter.py hunt --company 商汤科技 --channels boss,lagou --cdp
python3 job_hunter.py hunt-batch --exhibitors exhibitors.json --channels official,liepin,boss --cdp --limit 30
```

要点:

- **顺序不能反**:未登录时 BOSS 只给验证/空壳;agent 应先跑 `setup-browser`,提示用户登录,用户确认后再带 `--cdp` 跑。
- BOSS 有反调试置空(页面几秒内跳 about:blank),脚本已做「commit 后 300ms 轮询抢快照」,无需干预。
- BOSS 企业页只有热招职位区(几个到几十个);「招聘职位(0)」= 该公司真无在招,不是失败。
- 本机若有系统代理(Clash 等),脚本已显式绕过 localhost 代理;若 `cdp_ok` 仍失败,重启 `setup-browser`。
- 合规:仅本人账号、低频、自用;高频有封号风险,结果仅供求职参考。

## 命令

```bash
cd ~/.agents/skills/job-hunter

# 1. 抓一家公司(全渠道)
python3 job_hunter.py hunt --company 商汤科技 --channels official,liepin,boss,lagou --out out/sensetime.json

# 1.5 BOSS/拉勾走已登录浏览器:先 setup-browser 登录,再加 --cdp(见上节)
python3 job_hunter.py setup-browser
python3 job_hunter.py hunt --company 商汤科技 --channels boss,lagou --cdp --out out/sensetime_cdp.json

# 2. 按展商名单批量抓(exhibitors.json 需含 company/subfield 字段)
python3 job_hunter.py hunt-batch --exhibitors /path/exhibitors.json \
    --subfield 具身智能/人形机器人 --limit 30 --channels official,liepin --out out/embodied.json

# 3. 创建飞书多维表格(记录返回的 base_token / table_id)
python3 job_hunter.py make-base --name "岗位雷达"

# 4. 把结果写入多维表格
python3 job_hunter.py push-base --input out/embodied.json --base-token XXX --table-id tblYYY
```

## 输出格式

`hunt*` 输出公司数组,每项 `{company, subfield, channels: {渠道: {portal,total,note,source}}, jobs: [{title,city,salary,kind,source,url,company,subfield}]}`。
`jobs` 已按 (title,city) 去重;`channels.*.note` 记录降级/失败原因,汇报时必须如实转述,不要把 0 结果说成"该公司不招人"。

## 给 agent 的建议流程

1. 先 `hunt` 单家验证 key 与网络,再 `hunt-batch` 放量。
2. `--channels` 默认 `official,liepin`(性价比最高);用户点名要 BOSS/拉勾再加。
3. 写 Base 前必须先 `make-base` 或让用户提供 base_token;写完把 Base 链接给用户。
4. 结果分析(岗位聚类、城市分布、薪资带)直接读 JSON 做,不要再抓。
