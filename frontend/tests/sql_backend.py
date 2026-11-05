"""SQL browser test backend: runs real local CLI without original model services."""
from contextlib import asynccontextmanager
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fastapi import FastAPI
from apis.sql_evaluation_api import router, get_sql_manager
from tools.sql_evaluation.jobs import SQLJobManager

temporary = tempfile.TemporaryDirectory(prefix='sql-evaluation-browser-')
manager = SQLJobManager(Path(temporary.name) / 'jobs', python=sys.executable)


@asynccontextmanager
async def lifespan(app):
    yield
    manager.close()
    temporary.cleanup()


app = FastAPI(lifespan=lifespan)
app.include_router(router)
app.dependency_overrides[get_sql_manager] = lambda: manager


@app.get('/health')
def health():
    return {'status': 'ok'}


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=51011, log_level='warning')
