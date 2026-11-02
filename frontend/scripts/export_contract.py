"""从真实评测路由导出浏览器所需契约；不导入整应用或连接外部服务。"""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from apis.evaluation_api import router


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    app = FastAPI()
    app.include_router(router)
    spec = app.openapi()
    prefix = "/api/v1/evaluation/"
    operations = {}
    for path, methods in spec["paths"].items():
        operation = methods["post"]
        content = operation["requestBody"]["content"]["application/json"]
        example = content["examples"]["default"]
        operations[path.removeprefix(prefix)] = {
            "path": path, "title": operation["summary"],
            "schema": content["schema"], "example": example["value"],
            "exampleDescription": example["description"],
        }
    output = json.dumps({"operations": operations, "schemas": spec["components"]["schemas"]}, ensure_ascii=False, indent=2) + "\n"
    target = ROOT / "frontend" / "src" / "data" / "contract.json"
    if args.check:
        if not target.exists() or target.read_text(encoding="utf-8") != output:
            raise SystemExit("前端契约与后端不一致，请运行 npm run contract")
        print("Contract matches all 12 backend evaluation routes.")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(output, encoding="utf-8")
        print(f"Exported {len(operations)} operations.")


if __name__ == "__main__":
    main()
