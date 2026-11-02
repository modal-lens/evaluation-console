"""浏览器联调专用：加载真实评测路由及错误协议，不依赖旧业务数据库。"""

from pathlib import Path
import os
import sys
import tempfile
import json
from copy import deepcopy
from contextlib import asynccontextmanager

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from apis.evaluation_api import router
from apis.evaluation_runs_api import router as runs_router, get_manager
from tools.text_evaluation.jobs import JobManager
from tools.text_evaluation.fallback import run_evaluation_with_fallback, PACKAGES
from tools.text_evaluation.templates import template
from frontend.tests.fixture_predictor import FixturePredictor
from utils.exceptions import AppError

temporary = tempfile.TemporaryDirectory(prefix='zncl-evaluation-browser-')
prediction_root = Path(temporary.name) / 'prediction-packages'
# A synthetic cache fixture: the text explicitly names 李四, while FixturePredictor
# intentionally misses that person. This exercises real low-score fallback end to end.
fallback_template = template('entity-relation-extraction')
fallback_template['texts'][0]['text'] += '李四参加会议。'
fallback_template['annotations']['documents'][0]['entities'].append({'entity_id': 'extra-person', 'name': '李四', 'type': '人物'})
fallback_directory = prediction_root / PACKAGES['entity-relation-extraction']
(fallback_directory / 'texts').mkdir(parents=True)
for filename, value in (
    ('gold.json', fallback_template['annotations']),
    ('options.json', fallback_template['options']),
    ('predictions.json', {'metric': 'entity-relation-extraction', 'prediction_input': deepcopy(fallback_template['annotations'])}),
):
    (fallback_directory / filename).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
for document in fallback_template['texts']:
    (fallback_directory / 'texts' / document['filename']).write_text(document['text'], encoding='utf-8')
manager = JobManager(Path(temporary.name) / 'jobs.sqlite', runner=lambda request, **kwargs: run_evaluation_with_fallback(request, predictor=FixturePredictor(), cache_root=prediction_root, **kwargs))

@asynccontextmanager
async def lifespan(app):
    yield
    manager.close()
    temporary.cleanup()

app = FastAPI(title="Evaluation integration test backend", lifespan=lifespan)
app.include_router(router)
app.include_router(runs_router)
app.dependency_overrides[get_manager] = lambda: manager


@app.get("/health")
def health():
    return {"status": "ok"}


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(status_code=exc.status_code, content={"success": False, "data": None, "error": exc.to_error()})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("TEST_BACKEND_PORT", "51011")), log_level="warning")
