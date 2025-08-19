## High Performance Transaction Processing System (FastAPI + Redis + Mock Service)

End-to-end, ready-to-run solution for the take-home exercise.
Provides <100 ms API responses, zero data loss, and no duplicate transactions, even when the downstream posting service is slow and unreliable.

## Overview

This service acts as a reliable intermediary between clients and a slow, unreliable mock posting service.
It ensures high performance, fault tolerance, idempotency, and deduplication under heavy transaction loads.

## Features

1. <100 ms response on transaction submission

2. Reliable queueing with Redis

3. Zero data loss & deduplication using GET verification

4. Retry logic for failures (pre-write & post-write)

5. Health monitoring (queue depth, error rate, uptime)

6. Load testing with automated script (test.py)

## System Architecture

API Layer (FastAPI) – accepts client requests, responds immediately

Queue (Redis) – stores transactions for async processing

Worker Pool – processes queued transactions, retries failures

Mock Posting Service – slow, unreliable downstream (provided via Docker)

State Store – keeps transaction status (pending, processing, completed, failed)

## Prerequisites

1. Python 3.9+

pip (Python package manager)

2. Docker (for Redis + Mock Posting Service)

3. Git (to clone repository)

# Setup & Installation

# Setup environment
python -m venv venv

 Windows: venv\Scripts\activate
 macOS/Linux: source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start Redis
docker run -d --name redis -p 6379:6379 redis

# Start mock posting service
docker run -d --name mock-posting -p 8080:8080 vinhopenfabric/mock-posting-service:latest

# Run FastAPI server
uvicorn main:app --host 0.0.0.0 --port 3000 --reload
API is now available at http://localhost:3000

## API Endpoints

1. POST /api/transactions

  Submit a new transaction. Returns immediately (≈<100 ms).

2. GET /api/transactions/{id}

Check transaction status (pending, processing, completed, failed).

3. GET /api/health

Returns system health, queue depth, error rate, uptime.

# How the System Works

Transaction submitted → immediately enqueued (status pending)

Worker picks it up → checks posting service for existence

If not exists → POST to mock service

If failure → retries based on type (pre-write/post-write)

Status updated (completed or failed)

## Load Testing
python test.py

## Example:

{
  "status": "OK",
  "queue_depth": 3,
  "error_rate": 0.01,
  "uptime": 250
}

## Folder Structure

OPEN_FABRIC
│
├── main.py  # FastAPI app + worker logic

├── test.py              # Load testing script

├── index.html           # Simple test client

├── requirements.txt     # Python dependencies

├── README.md            # Documentation

└── .gitignore

## Future Improvements

Prometheus + Grafana for real-time monitoring

Circuit breaker for downstream outages

Rate limiting for overload protection

PostgreSQL storage for persistence

Horizontal scaling of workers
