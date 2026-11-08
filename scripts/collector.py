"""资料采集客户端：登录后将指定公开资料通过现有导入协议提交。"""

import argparse
import getpass
import io
import json
from html.parser import HTMLParser
from urllib.parse import urlsplit
from uuid import uuid4
import httpx


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.parts.append(data.strip())


parser = argparse.ArgumentParser(description="将指定资料导入标注项目")
parser.add_argument("--server", default="http://127.0.0.1:8000")
parser.add_argument("--project", required=True)
parser.add_argument("--username", required=True)
parser.add_argument("url")
parser.add_argument("--kind", choices=["text", "image"], default="text")
args = parser.parse_args()
if urlsplit(args.url).scheme not in ("http", "https"):
    raise SystemExit("来源只支持 HTTP/HTTPS")
password = getpass.getpass("项目账号密码：")
with httpx.Client(timeout=30, follow_redirects=False) as client:
    login = client.post(
        args.server + "/api/v1/auth/login",
        json={"username": args.username, "password": password},
    )
    login.raise_for_status()
    headers = {
        "Authorization": "Bearer " + login.json()["access_token"],
        "Idempotency-Key": str(uuid4()),
    }
    source = client.get(args.url)
    source.raise_for_status()
    if len(source.content) > 20 * 1024 * 1024:
        raise SystemExit("资料超过 20 MB 限制")
    if args.kind == "text":
        extractor = TextExtractor()
        extractor.feed(source.text)
        payload = json.dumps(
            {"text": "\n".join(extractor.parts), "source_url": args.url},
            ensure_ascii=False,
        ).encode()
        filename = "collected.jsonl"
    else:
        payload = source.content
        filename = "collected.png"
    response = client.post(
        args.server + f"/api/v1/projects/{args.project}/imports",
        headers=headers,
        files={"file": (filename, payload)},
        data={"source_url": args.url},
    )
    response.raise_for_status()
    print(
        json.dumps(
            {"job_id": response.json()["id"], "source_url": args.url},
            ensure_ascii=False,
        )
    )
