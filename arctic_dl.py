#!/usr/bin/env python3
"""从 Arctic Shift 公开接口按月下载一个 subreddit 的帖子／回复，存成 <out>/<kind>/YYYY-MM.jsonl.gz（全字段）。
对方限速（"Timeout. Maybe slow down a bit"），所以单线程、按月分窗、每次请求（limit=auto，一次最多 1000 条）间隔 --pause 秒，失败指数退避。
断点续跑：已完成的月份是 .jsonl.gz，跳过；没写完的 .part 改名留作 .stale 后重下该月（不删任何文件）。
磁盘剩余低于 --min-free-gb 就停。
用法（HK，脱离会话）：
  setsid nohup python3 tools/arctic_dl.py --sub schizophrenia --out /root/data/reddit/schizophrenia \
      --kinds posts,comments --start 2012-10 > /root/data/reddit/schizophrenia.log 2>&1 < /dev/null &
"""
import argparse, gzip, json, os, shutil, sys, time, urllib.parse, urllib.request
from datetime import datetime, timezone

API = "https://arctic-shift.photon-reddit.com/api/{kind}/search"


def months(start, end):
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    while (y, m) <= (ey, em):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def ts(y, m):
    return int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp())


def fetch(kind, params, pause):
    url = API.format(kind=kind) + "?" + urllib.parse.urlencode(params)
    wait = pause
    for attempt in range(12):
        time.sleep(wait)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "oaf-case-miner research downloader"})
            with urllib.request.urlopen(req, timeout=60) as r:
                body = json.load(r)
            if body.get("error"):
                raise RuntimeError(body["error"])
            return body["data"] or []
        except Exception as e:  # 422 超时、429、5xx、网络抖动都退避重试
            err = getattr(e, "code", "") or e
            print(f"  重试 {attempt + 1}: {err}", flush=True)
            wait = min(wait * 2 + 2, 300)
    raise SystemExit(f"连续失败，停在 {url}")


def free_gb(path):
    return shutil.disk_usage(path).free / 2**30


def dl_month(kind, sub, y, m, out_dir, a):
    final = os.path.join(out_dir, f"{y:04d}-{m:02d}.jsonl.gz")
    if os.path.exists(final):
        return None
    part = final + ".part"
    if os.path.exists(part):
        os.rename(part, f"{part}.stale{int(time.time())}")
    lo, hi = ts(y, m), ts(*((y + 1, 1) if m == 12 else (y, m + 1)))
    after, seen, n = lo - 1, set(), 0  # after 不含边界，月初第一秒要算进来
    with gzip.open(part, "wt", encoding="utf-8") as f:
        while True:
            rows = fetch(kind, {"subreddit": sub, "after": after, "before": hi,
                                "sort": "asc", "limit": "auto"}, a.pause)
            if not rows:
                break
            new = [r for r in rows if r["id"] not in seen]
            for r in new:
                seen.add(r["id"])
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += len(new)
            last = max(int(r["created_utc"]) for r in rows)
            # after 不含边界秒（实测）。退一秒再取、按 id 去重，翻页处同一秒的条目不丢；
            # 这一页全是旧条目（同一秒挤满一页）就越过这一秒，免得死循环
            after = last - 1 if new else last
    os.rename(part, final)
    return n


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sub", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--kinds", default="posts,comments")
    p.add_argument("--start", default="2012-10")
    p.add_argument("--end", default=datetime.now(timezone.utc).strftime("%Y-%m"))
    p.add_argument("--pause", type=float, default=1.0)
    p.add_argument("--min-free-gb", type=float, default=2.0)
    a = p.parse_args()
    for kind in a.kinds.split(","):
        out_dir = os.path.join(a.out, kind)
        os.makedirs(out_dir, exist_ok=True)
        for y, m in months(a.start, a.end):
            if free_gb(out_dir) < a.min_free_gb:
                raise SystemExit(f"磁盘剩余 {free_gb(out_dir):.1f}G，低于 {a.min_free_gb}G，停")
            t0 = time.time()
            n = dl_month(kind, a.sub, y, m, out_dir, a)
            if n is not None:
                print(f"{datetime.now():%F %T} {kind} {y:04d}-{m:02d} {n} 条 {time.time() - t0:.0f}s "
                      f"剩余 {free_gb(out_dir):.1f}G", flush=True)
    print(f"{datetime.now():%F %T} 全部完成", flush=True)


if __name__ == "__main__":
    main()
