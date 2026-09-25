"""只读性能观察脚本：对比 2.10.0 与 3.0.0（性能补丁）时，用同一口径采样浏览器和服务进程。

只用 psutil 读进程信息，不连接 CDP（连接 CDP 会解除标签页冻结、干扰观察）。

用法（项目启动后另开一个终端）：
    python perf_watch.py                         # 默认浏览器端口 9222、服务端口 8199，每 5 秒一行
    python perf_watch.py --interval 2 --csv v3.csv
    python perf_watch.py --duration 600 --csv v2.csv   # 采样 10 分钟后自动结束
    python perf_watch.py --browser-port 9333 --api-port 8200

Ctrl+C 结束时打印平均值 / 峰值。两个版本各跑一次同样的操作，再对比两份汇总或 CSV。

列说明：
    chrome_MB   浏览器全部进程内存合计（Windows=private，Linux=rss-shared，macOS=rss；与 Chrome 任务管理器“内存占用”接近）
    rend        渲染进程数量          max_rend_MB  最大的渲染进程内存（通常就是你的对话标签页）
    idle_rend   本周期 CPU≈0 的渲染进程数（后台标签被冻结后会增加）
    chrome_cpu  浏览器全部进程 CPU（100% = 占满 1 个核）
    py_MB / py_cpu  服务进程（main.py）的内存 / CPU
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
import time

try:
    import psutil
except ImportError:  # pragma: no cover
    sys.exit("需要 psutil：pip install psutil（项目 requirements.txt 已包含）")


def mem_mb(proc: "psutil.Process") -> float:
    info = proc.memory_info()
    if sys.platform.startswith("win"):
        value = getattr(info, "private", 0) or info.rss
    elif sys.platform.startswith("linux"):
        value = info.rss - getattr(info, "shared", 0)
    else:
        value = info.rss
    return max(value, 0) / 1048576


def find_browser(port: int):
    flag = f"--remote-debugging-port={port}"
    for proc in psutil.process_iter(["cmdline"]):
        try:
            cmd = proc.info["cmdline"] or []
            if any(part == flag for part in cmd) and not any(p.startswith("--type=") for p in cmd):
                return proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def find_service(api_port: int):
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == api_port and conn.pid:
                return psutil.Process(conn.pid)
    except (psutil.AccessDenied, PermissionError, OSError):
        pass
    for proc in psutil.process_iter(["cmdline"]):
        try:
            cmd = " ".join(proc.info["cmdline"] or [])
            if "python" in cmd.lower() and ("main.py" in cmd or "uvicorn" in cmd):
                return proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def cpu_seconds(proc: "psutil.Process") -> float:
    t = proc.cpu_times()
    return t.user + t.system


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--browser-port", type=int, default=9222, help="BROWSER_PORT（默认 9222）")
    ap.add_argument("--api-port", type=int, default=8199, help="APP_PORT（默认 8199）")
    ap.add_argument("--interval", type=float, default=5.0, help="采样间隔秒数（默认 5）")
    ap.add_argument("--csv", help="同时写入 CSV 文件")
    ap.add_argument("--duration", type=float, default=0, help="采样多少秒后自动结束并打印汇总（默认 0 = 直到 Ctrl+C）")
    args = ap.parse_args()

    writer = None
    fh = None
    if args.csv:
        fh = open(args.csv, "w", newline="", encoding="utf-8")
        writer = csv.writer(fh)
        writer.writerow(["time", "chrome_MB", "rend", "max_rend_MB", "idle_rend", "chrome_cpu", "py_MB", "py_cpu"])

    last_cpu: dict[int, float] = {}
    rows = []
    last_t = time.monotonic()
    deadline = last_t + args.duration if args.duration > 0 else None
    header = f"{'time':8} {'chrome_MB':>9} {'rend':>4} {'max_rend_MB':>11} {'idle_rend':>9} {'chrome_cpu':>10} {'py_MB':>7} {'py_cpu':>7}"
    print(header)
    try:
        while True:
            browser = find_browser(args.browser_port)
            service = find_service(args.api_port)
            now = time.monotonic()
            dt = max(now - last_t, 1e-6)
            last_t = now

            procs = []
            if browser is not None:
                try:
                    procs = [browser] + browser.children(recursive=True)
                except psutil.NoSuchProcess:
                    procs = []
            chrome_mb = chrome_cpu = max_rend = 0.0
            rend = idle_rend = 0
            seen = {}
            for p in procs:
                try:
                    mb = mem_mb(p)
                    cs = cpu_seconds(p)
                    is_renderer = "--type=renderer" in (p.cmdline() or [])
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
                seen[p.pid] = cs
                delta = (cs - last_cpu[p.pid]) if p.pid in last_cpu else None
                chrome_mb += mb
                if delta is not None:
                    chrome_cpu += delta / dt * 100
                if is_renderer:
                    rend += 1
                    max_rend = max(max_rend, mb)
                    if delta is not None and delta / dt < 0.002:
                        idle_rend += 1

            py_mb = py_cpu = 0.0
            if service is not None:
                try:
                    py_mb = mem_mb(service)
                    cs = cpu_seconds(service)
                    key = -service.pid
                    if key in last_cpu:
                        py_cpu = (cs - last_cpu[key]) / dt * 100
                    seen[key] = cs
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            first_sample = not last_cpu
            last_cpu = seen

            if browser is None:
                print(f"{time.strftime('%H:%M:%S')} 未找到 --remote-debugging-port={args.browser_port} 的浏览器，等待中…")
            elif not first_sample:
                row = [time.strftime("%H:%M:%S"), round(chrome_mb, 1), rend, round(max_rend, 1), idle_rend,
                       round(chrome_cpu, 1), round(py_mb, 1), round(py_cpu, 1)]
                rows.append(row)
                print(f"{row[0]:8} {row[1]:>9} {row[2]:>4} {row[3]:>11} {row[4]:>9} {row[5]:>9}% {row[6]:>7} {row[7]:>6}%")
                if writer:
                    writer.writerow(row)
                    fh.flush()
            if deadline is not None and time.monotonic() + args.interval > deadline:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        if fh:
            fh.close()
    if rows:
        print(f"\n共 {len(rows)} 个样本")
        for idx, name in ((1, "chrome_MB"), (3, "max_rend_MB"), (5, "chrome_cpu%"), (6, "py_MB"), (7, "py_cpu%")):
            vals = [r[idx] for r in rows]
            print(f"  {name:12} 平均 {statistics.mean(vals):8.1f}   峰值 {max(vals):8.1f}   最后 {vals[-1]:8.1f}")


if __name__ == "__main__":
    main()
