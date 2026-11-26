"""留样后端启动入口：python -m retention.server --data fixtures/exchange.json"""
import argparse
import json
from pathlib import Path

from .api import serve
from .model import load_bundle
from .service import RetentionService


def main():
    parser = argparse.ArgumentParser(description="留样保全与处置后端")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--data", type=Path,
                        default=Path("fixtures/exchange.json"))
    args = parser.parse_args()
    data = json.loads(args.data.read_text(encoding="utf-8"))
    service = RetentionService(load_bundle(data))
    server = serve(service, args.host, args.port)
    print(f"http://{args.host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
