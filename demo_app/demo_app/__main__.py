"""Run the demo API with ``python -m demo_app``."""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("demo_app.main:app", host="127.0.0.1", port=8000, reload=True)
