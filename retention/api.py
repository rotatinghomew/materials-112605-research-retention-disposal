"""留样后端的 HTTP 入口（标准库 ThreadingHTTPServer，无第三方依赖）。

路由：
  GET  /health
  GET  /v1/reports?at=...                       档案员报告视图
  GET  /v1/stations/{id}/worklist?at=...        保管点交接清单
  GET  /v1/disposal/options?at=...&site=...     可处置范围
  GET  /v1/holds?at=...                         有效保全与覆盖身份
  GET  /v1/holds/{id}/recovery?at=...&known=... 保全追回分类
  GET  /v1/holds/{id}/write-off?known=...       迟到销毁冲减与复核充足性
  GET  /v1/confirmations?known=...              待确认方与记录缺口
  POST /v1/splits                               拆分
  POST /v1/reports                              报告登记/更正
  POST /v1/holds                                保全
  POST /v1/holds/release                        释放
  POST /v1/tickets                              处置申请
  POST /v1/receipts                             回执接入
"""
import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .timeparse import parse_time
from .model import DomainError
from .disposal import disposal_options, station_worklist
from .reports import archive_view
from .reconciliation import (
    classify_at_hold, confirmations_overview, late_destruction_reconciliation,
)
from . import holds as hold_rules


def _time(params, key):
    raw = params.get(key, [])
    if not raw:
        return None
    return parse_time(raw[0])


def make_handler(service):
    class ApiHandler(BaseHTTPRequestHandler):
        def _send(self, status, payload):
            data = json.dumps(payload, ensure_ascii=False, default=_default).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1_048_576:
                raise DomainError("请求须为非空 JSON 对象")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise DomainError("请求须为 JSON 对象")
            return payload

        def do_GET(self):
            try:
                parts = urlsplit(self.path)
                params = parse_qs(parts.query)
                path = parts.path
                bundle = service.bundle
                if path == "/health":
                    self._send(200, {"status": "ok"})
                elif path == "/v1/reports":
                    self._send(200, archive_view(
                        bundle, at=_time(params, "at"),
                        knowledge_at=_time(params, "known")))
                elif path.startswith("/v1/stations/") and path.endswith("/worklist"):
                    site_id = path.split("/")[3]
                    at = _time(params, "at") or datetime.now().astimezone()
                    self._send(200, station_worklist(
                        bundle, site_id, at,
                        knowledge_at=_time(params, "known")))
                elif path == "/v1/disposal/options":
                    at = _time(params, "at") or datetime.now().astimezone()
                    self._send(200, {"options": disposal_options(
                        bundle, at, station_id=(params.get("site") or [None])[0],
                        knowledge_at=_time(params, "known"))})
                elif path == "/v1/holds":
                    at = _time(params, "at") or datetime.now().astimezone()
                    self._send(200, {"holds": [
                        {"id": h.id, "scope": h.scope.value,
                         "references": list(h.references),
                         "effective": h.effective.isoformat(),
                         "covered": sorted(
                             hold_rules.covered_samples(bundle, h))}
                        for h in hold_rules.open_holds(bundle, at)]})
                elif path.startswith("/v1/holds/") and path.endswith("/recovery"):
                    hold_id = path.split("/")[3]
                    hold = bundle.holds[hold_id]
                    rows = [c.__dict__ for c in classify_at_hold(
                        bundle, hold, knowledge_at=_time(params, "known"))]
                    self._send(200, {"hold": hold_id, "conclusions": rows})
                elif path.startswith("/v1/holds/") and path.endswith("/write-off"):
                    hold_id = path.split("/")[3]
                    self._send(200, late_destruction_reconciliation(
                        bundle, bundle.holds[hold_id],
                        knowledge_at=_time(params, "known"),
                        at=_time(params, "at")))
                elif path == "/v1/confirmations":
                    self._send(200, {"pending": confirmations_overview(
                        bundle, knowledge_at=_time(params, "known"))})
                else:
                    self._send(404, {"message": "路径不存在"})
            except DomainError as exc:
                self._send(400, {"message": str(exc)})
            except KeyError as exc:
                self._send(404, {"message": f"记录不存在: {exc.args[0]}"})
            except ValueError as exc:
                self._send(400, {"message": str(exc)})

        def do_POST(self):
            try:
                body = self._body()
                path = urlsplit(self.path).path
                if path == "/v1/splits":
                    rec = service.split_sample(**body)
                    self._send(201, {"sample": rec.id, "origin": rec.origin,
                                     "parent": rec.parent, "mass": rec.mass})
                elif path == "/v1/reports":
                    rec = service.register_report(**body)
                    self._send(201, {"report": rec.id, "version": rec.version})
                elif path == "/v1/holds":
                    rec = service.place_hold(**body)
                    self._send(201, {"hold": rec.id, "state": rec.state.value})
                elif path == "/v1/holds/release":
                    rec = service.release_hold(
                        body["release_id"], body["legal"],
                        body["target_hold_id"], body["effective"],
                        body.get("reported"))
                    self._send(201, {"release": rec.id})
                elif path == "/v1/tickets":
                    rec, kind = service.submit_ticket(**body)
                    self._send(201, {"ticket": rec.id, "kind": kind})
                elif path == "/v1/receipts":
                    rec = service.record_receipt(**body)
                    self._send(201, {"receipt": rec.id, "state": rec.state.value})
                else:
                    self._send(404, {"message": "路径不存在"})
            except DomainError as exc:
                self._send(409, {"message": str(exc)})
            except (ValueError, TypeError) as exc:
                self._send(400, {"message": str(exc)})

        def log_message(self, fmt, *args):
            pass

    return ApiHandler


def _default(obj):
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "value"):
        return obj.value
    raise TypeError(type(obj))


def serve(service, host="127.0.0.1", port=8088):
    server = ThreadingHTTPServer((host, port), make_handler(service))
    return server
