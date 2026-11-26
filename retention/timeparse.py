"""时间解析：业务时间与到达时间都必须带时区偏移。"""
from datetime import datetime


def parse_time(text):
    """解析 ISO-8601 文本，拒绝无时区的朴素时间。"""
    if not isinstance(text, str):
        raise ValueError("时间须为 ISO-8601 字符串")
    instant = datetime.fromisoformat(text)
    if instant.utcoffset() is None:
        raise ValueError("时间须包含时区偏移")
    return instant
