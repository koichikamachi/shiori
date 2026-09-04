# しおり — Cloud Run 用
#
# buildpack にまかせず Dockerfile を置いているのは、
# 会場や10月に「なぜ動かないか」を追える形にしておくためである。
# 何が入って、何で起動するかが、この一枚に全部書いてある。

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt

# 判定に要るものだけを入れる。テストや資料は入れない。
COPY src/ ./src/
COPY tools/ ./tools/
COPY data/ ./data/
COPY server.py .

# Cloud Run は $PORT で待つことを求める。既定は 8080。
ENV PORT=8080
EXPOSE 8080
CMD exec uvicorn server:app --host 0.0.0.0 --port ${PORT}
