"""对接方回执查询客户端（标准库 urllib，配合 partner.py 回放服务）。"""
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ReceiptUnavailable(RuntimeError):
    """对接方返回 503 或网络不可达。"""


def fetch_receipts(base_url, at, timeout=5.0):
    """GET {base}/v1/receipts?at=<带时区 ISO 时间>。

    返回回执原始记录列表；503 抛 ReceiptUnavailable，由调用方决定重试，
    不能把查询失败当作“没有保全/没有销毁”。
    """
    query = urlencode({"at": at.isoformat()})
    url = f"{base_url.rstrip('/')}/v1/receipts?{query}"
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 503:
            raise ReceiptUnavailable("对接方暂时无法查阅回执") from exc
        raise
    except URLError as exc:
        raise ReceiptUnavailable(f"无法连接对接方: {exc.reason}") from exc
    return payload.get("receipts", [])


def post_dispatch(base_url, body, timeout=5.0):
    """POST /v1/dispatches：只代表送达对手方，不生成履约回执。"""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"{base_url.rstrip('/')}/v1/dispatches", data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST")
    try:
        with urlopen(request, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except URLError as exc:
        raise ReceiptUnavailable(f"无法连接对接方: {exc.reason}") from exc
