High Performance Transaction Processing System (FastAPI + Redis + Mock Service)

An end-to-end, ready-to-run solution for the take-home exercise.
Provides <100 ms API responses, zero data loss, and no duplicate transactions even when the downstream posting service is slow and unreliable.

Overview

This system acts as a reliable intermediary between clients and a slow, unreliable mock posting service.
It ensures high performance, fault tolerance, idempotency, and deduplication under heavy transaction loads.

Features

<100 ms response on transaction submission

Reliable queueing with Redis

Zero data loss and deduplication using GET verification

Retry logic for failures (pre-write and post-write)

Health monitoring (queue depth, error rate, uptime)

Load testing with automated script (test.py)

System Architecture

API Layer (FastAPI) – accepts client requests, responds immediately

Queue (Redis) – stores transactions for asynchronous processing

Worker Pool – processes queued transactions, retries failures

Mock Posting Service – slow, unreliable downstream (Docker-provided)

State Store – maintains transaction status (pending, processing, completed, failed)

Prerequisites

Python 3.9+ (with pip)

Docker (for Redis and Mock Posting Service)

Git (to clone repository)

Setup & Installation
# Clone repository
git clone <repo-url>
cd OPEN_FABRIC

# Create and activate virtual environment
python -m venv venv
# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

Start Services
# Start Redis
docker run -d --name redis -p 6379:6379 redis

# Start Mock Posting Service
docker run -d --name mock-posting -p 8080:8080 vinhopenfabric/mock-posting-service:latest

Run API Server
uvicorn main:app --host 0.0.0.0 --port 3000 --reload


API available at: http://localhost:3000

API Endpoints
1. Submit Transaction

POST /api/transactions
Returns immediately (<100 ms).

2. Get Transaction Status

GET /api/transactions/{id}
Statuses: pending, processing, completed, failed.

3. Health Check

GET /api/health
Returns system health info.
Example response:

{
  "status": "OK",
  "queue_depth": 3,
  "error_rate": 0.01,
  "uptime": 250
}

How It Works

Client submits transaction → enqueued immediately (status: pending)

Worker picks transaction → checks posting service for existence

If not exists → POST to mock service

On failure → retry with pre/post-write logic

Status updated → completed or failed

Load Testing
python test.py

Folder Structure
OPEN_FABRIC
│── main.py           # FastAPI app + worker logic
│── test.py           # Load testing script
│── index.html        # Simple test client
│── requirements.txt  # Python dependencies
│── README.md         # Documentation
└── .gitignore
