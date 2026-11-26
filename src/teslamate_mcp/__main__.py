import uvicorn

from .config import Settings
from .server import create_app


def main():
    uvicorn.run(create_app(Settings.from_env()), host="0.0.0.0", port=8000,
                access_log=False, proxy_headers=False)


if __name__ == "__main__":
    main()
