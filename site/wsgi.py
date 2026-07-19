"""gunicorn entrypoint:  gunicorn --workers 2 --threads 4 wsgi:app"""
from app import create_app

app = create_app()

if __name__ == "__main__":
    import os
    # Set BBB_DEV_HOST=0.0.0.0 to test from another device on the local network.
    app.run(host=os.getenv("BBB_DEV_HOST", "127.0.0.1"), port=5000,
            debug=True, use_reloader=False)
