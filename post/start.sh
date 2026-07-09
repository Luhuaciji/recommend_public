#!/bin/bash

# 1. 启动后台定时同步任务（注意末尾的 & 符号，它表示在后台静默运行）
python sync/sync_db_main.py &

# 2. 启动 FastAPI 主服务（这个必须在最后，且不加 &，作为前台主进程保持容器不退出
uvicorn main:app --host 0.0.0.0 --port 8000