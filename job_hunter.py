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
import argparse, json, os, re, subprocess, sys, time, urllib.request, urllib.parse

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


# ---------------- 登录浏览器 CDP 接管(破 BOSS/拉勾风控) ----------------
CDP_PORT = 9222
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME_PROFILE = os.path.expanduser("~/.job-hunter/chrome-profile")

def cdp_ok(port=CDP_PORT):
    try:  # 显式绕开系统代理(如 Clash),localhost 必须直连
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://localhost:{port}/json/version", timeout=3) as r:
            return True
    except Exception:
        return False

def cdp_fetch(url, wait_ms=7000, port=CDP_PORT, selector_wait=None):
    """用已登录的 Chrome(远程调试)打开 url 并返回渲染后 HTML。playwright 仅作 CDP 客户端。"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{port}")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        try:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
            except Exception:
                pass  # 重资源页常挂加载,超时后直接等渲染读内容
            if selector_wait:
                try:
                    page.wait_for_selector(selector_wait, timeout=wait_ms)
                except Exception:
                    pass
            page.wait_for_timeout(wait_ms)
            return page.content()
        finally:
            page.close()

def _strip(html):
    return re.sub(r"<[^>]+>", " ", html)

def parse_boss(html):
    """BOSS 企业页职位卡:<a href=/job_detail/..><div class=name><span class=salary>..<b>title</b>..<span class=company-location>"""
    jobs = []
    for m in re.finditer(r'<a[^>]*href="(/job_detail/[^"]+\.html[^"]*)"[^>]*>(.{100,4000}?)</a>', html, re.S):
        block = m.group(2)
        tm = re.search(r"<b[^>]*>(.*?)</b>", block, re.S)
        sm = re.search(r'class="salary"[^>]*>(.*?)</span>', block, re.S)
        am = re.search(r'class="company-location"[^>]*>(.*?)</span>', block, re.S)
        tags = re.findall(r'class="tag-list-item"[^>]*>(.*?)</span>', block, re.S)
        if tm:
            title = _strip(tm.group(1)).strip()[:60]
            jobs.append({"title": title,
                         "salary": _strip(sm.group(1)).strip() if sm else "",
                         "city": _strip(am.group(1)).strip() if am else "",
                         "exp_edu": "/".join(_strip(t).strip() for t in tags if t.strip())[:30],
                         "kind": "实习" if "实习" in title else "社招",
                         "url": "https://www.zhipin.com" + m.group(1)})
    total = re.search(r'共\s*(\d+)\s*个职位', html)
    return jobs, (int(total.group(1)) if total else None)

def parse_lagou_html(html, company_short):
    r"""拉勾列表:anchor 到 /jobs/\d+.html,卡片内取薪资/城市;按公司名过滤。"""
    jobs = []
    for m in re.finditer(r'<a[^>]*href="((?:https?:)?//www\.lagou\.com/jobs/\d+\.html[^"]*)"[^>]*>(.{100,3000}?)</a>', html, re.S):
        block = _strip(m.group(2))
        block = re.sub(r"\s+", " ", block).strip()
        if company_short and company_short not in block:
            continue
        title = block.split(" ")[0][:60]
        sm = re.search(r"(\d+k-\d+k|\d+-\d+k|面议)", block)
        cm = re.search(r"(北京|上海|广州|深圳|杭州|成都|西安|武汉|南京|苏州|合肥|长沙|重庆|天津)", block)
        jobs.append({"title": title, "salary": sm.group(1) if sm else "",
                     "city": cm.group(1) if cm else "", "kind": "实习" if "实习" in title else "社招",
                     "url": ("https:" + m.group(1)) if m.group(1).startswith("//") else m.group(1)})
    return jobs, None

def cdp_grab_fast(url, port=CDP_PORT, max_ms=7000, marker=r"job-name|job-card|热招职位|招聘职位|salary|职位"):
    """BOSS 反爬会在数秒内把页面置 about:blank。从 commit 起每 300ms 抢一次内容,
    命中职位标记或长度见顶即返回最佳快照。"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{port}")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="commit", timeout=15000)
        except Exception:
            pass
        best, t = "", 0
        while t < max_ms:
            page.wait_for_timeout(300)
            t += 300
            try:
                html = page.content()
            except Exception:
                break
            if len(html) > len(best):
                best = html
            if re.search(marker, html):
                break
        page.close()
        return best

def channel_boss_cdp(company, sleep):
    if not cdp_ok():
        return {"jobs": [], "note": "CDP 未连接:先运行 python3 job_hunter.py setup-browser 并在窗口里登录 BOSS"}
    # ① 用 Firecrawl search 解析企业页 URL(BOSS 自家搜索页是 JS 壳,不好使)
    results = fc_search(f"{company} BOSS直聘 招聘", limit=2, scrape=False)
    g = next((r for r in results if re.search(r"(?:m\.)?zhipin\.com/gongsi/", r.get("url", ""))), None)
    if not g:
        return {"jobs": [], "note": "BOSS直聘未找到企业页"}
    url = re.sub(r"^https?://m\.zhipin\.com", "https://www.zhipin.com", g["url"].split("?")[0])
    time.sleep(sleep)
    # ② CDP 抢快照(反爬置空前)
    html = cdp_grab_fast(url)
    jobs, total = parse_boss(html)
    note = ""
    if not jobs:
        if re.search(r"招聘职位\(0\)|0\s*在招职位", html):
            total, note = 0, "BOSS 页标在招 0(该公司当前真无岗位)"
        else:
            note = "企业页打开但职位卡解析为空(快照被反爬置空或未登录)"
    return {"portal": url, "jobs": dedup(jobs), "total": total, "note": note}

def channel_lagou_cdp(company, sleep):
    """拉勾:Firecrawl search 解析企业页 → CDP 抓企业页。职位列表需登录后由受控浏览器点击 tab 加载;
    未登录时只能拿到公司概况与在招数量。"""
    if not cdp_ok():
        return {"jobs": [], "note": "CDP 未连接:先运行 python3 job_hunter.py setup-browser 并在窗口里登录拉勾"}
    results = fc_search(f"{company} 拉勾 招聘", limit=2, scrape=False)
    g = next((r for r in results if re.search(r"lagou\.com/gongsi/", r.get("url", ""))), None)
    if not g:
        return {"jobs": [], "note": "拉勾未找到企业页"}
    time.sleep(sleep)
    html = cdp_grab_fast(g["url"], marker=r"招聘职位|wn/jobs|在招")
    short = re.sub(r"(股份|有限|科技|公司|\(.*?\)|（.*?）)", "", company)[:6]
    jobs, total = parse_lagou_html(html, short)
    cnt = re.search(r"(\d+)\s*个\s*\n?\s*招聘职位|招聘职位（(\d+)）", html)
    if cnt and not total:
        total = int(cnt.group(1) or cnt.group(2))
    note = "" if jobs else "拉勾职位列表需登录后点击「招聘职位」tab 加载,本次仅获企业概况(未登录或未解析到)"
    return {"portal": g["url"], "jobs": dedup(jobs), "total": total, "note": note}


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
def hunt(company, channels, sleep, subfield="", industry="", use_cdp=False):
    rec = {"company": company, "subfield": subfield, "industry": industry,
           "channels": {}, "jobs": []}
    for i, ch in enumerate(channels):
        label, fn = CHANNELS[ch]
        if use_cdp and ch == "boss":
            fn = channel_boss_cdp
        elif use_cdp and ch == "lagou":
            fn = channel_lagou_cdp
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
               subfield=args.subfield, industry="", use_cdp=args.cdp)
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
                                 subfield=e.get("subfield", ""), industry=e.get("industry", ""),
                                 use_cdp=args.cdp))
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


def cmd_setup_browser(args):
    os.makedirs(CHROME_PROFILE, exist_ok=True)
    if cdp_ok():
        print(f"受控 Chrome 已在运行(端口 {CDP_PORT})")
    else:
        subprocess.Popen([CHROME, f"--remote-debugging-port={CDP_PORT}",
                          f"--user-data-dir={CHROME_PROFILE}",
                          "https://www.zhipin.com", "https://www.lagou.com", "https://www.liepin.com"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(30):
            time.sleep(1)
            if cdp_ok():
                break
        if not cdp_ok():
            sys.exit("Chrome 启动后 CDP 仍不可达,请检查是否被其他 Chrome 实例占用")
        print(f"受控 Chrome 已启动(独立 profile: {CHROME_PROFILE})")
    print("请在刚打开的窗口里登录 BOSS直聘 / 拉勾(各登一次,登录态会保留在该 profile),")
    print("然后即可运行:python3 job_hunter.py hunt --company XXX --channels boss,lagou --cdp")


def main():
    if not API_KEY and not (len(sys.argv) > 1 and sys.argv[1] in ("make-base", "push-base", "setup-browser")):
        sys.exit("请先 export FIRECRAWL_API_KEY=fc-xxx")
    ap = argparse.ArgumentParser(prog="job_hunter", description="公司名 → 全渠道在招岗位")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("hunt", help="抓一家公司")
    p.add_argument("--company", required=True)
    p.add_argument("--channels", default="official,liepin,boss,lagou")
    p.add_argument("--subfield", default="")
    p.add_argument("--sleep", type=float, default=8)
    p.add_argument("--cdp", action="store_true", help="BOSS/拉勾改走已登录 Chrome(CDP 接管)")
    p.add_argument("--out", default="hunt_out.json")
    p.set_defaults(fn=cmd_hunt)

    p = sub.add_parser("hunt-batch", help="按展商名单批量抓")
    p.add_argument("--exhibitors", required=True, help="exhibitors.json 路径")
    p.add_argument("--channels", default="official,liepin")
    p.add_argument("--subfield", default="")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--sleep", type=float, default=8)
    p.add_argument("--cdp", action="store_true", help="BOSS/拉勾改走已登录 Chrome(CDP 接管)")
    p.add_argument("--out", default="hunt_batch_out.json")
    p.set_defaults(fn=cmd_hunt_batch)

    p = sub.add_parser("setup-browser", help="启动受控 Chrome 并提示登录(BOSS/拉勾)")
    p.set_defaults(fn=cmd_setup_browser)

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
