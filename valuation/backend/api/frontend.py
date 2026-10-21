"""前端HTML每次重新取得，内容寻址资源长期缓存。"""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException


class FrontendAssets(StaticFiles):
    async def get_response(self, path, scope):
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as error:
            if error.status_code != 404:
                raise
            return JSONResponse({'detail': 'Not Found'}, status_code=404, headers={'Cache-Control': 'no-store'})
        response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        return response


def mount_frontend(app: FastAPI, directory: Path):
    if not directory.is_dir():
        return
    app.mount('/assets', FrontendAssets(directory=str(directory / 'assets')), name='assets')

    @app.get('/{full_path:path}', include_in_schema=False)
    async def serve_spa(full_path: str):
        if full_path == 'api' or full_path.startswith('api/'):
            raise HTTPException(status_code=404)
        return FileResponse(directory / 'index.html', headers={'Cache-Control': 'no-store'})
