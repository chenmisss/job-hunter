#!/usr/bin/env python3
"""job-hunter:公司名 → 全渠道在招岗位。

渠道:
  official  官网/招聘ATS(飞书招聘、北森 zhiye、自建站) —— Firecrawl search + wait 渲染抓取
  liepin    猎聘 —— search 定位公司页 → company-jobs 列表(标题/薪资/城市/经验)
  boss      BOSS直聘 —— search 定位企业页 → 抓取可见职位(常被风控,尽力而为)
  lagou     拉勾 —— 直抓必遇滑块,降级为搜索结果摘要提取

依赖:仅 Python 标准库;Firecrawl API key 从环境变量 FIRECRAWL_API_KEY 读取。
飞书写入通过本机 lark-cli 完成(复用其登录态,代码不接触任何 token)。
"""
import argparse, json, os, re, subprocess, sys, time, urllib.request

API_KEY = os.environ.get("FIRECRAWL_API_KEY", "")
FC = "https://api.firecrawl.dev/v1"


# ---------------- Firecrawl ----------------
def _post(endpoint, body, timeout=120):
    req = urllib.request.Request(
        f"{FC}/{endpoint}", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)

def fc_search(query, limit=2, scrape=True, wait=8000):
    body = {"query": query, "limit": limit}
    if scrape:
        body["scrapeOptions"] = {"formats": ["markdown"], "onlyMainContent": True,
                                 "actions": [{"type": "wait", "milliseconds": wait}]}
    return _post("search", body).get("data", [])

def fc_scrape(url, wait=8000):
    return _post("scrape", {"url": url, "formats": ["markdown"], "onlyMainContent": True,
                            "actions": [{"type": "wait", "milliseconds": wait}]}) \
        .get("data", {}).get("markdown", "") or ""


# ---------------- 解析器 ----------------
def parse_feishu(md):
    """飞书招聘 ATS:/position/ 链接,链接文本首行是标题。"""
    jobs = []
    for m in re.finditer(r"\[([^\]]{4,800}?)\]\((https?://[^)]*/position/[^)]+)\)", md):
        parts = [p.strip() for p in re.split(r"\\{1,2}\n", m.group(1)) if p.strip()]
        if not parts or re.search(r"登录|首页|搜索|官网|关于我们", parts[0]):
            continue
        title = re.sub(r"\s*(热招|急招)+\s*$", "", parts[0]).strip()[:60]
        city, kind = "", ""
        for p in parts[1:4]:
            if p in ("社招", "校招", "实习"):
                kind = p
            elif re.match(r"^[一-龥A-Za-z、·,，()\- ]{2,25}$", p) and not re.search(r"全职|兼职|类|部", p):
                city = city or p.split("|")[0].strip()
        jobs.append({"title": title, "city": city, "kind": kind or ("实习" if "实习" in title else "社招"),
                     "url": m.group(2)})
    total = re.search(r"开启新的工作[（(](\d+)[)）]", md)
    return jobs, (int(total.group(1)) if total else None)

def parse_beisen(md):
    """北森 zhiye:纯文本卡片 标题(J12345) + 社会招聘 + 城市 + 日期。"""
    jobs, lines = [], [l.strip() for l in md.splitlines()]
    for i, l in enumerate(lines):
        m = re.match(r"^(.{2,45}?)\s*[（(]([A-Z]?\d{4,6})[)）]$", l)
        if not m:
            continue
        ctx = lines[i:i + 10]
        city = next((c for c in ctx if re.search(r"[市省].*[,，,]|[市省]$", c) and "发布" not in c), "")
        kind = "校招" if "校园招聘" in ctx else ("实习" if re.search(r"实习生|实习", l) else "社招")
        jobs.append({"title": m.group(1).strip(), "city": city.replace(",", "、"), "kind": kind, "url": ""})
    total = re.search(r"全部职位[（(]共\s*(\d+)\s*个[)）]", md)
    return jobs, (int(total.group(1)) if total else None)

def parse_generic_portal(md):
    """自建招聘站/其他:链接文本形如岗位名,或正文行像岗位名。"""
    jobs = []
    for m in re.finditer(r"\[([^\]]{2,60})\]\((https?://[^)]*(?:job|position|career|recruit|join)[^)]*)\)", md, re.I):
        t = re.sub(r"\s*(热招|急招)+\s*$", "", m.group(1).replace("\\", "")).strip()
        if re.search(r"(工程师|算法|研究员|架构师|经理|运营|产品|设计|开发|测试|实习|科学家|顾问|专员)$", t) \
                and not re.search(r"登录|首页|官网|搜索|关于", t):
            jobs.append({"title": t[:60], "city": "", "kind": "实习" if "实习" in t else "社招", "url": m.group(2)})
    return jobs, None

def parse_liepin(md):
    """猎聘 company-jobs 页:[**title  salary**\\ + 城市-区县+经验学历 + 日期](job/lptjob 链接)。"""
    jobs = []
    for m in re.finditer(r"\[([^\]]{10,800}?)\]\((https?://[^)]*liepin\.com/(?:job|lptjob)[^)\s]*)[^)]*\)", md):
        text, url = m.group(1), m.group(2)
        bm = re.search(r"\*\*(.+?)\*\*", text)
        if not bm:
            continue
        ts = bm.group(1).strip()
        sm = re.match(r"^(.*?)\s{2,}(\d+k?[-~][0-9k.]+[^ ]*|\d+[-~]\d+元/天|面议)$", ts)
        title, salary = (sm.group(1).strip(), sm.group(2)) if sm else (ts, "")
        lines = [l.strip() for l in re.split(r"\\{1,2}\n|\n", text) if l.strip()]
        loc = ""
        for l in lines:
            if l != ts and re.match(r"^[一-龥A-Za-z]{2,}[-·]", l) and "k" not in l[:6]:
                loc = l
                break
        city = re.split(r"(?:\d+[-年]|经验|应届|本科|硕士|博士|大专|学历|统招|在校)", loc)[0].strip()
        date = next((l for l in lines
                     if re.match(r"^(\d{4}-\d{2}-\d{2}|\d+小时前|\d+天前|\d+周前|\d+个?月前|今天|昨天)$", l)), "")
        jobs.append({"title": title[:60], "salary": salary, "city": city,
                     "kind": "实习" if "实习" in title else "社招", "url": url, "date": date})
    total = re.search(r"职位\s*·\s*(\d+)", md)
    return jobs, (int(total.group(1)) if total else None)

def parse_zhipin(md):
    """BOSS 企业页(常只渲染出部分):/job_detail/ 链接。"""
    jobs = []
    for m in re.finditer(r"\[([^\]]{2,50})\]\((https?://www\.zhipin\.com/job_detail/[^)\s]+)[^)]*\)", md):
        t = re.sub(r"\s*(热招|急招)+\s*$", "", m.group(1).replace("\\", "")).strip()
        if not re.search(r"登录|首页|搜索|官网|公司|产品", t):
            jobs.append({"title": t[:60], "city": "", "kind": "实习" if "实习" in t else "社招",
                         "url": m.group(2)})
    return jobs, None


def dedup(jobs):
    seen, out = set(), []
    for j in jobs:
        k = (j.get("title", ""), j.get("city", ""))
        if j.get("title") and k not in seen:
            seen.add(k)
            out.append(j)
    return out


# ---------------- 渠道 ----------------
ATS_HINT = re.compile(r"jobs\.feishu\.cn|zhiye\.com|/position|career|join|jobs\.|hr\.|zhaopin|recruit", re.I)

def channel_official(company, sleep):
    results = fc_search(f"{company} 招聘 官网 社会招聘 职位", limit=2)
    best = next((r for r in results if ATS_HINT.search(r.get("url", ""))), None) or (results[0] if results else None)
    if not best:
        return {"jobs": [], "note": "未找到官网招聘页"}
    url, md = best.get("url", ""), best.get("markdown", "") or ""
    jobs, total = parse_feishu(md)
    if not jobs and "zhiye.com" in url:
        jobs, total = parse_beisen(md)
    if not jobs:
        jobs, _ = parse_generic_portal(md)
    if not jobs and re.search(r"jobs\.feishu\.cn", url):
        time.sleep(sleep)
        md2 = fc_scrape(url.rstrip("/") + "/position/list")
        jobs, total = parse_feishu(md2)
    src_note = "" if jobs else "官网有招聘页但未解析到职位(可能为交互页)"
    return {"portal": url, "jobs": dedup(jobs), "total": total, "note": src_note}

def channel_liepin(company, sleep):
    results = fc_search(f"{company} 猎聘 招聘职位", limit=3, scrape=False)
    comp = next((r for r in results if re.search(r"liepin\.com/company/\d+", r.get("url", ""))), None)
    if not comp:
        return {"jobs": [], "note": "猎聘未找到公司主页"}
    cid = re.search(r"liepin\.com/company/(\d+)", comp["url"]).group(1)
    time.sleep(sleep)
    md = fc_scrape(f"https://m.liepin.com/company-jobs/{cid}/")
    jobs, total = parse_liepin(md)
    if not jobs:
        jobs, total2 = parse_generic_portal(md)
        total = total or total2
    return {"portal": f"https://m.liepin.com/company/{cid}/", "jobs": dedup(jobs), "total": total,
            "note": "" if jobs else "猎聘列表页未解析到职位"}

def channel_boss(company, sleep):
    results = fc_search(f"{company} BOSS直聘 招聘", limit=2)
    boss = next((r for r in results if "zhipin.com/gongsi" in r.get("url", "")), None)
    if not boss:
        return {"jobs": [], "note": "BOSS直聘未找到企业页"}
    jobs, _ = parse_zhipin(boss.get("markdown", "") or "")
    if not jobs:
        time.sleep(sleep)
        jobs, _ = parse_zhipin(fc_scrape(boss["url"], wait=10000))
    return {"portal": boss["url"], "jobs": dedup(jobs), "total": None,
            "note": "" if jobs else "BOSS 风控拦截,仅获页面框架"}

def channel_lagou(company, sleep):
    """拉勾直抓必遇滑块验证,降级:从搜索结果摘要提取。"""
    results = fc_search(f"{company} 拉勾 招聘职位", limit=3, scrape=False)
    jobs = []
    for r in results:
        if "lagou.com" not in r.get("url", ""):
            continue
        for seg in re.split(r"[,，;；。\n]", r.get("description", "")):
            seg = seg.strip()
            if re.search(r"(工程师|经理|运营|产品|设计|实习|研究员)$", seg) and 4 <= len(seg) <= 30:
                jobs.append({"title": seg, "city": "", "kind": "社招", "url": r["url"]})
    note = "拉勾反爬严格,结果为搜索摘要降级" if jobs else "拉勾被反爬拦截且无摘要可用"
    return {"portal": "", "jobs": dedup(jobs), "total": None, "note": note}

CHANNELS = {"official": ("官网/ATS", channel_official), "liepin": ("猎聘", channel_liepin),
            "boss": ("BOSS直聘", channel_boss), "lagou": ("拉勾", channel_lagou)}


# ---------------- 主流程 ----------------
def hunt(company, channels, sleep, subfield="", industry=""):
    rec = {"company": company, "subfield": subfield, "industry": industry,
           "channels": {}, "jobs": []}
    for i, ch in enumerate(channels):
        label, fn = CHANNELS[ch]
        try:
            r = fn(company, sleep)
        except Exception as e:
            r = {"jobs": [], "note": f"渠道异常:{e}"}
        r["source"] = label
        rec["channels"][ch] = {k: v for k, v in r.items() if k != "jobs"}
        for j in r.get("jobs", []):
            j.update({"company": company, "source": label, "subfield": subfield})
            rec["jobs"].append(j)
        print(f"  [{label}] {len(r.get('jobs', []))} 个岗位"
              + (f"(页标 {r['total']})" if r.get("total") else "")
              + (f" — {r['note']}" if r.get("note") else ""), flush=True)
        if i < len(channels) - 1:
            time.sleep(sleep)
    rec["jobs"] = dedup(rec["jobs"])
    return rec

def cmd_hunt(args):
    rec = hunt(args.company, args.channels.split(","), args.sleep,
               subfield=args.subfield, industry="")
    _save([rec], args.out)
    print(f"\n{rec['company']}: 共 {len(rec['jobs'])} 个岗位 → {args.out}")

def cmd_hunt_batch(args):
    exhibitors = json.load(open(args.exhibitors))
    if args.subfield:
        exhibitors = [e for e in exhibitors if e.get("subfield") == args.subfield]
    exhibitors = exhibitors[:args.limit]
    all_recs = []
    for i, e in enumerate(exhibitors):
        name = e["company"].split("/")[0].strip()  # 用完整法定名(含地区括号)提高搜索精度
        print(f"[{i + 1}/{len(exhibitors)}] {name} ({e.get('subfield', '')})", flush=True)
        try:
            all_recs.append(hunt(name, args.channels.split(","), args.sleep,
                                 subfield=e.get("subfield", ""), industry=e.get("industry", "")))
        except Exception as ex:
            print(f"  !! {ex}", flush=True)
        _save(all_recs, args.out)  # 增量落盘
        time.sleep(args.sleep)
    total = sum(len(r["jobs"]) for r in all_recs)
    print(f"\n完成:{len(all_recs)} 家公司, {total} 个岗位 → {args.out}")

def _save(recs, path):
    json.dump(recs, open(path, "w"), ensure_ascii=False, indent=1)


# ---------------- 飞书多维表格 ----------------
BASE_FIELDS = [
    {"type": "text", "name": "岗位名称"},
    {"type": "text", "name": "公司"},
    {"type": "text", "name": "赛道"},
    {"type": "text", "name": "城市"},
    {"type": "text", "name": "薪资"},
    {"type": "select", "name": "类型", "options": [{"name": "社招"}, {"name": "校招"}, {"name": "实习"}]},
    {"type": "select", "name": "来源", "options": [{"name": "官网/ATS"}, {"name": "猎聘"},
                                                {"name": "BOSS直聘"}, {"name": "拉勾"}]},
    {"type": "text", "name": "链接", "style": {"type": "url"}},
    {"type": "datetime", "name": "抓取日期", "style": {"format": "yyyy-MM-dd"}},
]

def _lark(args_list):
    p = subprocess.run(["lark-cli"] + args_list, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(f"lark-cli 失败: {p.stderr or p.stdout}")
    return json.loads(p.stdout)

def cmd_make_base(args):
    out = _lark(["base", "+base-create", "--name", args.name, "--table-name", "岗位明细",
                 "--fields", json.dumps(BASE_FIELDS, ensure_ascii=False), "--as", "user"])
    data = out.get("data", out)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    print("\n把上面返回的 base_token / table_id 记下来,供 push-base 使用")

def cmd_push_base(args):
    recs = json.load(open(args.input))
    rows = []
    today = time.strftime("%Y-%m-%d %H:%M:%S")
    valid_src = {"官网/ATS", "猎聘", "BOSS直聘", "拉勾"}

    def norm_kind(k, title):
        if "实习" in k or "实习" in title:
            return "实习"
        if "校招" in k:
            return "校招"
        return "社招"

    for rec in recs:
        for j in rec.get("jobs", []):
            src = j.get("source", "")
            rows.append([j.get("title", ""), rec.get("company", ""), j.get("subfield", ""),
                         j.get("city", ""), j.get("salary", ""), norm_kind(j.get("kind", ""), j.get("title", "")),
                         src if src in valid_src else "官网/ATS", j.get("url", ""), today])
    fields = ["岗位名称", "公司", "赛道", "城市", "薪资", "类型", "来源", "链接", "抓取日期"]
    n = 0
    for i in range(0, len(rows), 200):
        batch = rows[i:i + 200]
        _lark(["base", "+record-batch-create", "--base-token", args.base_token,
               "--table-id", args.table_id,
               "--json", json.dumps({"fields": fields, "rows": batch}, ensure_ascii=False),
               "--as", "user"])
        n += len(batch)
        print(f"已写入 {n}/{len(rows)}", flush=True)
        time.sleep(1)
    print(f"完成:共写入 {n} 条岗位记录")


def main():
    if not API_KEY and not (len(sys.argv) > 1 and sys.argv[1] in ("make-base", "push-base")):
        sys.exit("请先 export FIRECRAWL_API_KEY=fc-xxx")
    ap = argparse.ArgumentParser(prog="job_hunter", description="公司名 → 全渠道在招岗位")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("hunt", help="抓一家公司")
    p.add_argument("--company", required=True)
    p.add_argument("--channels", default="official,liepin,boss,lagou")
    p.add_argument("--subfield", default="")
    p.add_argument("--sleep", type=float, default=8)
    p.add_argument("--out", default="hunt_out.json")
    p.set_defaults(fn=cmd_hunt)

    p = sub.add_parser("hunt-batch", help="按展商名单批量抓")
    p.add_argument("--exhibitors", required=True, help="exhibitors.json 路径")
    p.add_argument("--channels", default="official,liepin")
    p.add_argument("--subfield", default="")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--sleep", type=float, default=8)
    p.add_argument("--out", default="hunt_batch_out.json")
    p.set_defaults(fn=cmd_hunt_batch)

    p = sub.add_parser("make-base", help="创建飞书多维表格(岗位明细)")
    p.add_argument("--name", default="岗位雷达")
    p.set_defaults(fn=cmd_make_base)

    p = sub.add_parser("push-base", help="把抓取结果写入飞书多维表格")
    p.add_argument("--input", required=True)
    p.add_argument("--base-token", required=True)
    p.add_argument("--table-id", required=True)
    p.set_defaults(fn=cmd_push_base)

    args = ap.parse_args()
    args.fn(args)

if __name__ == "__main__":
    main()
