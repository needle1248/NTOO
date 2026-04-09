import uvicorn

from app.config import get_settings
from app.main import create_app


app = create_app()
settings = get_settings()


if __name__ == "__main__":
    uvicorn.run(
        "run:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
