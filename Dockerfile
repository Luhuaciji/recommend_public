ARG PYTHON_BASE_IMAGE=public.ecr.aws/docker/library/python:3.10-slim
FROM ${PYTHON_BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && echo "Asia/Shanghai" > /etc/timezone

WORKDIR /app

COPY cdf_api/requirements.txt /tmp/cdf_api-requirements.txt
COPY post/requirements.txt /tmp/post-requirements.txt

RUN pip install --no-cache-dir -r /tmp/cdf_api-requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple \
    && pip install --no-cache-dir -r /tmp/post-requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY cdf_api /app/cdf_api
COPY post /app/post
COPY start.sh /app/start.sh

RUN chmod +x /app/start.sh

EXPOSE 8000 8001

CMD ["./start.sh"]
