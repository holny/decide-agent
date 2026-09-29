"""JSONC：带注释的 JSON（配置文件用）——剥离 // 与 /* */ 注释后按标准 JSON 解析。

字符串字面量内的斜杠（如 "https://example.com"）不受影响。
"""
import json
import re
from pathlib import Path
from typing import Any


def loads(text: str) -> Any:
    out: list[str] = []
    index = 0
    length = len(text)
    in_string = False
    while index < length:
        char = text[index]
        if in_string:
            out.append(char)
            if char == "\\" and index + 1 < length:
                out.append(text[index + 1])
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue
        if char == "/" and index + 1 < length and text[index + 1] == "/":
            while index < length and text[index] != "\n":
                index += 1
            continue
        if char == "/" and index + 1 < length and text[index + 1] == "*":
            index += 2
            while index + 1 < length and not (text[index] == "*" and text[index + 1] == "/"):
                index += 1
            index += 2
            continue
        out.append(char)
        index += 1
    cleaned = "".join(out)
    # 尾逗号清理（JSONC 允许，标准 JSON 不允许）
    cleaned = re.sub(r",\s*([\]}])", r"\1", cleaned)
    return json.loads(cleaned)


def load(path: str | Path) -> Any:
    return loads(Path(path).read_text("utf-8"))


def dumps(data: Any, indent: int = 2) -> str:
    return json.dumps(data, ensure_ascii=False, indent=indent) + "\n"
