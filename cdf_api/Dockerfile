# 1. 基础镜像
ARG PYTHON_BASE_IMAGE=public.ecr.aws/docker/library/python:3.10-slim
FROM ${PYTHON_BASE_IMAGE}

# 2. 设置工作目录
WORKDIR /app

# 3. 环境变量设置 (不生成 pyc，且日志不缓冲)
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# 4. 修改容器时区为北京时间 (极其重要！否则你的 FastAPI 日志和定时任务全差 8 小时)
RUN ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && echo 'Asia/Shanghai' > /etc/timezone

# 5. 复制并安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 6. 复制项目所有代码
COPY . .

# 7. 赋予启动脚本可执行权限
RUN chmod +x start.sh

# 8. 暴露 Web 服务的端口
EXPOSE 8000

# 9. 最终启动命令：执行那个包揽了两个任务的脚本
CMD ["./start.sh"]
