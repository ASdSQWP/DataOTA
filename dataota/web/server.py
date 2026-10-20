"""本地 HTTP 服务：静态前端 + JSON API（标准库实现，无第三方依赖）。

    python -m dataota web --port 8000

默认只监听 127.0.0.1；如需局域网访问可传 --host 0.0.0.0（Demo 无鉴权，请勿暴露公网）。
"""
from __future__ import annotations

import json
import mimetypes
import re
import socket
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .api import Dashboard, RunManager

STATIC_DIR = Path(__file__).resolve().parent / "static"

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("application/json", ".json")


class Handler(BaseHTTPRequestHandler):
    server_version = "DataOTA/0.1"
    protocol_version = "HTTP/1.1"
    dashboard: Dashboard
    runner: RunManager

    # ------------------------------------------------------------ 基础响应
    def log_message(self, fmt: str, *args) -> None:  # 精简访问日志
        if str(args[1] if len(args) > 1 else "")[:3] in ("200", "304"):
            return
        sys.stderr.write(f"[web] {self.address_string()} {fmt % args}\n")

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _json(self, status: int, obj: object) -> None:
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    # ------------------------------------------------------------ 入口
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
        if path == "/media":
            self._media(query.get("path", ""))
            return
        if path.startswith("/api/"):
            self._api("GET", path, query, None)
        else:
            self._static(path)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > 3 * 1024 * 1024:                       # 上传收件箱：base64 图像 ≤ 1.5MB
            self._error(413, "请求体过大")
            return
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
            if not isinstance(payload, dict):
                raise ValueError("请求体必须是 JSON 对象")
        except (ValueError, UnicodeDecodeError) as exc:
            self._error(400, f"JSON 解析失败：{exc}")
            return
        if not path.startswith("/api/"):
            self._error(404, "Not found")
            return
        self._api("POST", path, query, payload)

    # ------------------------------------------------------------ API 路由
    def _api(self, method: str, path: str, q: dict[str, str], body: dict | None) -> None:
        dash, runner = self.dashboard, self.runner
        try:
            if method == "GET" and path == "/api/health":
                self._json(200, {"ok": True, "workdir": str(dash.workdir), "version": "0.1.0"})
                return
            if method == "GET" and path == "/api/overview":
                self._json(200, dash.overview())
                return
            if method == "GET" and path == "/api/rounds":
                self._json(200, {"items": dash.rounds(), "total": len(dash.rounds())})
                return
            m = re.fullmatch(r"/api/rounds/(\d+)", path)
            if method == "GET" and m:
                rep = dash.round_report(int(m.group(1)))
                if rep is None:
                    self._error(404, "该轮次没有报告")
                else:
                    self._json(200, rep)
                return
            if method == "GET" and path == "/api/datasets":
                self._json(200, dash.datasets())
                return
            m = re.fullmatch(r"/api/datasets/([^/]+)", path)
            if method == "GET" and m:
                detail = dash.dataset_detail(m.group(1))
                if detail is None:
                    self._error(404, "数据集不存在")
                else:
                    self._json(200, detail)
                return
            if method == "GET" and path == "/api/models":
                self._json(200, dash.models())
                return
            if method == "GET" and path == "/api/releases":
                self._json(200, dash.releases())
                return
            if method == "GET" and path == "/api/fleet":
                self._json(200, dash.fleet())
                return
            if method == "GET" and path == "/api/monitor":
                self._json(200, dash.monitor())
                return
            if method == "GET" and path == "/api/telemetry":
                rnd = int(q["round"]) if str(q.get("round", "")).isdigit() else None
                self._json(200, dash.telemetry(rnd))
                return
            if method == "GET" and path == "/api/annotations":
                self._json(200, dash.annotations(
                    page=int(q.get("page", 1) or 1), size=int(q.get("size", 25) or 25),
                    split=q.get("split", ""), synthetic=q.get("synthetic", ""),
                    defect_type=q.get("defect_type", ""), gate=q.get("gate", ""),
                    human=q.get("human", ""), q=q.get("q", "")))
                return
            m = re.fullmatch(r"/api/annotations/([^/]+)", path)
            if method == "GET" and m:
                detail = dash.annotation_detail(m.group(1))
                if detail is None:
                    self._error(404, "标注不存在")
                else:
                    self._json(200, detail)
                return
            if method == "GET" and path == "/api/audit":
                self._json(200, dash.audit(
                    page=int(q.get("page", 1) or 1), size=int(q.get("size", 30) or 30),
                    action=q.get("action", ""), actor=q.get("actor", ""), q=q.get("q", "")))
                return
            if method == "GET" and path == "/api/audit.csv":
                csv_text = dash.audit_csv(action=q.get("action", ""), actor=q.get("actor", ""),
                                          q=q.get("q", ""))
                self._send(200, csv_text.encode("utf-8-sig"), "text/csv; charset=utf-8")
                return
            if method == "GET" and path == "/api/review":
                self._json(200, dash.review(include_resolved=q.get("resolved") == "1"))
                return
            m = re.fullmatch(r"/api/review/([^/]+)", path)
            if method == "POST" and m:
                try:
                    self._json(200, dash.submit_review(m.group(1), body or {}))
                except FileNotFoundError as exc:
                    self._error(404, str(exc))
                except (ValueError, KeyError) as exc:
                    self._error(400, str(exc))
                return
            if method == "GET" and path == "/api/trace":
                kind = q.get("kind", "")
                ident = q.get("id", "")
                if not ident:
                    self._error(400, "缺少 id 参数")
                    return
                self._json(200, dash.trace(kind, ident))
                return
            if method == "GET" and path == "/api/lineage/graph":
                self._json(200, dash.lineage_graph(
                    focus=q.get("focus", ""),
                    depth=int(q.get("depth", 2) or 2),
                    max_nodes=int(q.get("max_nodes", 90) or 90)))
                return
            if method == "GET" and path == "/api/lineage/search":
                self._json(200, dash.search_nodes(q.get("q", ""), limit=int(q.get("limit", 20) or 20)))
                return
            if method == "GET" and path == "/api/system":
                self._json(200, dash.system())
                return
            if method == "GET" and path == "/api/meta":
                self._json(200, dash.meta())
                return
            if method == "GET" and path == "/api/zoo":
                self._json(200, dash.zoo())
                return
            if method == "GET" and path == "/api/world":
                self._json(200, dash.world())
                return
            if method == "GET" and path == "/api/world/clips":
                self._json(200, dash.clips(page=_int(q, "page", 1), size=_int(q, "size", 24), view=q.get("view", ""),
                                           model=q.get("model", ""), status=q.get("status", ""),
                                           round_no=q.get("round", ""), q=q.get("q", "")))
                return
            m = re.fullmatch(r"/api/world/clips/([^/]+)", path)
            if method == "GET" and m:
                detail = dash.clip_detail(m.group(1))
                if detail is None:
                    self._error(404, "片段不存在")
                else:
                    self._json(200, detail)
                return
            if method == "GET" and path == "/api/detectors":
                self._json(200, dash.detectors())
                return
            if method == "GET" and path == "/api/detections":
                self._json(200, dash.detections(page=_int(q, "page", 1), size=_int(q, "size", 24),
                                                outcome=q.get("outcome", ""), label=q.get("label", ""),
                                                source=q.get("source", ""), round_no=q.get("round", ""),
                                                q=q.get("q", "")))
                return
            if method == "GET" and path == "/api/sources":
                self._json(200, dash.sources())
                return
            if method == "GET" and path == "/api/ingest":
                self._json(200, dash.ingest_events(page=_int(q, "page", 1), size=_int(q, "size", 24),
                                                   source=q.get("source", ""), round_no=q.get("round", ""),
                                                   flag=q.get("flag", ""), q=q.get("q", "")))
                return
            if method == "GET" and path == "/api/inbox":
                self._json(200, dash.inbox())
                return
            if method == "POST" and path == "/api/inbox":
                try:
                    self._json(201, dash.upload(body or {}))
                except ValueError as exc:
                    self._error(400, str(exc))
                return
            if method == "POST" and path == "/api/run":
                payload = body or {}
                seed = payload.get("seed_size", 60)
                started = runner.start(rounds=int(payload.get("rounds", 3) or 3),
                                       seed_size=int(60 if seed is None or seed == "" else seed),
                                       fresh=bool(payload.get("fresh", False)),
                                       include_inbox=bool(payload.get("include_inbox", True)))
                if started:
                    self._json(202, {"ok": True, "status": runner.status()})
                else:
                    self._error(409, "已有任务在运行")
                return
            if method == "GET" and path == "/api/run/status":
                self._json(200, runner.status())
                return
            self._error(404, f"未知接口 {path}")
        except Exception as exc:  # 接口级兜底，避免一个坏响应拖垮服务
            traceback.print_exc()
            self._error(500, f"{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------ 媒体文件
    MEDIA_TYPES = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp",
    }

    def _media(self, rel: str) -> None:
        """只读回放 workdir 内的图像，用于标注样例与审核台预览。"""
        s = (rel or "").replace("\\", "/").lstrip("/")
        if not s or ".." in Path(s).parts:
            self._error(404, "文件不存在")
            return
        target = (self.dashboard.workdir / s).resolve()
        try:
            target.relative_to(self.dashboard.workdir)
        except ValueError:
            self._error(403, "Forbidden")
            return
        ctype = self.MEDIA_TYPES.get(target.suffix.lower())
        if not ctype or not target.is_file():
            self._error(404, "文件不存在")
            return
        try:
            self._send(200, target.read_bytes(), ctype)
        except OSError:
            self._error(500, "读取媒体文件失败")

    # ------------------------------------------------------------ 静态文件
    def _static(self, path: str) -> None:
        rel = path.lstrip("/") or "index.html"
        if rel.endswith("/"):
            rel += "index.html"
        target = (STATIC_DIR / rel).resolve()
        try:
            target.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self._error(403, "Forbidden")
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            self._send(404, b"<!doctype html><meta charset='utf-8'><h1>404</h1>"
                            b"<p>DataOTA console: file not found.</p>", "text/html; charset=utf-8")
            return
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json", "image/svg+xml"):
            ctype += "; charset=utf-8"
        try:
            self._send(200, target.read_bytes(), ctype)
        except OSError:
            self._error(500, "读取静态文件失败")


def _int(q: dict[str, str], key: str, default: int) -> int:
    try:
        return int(q.get(key) or default)
    except (TypeError, ValueError):
        return default


def _make_handler(dash: Dashboard, runner: RunManager):
    return type("BoundHandler", (Handler,), {"dashboard": dash, "runner": runner})


def serve(config_path: Path | str, workdir: Path | str | None = None,
          host: str = "127.0.0.1", port: int = 8000, open_browser: bool = True) -> None:
    from ..core.infra import cfg_get, load_config

    cfg = load_config(config_path)
    wd = Path(workdir or cfg_get(cfg, "run.workdir", "runs/demo")).expanduser().resolve()
    wd.mkdir(parents=True, exist_ok=True)
    dash = Dashboard(wd, config_path)
    runner = RunManager(config_path, wd)
    handler = _make_handler(dash, runner)

    httpd = None
    bound = port
    last_error: Exception | None = None
    for candidate in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer((host, candidate), handler)
            bound = candidate
            break
        except OSError as exc:
            last_error = exc
            continue
    if httpd is None:
        raise SystemExit(f"端口 {port}-{port + 19} 都被占用：{last_error}")
    httpd.daemon_threads = True

    url = f"http://{host if host != '0.0.0.0' else '127.0.0.1'}:{bound}/"
    print("DataOTA 控制台已启动")
    print(f"  地址    : {url}")
    print(f"  工作目录: {wd}")
    print(f"  配置    : {Path(config_path).resolve()}")
    print("  停止    : Ctrl+C")
    if host == "0.0.0.0":
        try:
            lan = socket.gethostbyname(socket.gethostname())
            print(f"  局域网  : http://{lan}:{bound}/")
        except OSError:
            pass
    if open_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止…")
    finally:
        httpd.server_close()
        dash.close()
        print("已停止")
