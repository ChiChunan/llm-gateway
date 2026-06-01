#!/usr/bin/env python3
"""
Codex Keepalive Daemon
- 定期发 codex exec "hi" 刷新 rate_limits
- 从新产生的 session JSONL 文件中解析 rate_limits
- HTTP API 端口 18432
"""
import glob, json, logging, os, random, subprocess, sys, threading, time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stderr),
        logging.FileHandler("/tmp/codex_keepalive.log", mode="a"),
    ],
)
log = logging.getLogger("codex-keepalive")
sys.stdout.flush()
sys.stderr.flush()

PORT = 18432
STATUS_FILE = "/tmp/codex_status.json"
SESSIONS_DIR = os.path.expanduser("~/.codex/sessions")
BASE_INTERVAL = 1200  # 20分钟基准
JITTER_RANGE = 300   # ±5分钟随机波动（15-25分钟）
NIGHT_START = 22     # 夜间静默开始 22:00
NIGHT_END = 9         # 夜间静默结束 09:00
RETENTION_DAYS = 7    # session 文件保留天数

# 轮换的轻量提示词列表（都是 1 token，极轻量）
_PROMPTS = [
    "hi",
    "hi.",
    "ok",
    "y",
    "go",
    "1",
]


def is_night():
    """判断当前是否在夜间静默期"""
    hour = datetime.now().hour
    if NIGHT_START > NIGHT_END:
        return hour >= NIGHT_START or hour < NIGHT_END
    return NIGHT_START <= hour < NIGHT_END


def cleanup_old_sessions():
    """删除超过保留期的 rollout JSONL 文件及空目录"""
    cutoff = time.time() - RETENTION_DAYS * 86400
    pattern = f"{SESSIONS_DIR}/**/**/**/rollout-*.jsonl"
    removed = 0
    removed_size = 0
    try:
        for filepath in glob.glob(pattern, recursive=True):
            if os.path.getmtime(filepath) < cutoff:
                size = os.path.getsize(filepath)
                os.remove(filepath)
                removed += 1
                removed_size += size
                # 清理空目录
                parent = os.path.dirname(filepath)
                while parent and parent != SESSIONS_DIR and not os.listdir(parent):
                    os.rmdir(parent)
                    parent = os.path.dirname(parent)
        if removed > 0:
            log.info("清理完成: 移除 %d 个旧 session 文件，释放 %.1f MB", removed, removed_size / 1024 / 1024)
    except Exception as e:
        log.warning("清理 session 文件失败: %s", e)


def find_latest_rollout():
    """找最新的 rollout JSONL 文件"""
    pattern = f"{SESSIONS_DIR}/**/**/**/rollout-*.jsonl"
    files = glob.glob(pattern, recursive=True)
    if not files:
        return None, None
    files.sort(key=os.path.getmtime, reverse=True)
    return files[0], os.path.getmtime(files[0])


def parse_rate_limits(filepath):
    """从 JSONL 文件解析 rate_limits"""
    result = {
        "model": None, "plan": None,
        "hourly_limit": None, "weekly_limit": None,
        "data_age_seconds": None, "source_file": None,
    }
    try:
        with open(filepath) as f:
            for line in f:
                try:
                    d = json.loads(line)
                    pl = d.get("payload", {})
                    if pl.get("type") == "token_count":
                        rl = pl.get("rate_limits", {})
                        if rl:
                            primary = rl.get("primary", {})
                            secondary = rl.get("secondary", {})
                            result["hourly_limit"] = {
                                "pct_left": 100 - int(primary.get("used_percent", 0)),
                                "window_minutes": primary.get("window_minutes", 300),
                            }
                            result["weekly_limit"] = {
                                "pct_left": 100 - int(secondary.get("used_percent", 0)),
                                "window_minutes": secondary.get("window_minutes", 10080),
                            }
                            result["plan"] = rl.get("plan_type")
                            result["model"] = rl.get("model")
                            result["source_file"] = os.path.basename(filepath)

                            ts_str = d.get("timestamp", "")
                            if ts_str:
                                try:
                                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                                    age = (datetime.now(ts.tzinfo) - ts).total_seconds()
                                    result["data_age_seconds"] = int(age)
                                except Exception:
                                    pass
                            return result
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        log.warning("读取 JSONL 失败: %s", e)
    return result


def send_keepalive():
    """发 codex exec 请求，刷新 rate_limits（方案2：shell文件重定向避免stdin检测）"""
    import tempfile, shlex
    prompt = random.choice(_PROMPTS)
    log.info("发送 codex exec (prompt=%r)...", prompt)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(prompt + "\n")
            tmp = f.name
        # 用 shell 重定向 < file 而非 pipe，避免 Codex 检测到非交互式 stdin
        cmd = f"codex exec --skip-git-repo-check -m gpt-5.4-mini < {shlex.quote(tmp)}"
        result = subprocess.run(
            cmd,
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=120,
        )
        if result.returncode != 0:
            err_msg = result.stderr.strip() if result.stderr else "(no stderr)"
            log.warning("codex exec 失败 [rc=%s]: %s", result.returncode, err_msg[:200])
            return False
        log.info("codex exec 成功")
        return True
    except subprocess.TimeoutExpired:
        log.warning("codex exec 超时")
        return False
    except Exception as e:
        log.warning("codex exec 异常: %s", e)
        return False
    finally:
        if tmp:
            os.unlink(tmp)


def query_and_update():
    """发请求 -> 等 JSONL 写入 -> 读最新 session"""
    latest_before, latest_mtime_before = find_latest_rollout()

    if not send_keepalive():
        return None

    # 等 JSONL 写入
    time.sleep(4)

    # 找最新的 session
    attempts = 0
    while attempts < 10:
        filepath, mtime = find_latest_rollout()
        if filepath and (latest_mtime_before is None or mtime > latest_mtime_before):
            break
        time.sleep(1)
        attempts += 1

    if not filepath:
        log.warning("未找到新 session，使用 fallback")
        filepath, _ = find_latest_rollout()

    if not filepath:
        return None

    return parse_rate_limits(filepath)


def write_status(parsed: dict):
    with open(STATUS_FILE, "w") as f:
        json.dump(
            {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"), "parsed": parsed},
            f, ensure_ascii=False, indent=2,
        )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, indent=2)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            self.send_json({"status": "ok"})
        elif path == "/quota":
            if not os.path.exists(STATUS_FILE):
                self.send_json({"error": "no data yet"}, status=503)
                return
            with open(STATUS_FILE) as f:
                self.send_json(json.load(f))
        else:
            self.send_error(404)


def next_interval():
    """计算下次查询间隔（带随机波动）"""
    jitter = random.randint(-JITTER_RANGE, JITTER_RANGE)
    return BASE_INTERVAL + jitter


def main():
    log.info("Codex Keepalive Daemon | PORT=%s | BASE_INTERVAL=%ss | JITTER=±%ss | NIGHT=%d-%d | RETENTION=%ddays",
              PORT, BASE_INTERVAL, JITTER_RANGE, NIGHT_START, NIGHT_END, RETENTION_DAYS)

    server = HTTPServer(("127.0.0.1", PORT), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    log.info("HTTP API: http://127.0.0.1:%s/quota", PORT)

    last_cleanup = 0  # 上次清理时间戳

    while True:
        try:
            # 每周清理一次（距上次清理超过 7 天）
            if time.time() - last_cleanup > RETENTION_DAYS * 86400:
                cleanup_old_sessions()
                last_cleanup = time.time()

            night = is_night()
            if night:
                log.info("夜间静默期（%d:00-%d:00），跳过本次查询", NIGHT_START, NIGHT_END)
            else:
                parsed = query_and_update()
                if parsed:
                    write_status(parsed)
                    h = (parsed.get("hourly_limit") or {}).get("pct_left", "?")
                    w = (parsed.get("weekly_limit") or {}).get("pct_left", "?")
                    # 如果周额度为 0，5h 额度强制归零（避免 5h 99% / 周 0% 的误导）
                    hl = parsed.get("hourly_limit")
                    if isinstance(w, int) and w == 0 and isinstance(h, int) and h > 0 and hl is not None:
                        hl["pct_left"] = 0
                        h = 0
                        log.info("周额度为 0，5h 额度强制归零")
                    age = parsed.get("data_age_seconds")
                    age_str = f" ({age}s ago)" if age is not None else ""
                    src = parsed.get("source_file", "?")
                    log.info("状态: 5h=%s%% weekly=%s%% src=%s%s", h, w, src, age_str)
                else:
                    log.warning("查询失败")

        except Exception as e:
            log.error("异常: %s", e)

        # 夜间静默期用短间隔轮询，避免睡过头
        if is_night():
            wait = 300  # 夜间每5分钟检查一次是否出静默期
        else:
            wait = next_interval()
        log.info("等待 %d 秒...", wait)
        time.sleep(wait)


if __name__ == "__main__":
    main()
