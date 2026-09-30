from fastapi import FastAPI

app = FastAPI(title="Signal Robot Engine")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
