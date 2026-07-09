#!/bin/bash

# Start the background CSV fetch sync task.
python sync/fetch_data_0513.py &

# Start the FastAPI service in the foreground.
uvicorn main:app --host 0.0.0.0 --port 8000
