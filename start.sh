#!/bin/bash

cd /app/cdf_api
python sync/fetch_data_0513.py &

cd /app/post
python -m uvicorn main:app --host 0.0.0.0 --port 8001 &

cd /app/cdf_api
python -m uvicorn main:app --host 0.0.0.0 --port 8000
