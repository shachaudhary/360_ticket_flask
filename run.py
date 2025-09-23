# run.py
from app import create_app
import os

app = create_app()

if __name__ == "__main__":
    # no extra positional arg!
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5002)),
        debug=True
    )
