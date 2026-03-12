# SPDX-FileCopyrightText: 2026 TOP Team Combat Control
# SPDX-License-Identifier: GPL-3.0-or-later

FROM python:3.13-slim

# libpq5 is required at runtime by psycopg2-binary
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

# /app must be the working directory because:
#   - main.py mounts StaticFiles(directory=".") — frontend assets must be here
#   - init_db() resolves alembic.ini relative to __file__
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5001

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5001", "--reload"]
