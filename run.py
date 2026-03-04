import sys
import os

# Add the backend folder to the Python path so app.py can be imported
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

from app import app

if __name__ == "__main__":
    app.run(debug=True, port=5000)
