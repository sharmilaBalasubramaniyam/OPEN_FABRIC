
## High Performance Transaction Processing System (FastAPI + Redis + Mock Service)

End-to-end, ready-to-run solution for the take-home exercise.  

Provides **sub-100ms API responses**, **zero data loss**, and **no duplicate transactions**, even when the downstream posting service is **slow** and **unreliable**.  


##  Overview
This service acts as a reliable intermediary between clients and a **slow, unreliable mock posting service**.  
It ensures **high performance, fault tolerance, idempotency, and deduplication** under heavy transaction loads.



##  Features
- **<100ms response** on transaction submission  
- **Reliable queueing** with Redis  
- **Zero data loss & deduplication** using GET verification  
- **Retry logic** for failures (pre-write & post-write)  
- **Health monitoring** (queue depth, error rate, uptime)  
- **Load testing** with automated script (`test.py`)  


## System Architecture
1. **API Layer (FastAPI)** – accepts client requests, responds immediately  
2. **Queue (Redis)** – stores transactions for async processing  
3. **Worker Pool** – processes queued transactions, retries failures  
4. **Mock Posting Service** – slow, unreliable downstream (provided via Docker)  
5. **State Store** – keeps transaction status (`pending`, `processing`, `completed`, `failed`)  


## Prerequisites
- Python **3.9+**  
- pip (Python package manager)  
- Docker (for Redis + Mock Posting Service)  
- Git (to clone repository)  

## Setup & Installation
```bash
# Setup environment
python -m venv venv
# Windows: venv\Scripts\activate
# macOS/Linux: source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start Redis
docker run -d --name redis -p 6379:6379 redis

# Start mock posting service
docker run -p 8080:8080 vinhopenfabric/mock-posting-service:latest

# Run FastAPI server
uvicorn main:app --host 0.0.0.0 --port 3000 --reload
# API is now available at http://localhost:3000
````

## API Endpoints

### `POST /api/transactions`

Submit a new transaction. Returns immediately (<100ms).

### `GET /api/transactions/{id}`

Check transaction status (`pending`, `processing`, `completed`, `failed`).

### `GET /api/health`

Returns system health, queue depth, error rate, uptime.


##  How the System Works

1. Transaction submitted → immediately enqueued (status `pending`)
2. Worker picks it up → checks posting service for existence
3. If not exists → `POST` to mock service
4. If failure → retries based on type (pre-write/post-write)
5. Status updated (`completed` or `failed`)



## Load Testing

Run:

```bash
python test.py
```

This script:

* Sends transactions in batches (warm-up, performance, stress)
* Measures latency, TPS, error rate
* Verifies no duplicates

## Monitoring

```bash
curl http://localhost:3000/api/health
```

Example:

```json
{
  "status": "OK",
  "queue_depth": 3,
  "error_rate": 0.01,
  "uptime": 250
}
```


## Folder Structure

```
OPEN_FABRIC
│
├── main.py              # FastAPI app + worker logic
├── test.py              # Load testing script
├── index.html           # Simple test client
├── requirements.txt     # Python dependencies
├── README.md            # Documentation
├── venv/                # Virtual environment
└── .gitignore
```


## Future Improvements

* Prometheus + Grafana for real-time monitoring
* Circuit breaker for downstream outages
* Rate limiting for overload protection
* PostgreSQL storage for persistence
* Horizontal scaling of workers



