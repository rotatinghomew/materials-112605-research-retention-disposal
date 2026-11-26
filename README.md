# 材料留样的跨站保全与销毁交接

档案室与保管站按母样、划分件及处置回执核对实体留存资料。

《留样交接与处置》说明在 [交换约定](docs/exchange.md)，后端规则与接口见 [后端说明](docs/backend.md)。`fixtures/exchange.json` 是本机联调使用的合成记录。项目当前可读取这些交换文件，回放对接方回执，并验证资料之间的引用。

`retention/` 是业务后端：报告登记与更正、样品拆分与跨站交接、法务保全与释放、处置申请校验、保全追回与迟到销毁冲减。只依赖标准库，通过 `retention.exchange` 与对接方回放服务同步回执。启动方式：`python3 -m retention.server --data fixtures/exchange.json`。

运行环境为 Python 3.11.2，只依赖标准库。执行 `python3 -m unittest discover -s tests -v` 检查交换资料与后端规则。执行 `python3 partner.py` 后，可通过回环地址的 8087 端口查询回执，`GET /health` 返回服务状态。如需模拟查询暂时失败，启动时传入 `--unavailable`。
