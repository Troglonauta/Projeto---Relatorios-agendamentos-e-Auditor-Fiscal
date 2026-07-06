"""Entry point conveniente para rodar a app: `python run.py`.

Em produção, prefira:
    uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers 2
"""
import uvicorn
from backend.config import get_settings

if __name__ == "__main__":
    s = get_settings()
    uvicorn.run(
        "backend.main:app",
        host=s.APP_HOST,
        port=s.APP_PORT,
        reload=(s.APP_ENV == "development"),
    )
