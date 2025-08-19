import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Any
from enum import Enum

import aioredis
import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator
import uvicorn

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DecimalEncoder(json.JSONEncoder):
    """Custom JSON encoder to handle Decimal objects"""

    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


class TransactionStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class TransactionRequest(BaseModel):
    id: str = Field(..., description="Transaction UUID")
    amount: Decimal = Field(..., gt=0, description="Transaction amount")
    currency: str = Field(..., min_length=3, max_length=3, description="ISO 4217 currency code")
    description: str = Field(..., description="Transaction description")
    timestamp: str = Field(..., description="ISO 8601 datetime")
    metadata: Optional[Dict[str, Any]] = None

    @validator('currency')
    def validate_currency(cls, v):
        return v.upper()

    class Config:
        json_encoders = {
            Decimal: float  # Convert Decimal to float for JSON serialization
        }


class TransactionResponse(BaseModel):
    transactionId: str
    status: TransactionStatus
    submittedAt: str
    completedAt: Optional[str] = None
    error: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    queue_depth: int
    error_rate: float
    uptime: float
    processed_count: int
    failed_count: int


class TransactionStore:
    """In-memory store for transaction metadata with Redis backing"""

    def __init__(self, redis: aioredis.Redis):
        self.redis = redis
        self.start_time = time.time()

    async def store_transaction(self, transaction_id: str, status: TransactionStatus,
                                submitted_at: str, error: Optional[str] = None,
                                completed_at: Optional[str] = None):
        """Store transaction metadata in Redis"""
        data = {
            "transactionId": transaction_id,
            "status": status.value,
            "submittedAt": submitted_at,
            "error": error or "",
            "completedAt": completed_at or ""
        }
        await self.redis.hset(f"transaction:{transaction_id}", mapping=data)

    async def get_transaction(self, transaction_id: str) -> Optional[TransactionResponse]:
        """Get transaction from Redis"""
        data = await self.redis.hgetall(f"transaction:{transaction_id}")
        if not data:
            return None

        # Handle bytes decoding and None values safely
        def decode_field(field_data, default=""):
            if not field_data:
                return default
            if isinstance(field_data, bytes):
                return field_data.decode()
            return str(field_data)

        return TransactionResponse(
            transactionId=decode_field(data.get(b'transactionId') or data.get('transactionId')),
            status=TransactionStatus(decode_field(data.get(b'status') or data.get('status'))),
            submittedAt=decode_field(data.get(b'submittedAt') or data.get('submittedAt')),
            completedAt=decode_field(data.get(b'completedAt') or data.get('completedAt')) or None,
            error=decode_field(data.get(b'error') or data.get('error')) or None
        )

    async def update_status(self, transaction_id: str, status: TransactionStatus,
                            completed_at: Optional[str] = None, error: Optional[str] = None):
        """Update transaction status"""
        updates = {"status": status.value}
        if completed_at:
            updates["completedAt"] = completed_at
        if error:
            updates["error"] = error
        # Only update fields that have values
        await self.redis.hset(f"transaction:{transaction_id}", mapping=updates)

    async def get_stats(self) -> Dict[str, Any]:
        """Get system statistics"""
        keys = await self.redis.keys("transaction:*")
        total_transactions = len(keys)

        failed_count = 0
        completed_count = 0

        for key in keys:
            try:
                status = await self.redis.hget(key, "status")
                if status:
                    status_str = status.decode() if isinstance(status, bytes) else str(status)
                    if status_str == TransactionStatus.FAILED.value:
                        failed_count += 1
                    elif status_str == TransactionStatus.COMPLETED.value:
                        completed_count += 1
            except Exception as e:
                logger.warning(f"Error reading status for key {key}: {e}")
                continue

        return {
            "total_transactions": total_transactions,
            "failed_count": failed_count,
            "completed_count": completed_count,
            "uptime": time.time() - self.start_time
        }


class TransactionQueue:
    """Redis-based transaction queue with retry logic"""

    def __init__(self, redis: aioredis.Redis, mock_service_url: str = "http://localhost:8080"):
        self.redis = redis
        self.mock_service_url = mock_service_url
        self.queue_name = "transaction_queue"
        self.retry_queue = "transaction_retry_queue"
        self.processing_set = "processing_transactions"
        self.max_retries = 3
        self.retry_delay = 5  # seconds

    async def enqueue(self, transaction: TransactionRequest) -> str:
        """Add transaction to queue"""
        # Convert transaction to dict and handle Decimal serialization
        transaction_dict = transaction.dict()

        transaction_data = {
            "id": transaction.id,
            "data": transaction_dict,
            "retry_count": 0,
            "enqueued_at": datetime.now(timezone.utc).isoformat()
        }

        # Use custom encoder to handle Decimal objects
        await self.redis.lpush(self.queue_name, json.dumps(transaction_data, cls=DecimalEncoder))
        logger.info(f"Enqueued transaction {transaction.id}")
        return transaction.id

    async def dequeue(self) -> Optional[Dict[str, Any]]:
        """Get next transaction from queue"""
        # Try regular queue first
        item = await self.redis.brpop(self.queue_name, timeout=1)
        if item:
            return json.loads(item[1])

        # Try retry queue
        item = await self.redis.brpop(self.retry_queue, timeout=1)
        if item:
            return json.loads(item[1])

        return None

    async def schedule_retry(self, transaction_data: Dict[str, Any]):
        """Schedule transaction for retry"""
        transaction_data["retry_count"] += 1
        transaction_data["retry_at"] = (
                datetime.now(timezone.utc).timestamp() + self.retry_delay
        )

        if transaction_data["retry_count"] <= self.max_retries:
            await self.redis.lpush(self.retry_queue, json.dumps(transaction_data, cls=DecimalEncoder))
            logger.info(f"Scheduled retry {transaction_data['retry_count']} for transaction {transaction_data['id']}")
        else:
            logger.error(f"Max retries exceeded for transaction {transaction_data['id']}")

    async def get_queue_depth(self) -> int:
        """Get current queue depth"""
        main_queue = await self.redis.llen(self.queue_name)
        retry_queue = await self.redis.llen(self.retry_queue)
        return main_queue + retry_queue


class TransactionProcessor:
    """Processes transactions with deduplication and error handling"""

    def __init__(self, store: TransactionStore, queue: TransactionQueue,
                 mock_service_url: str = "http://localhost:8080"):
        self.store = store
        self.queue = queue
        self.mock_service_url = mock_service_url
        self.client = httpx.AsyncClient(timeout=30.0)

    async def process_transaction(self, transaction_data: Dict[str, Any]) -> bool:
        """Process a single transaction with deduplication"""
        transaction_id = transaction_data["id"]
        transaction = TransactionRequest(**transaction_data["data"])

        try:
            # Update status to processing
            await self.store.update_status(transaction_id, TransactionStatus.PROCESSING)
            logger.info(f"Processing transaction {transaction_id}")

            # Step 1: Check if transaction already exists (deduplication)
            if await self._check_transaction_exists(transaction_id):
                await self.store.update_status(
                    transaction_id,
                    TransactionStatus.COMPLETED,
                    completed_at=datetime.now(timezone.utc).isoformat()
                )
                logger.info(f"Transaction {transaction_id} already exists, marked as completed")
                return True

            # Step 2: Attempt to POST transaction
            post_success = await self._post_transaction(transaction)

            if post_success:
                # Success case
                await self.store.update_status(
                    transaction_id,
                    TransactionStatus.COMPLETED,
                    completed_at=datetime.now(timezone.utc).isoformat()
                )
                logger.info(f"Successfully processed transaction {transaction_id}")
                return True
            else:
                # Post failed - check if it was a post-write failure
                if await self._check_transaction_exists(transaction_id):
                    # Post-write failure - transaction was saved despite error
                    await self.store.update_status(
                        transaction_id,
                        TransactionStatus.COMPLETED,
                        completed_at=datetime.now(timezone.utc).isoformat()
                    )
                    logger.info(f"Post-write failure detected for {transaction_id}, marked as completed")
                    return True
                else:
                    # Pre-write failure - schedule retry
                    logger.warning(f"Pre-write failure for transaction {transaction_id}, scheduling retry")
                    return False

        except Exception as e:
            logger.error(f"Error processing transaction {transaction_id}: {str(e)}")
            return False

    async def _check_transaction_exists(self, transaction_id: str) -> bool:
        """Check if transaction exists in mock service"""
        try:
            response = await self.client.get(f"{self.mock_service_url}/transactions/{transaction_id}")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Error checking transaction existence: {str(e)}")
            return False

    async def _post_transaction(self, transaction: TransactionRequest) -> bool:
        """Post transaction to mock service"""
        try:
            # Convert transaction to dict with proper serialization
            transaction_data = transaction.dict()
            # Convert Decimal to float for JSON serialization
            if isinstance(transaction_data.get('amount'), Decimal):
                transaction_data['amount'] = float(transaction_data['amount'])

            response = await self.client.post(
                f"{self.mock_service_url}/transactions",
                json=transaction_data,
                headers={"Content-Type": "application/json"}
            )
            return response.status_code in [200, 201]
        except Exception as e:
            logger.error(f"Error posting transaction: {str(e)}")
            return False


# Global instances
app = FastAPI(title="High-Performance Transaction Queue", version="1.0.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global variables (will be initialized on startup)
redis: Optional[aioredis.Redis] = None
store: Optional[TransactionStore] = None
queue: Optional[TransactionQueue] = None
processor: Optional[TransactionProcessor] = None


@app.on_event("startup")
async def startup_event():
    global redis, store, queue, processor

    # Initialize Redis connection with proper configuration
    redis = aioredis.from_url(
        "redis://localhost:6379",
        decode_responses=False,  # Keep as bytes for consistency
        socket_timeout=5,
        socket_connect_timeout=5,
        retry_on_timeout=True
    )

    # Test Redis connection
    try:
        await redis.ping()
        logger.info("Redis connection successful")
    except Exception as e:
        logger.error(f"Redis connection failed: {e}")
        raise

    # Initialize components
    store = TransactionStore(redis)
    queue = TransactionQueue(redis)
    processor = TransactionProcessor(store, queue)

    # Start background workers
    asyncio.create_task(transaction_worker())
    asyncio.create_task(retry_scheduler())

    logger.info("Transaction queue system started")


@app.on_event("shutdown")
async def shutdown_event():
    if redis:
        await redis.close()


async def transaction_worker():
    """Background worker to process transactions"""
    while True:
        try:
            transaction_data = await queue.dequeue()
            if transaction_data:
                success = await processor.process_transaction(transaction_data)
                if not success:
                    await queue.schedule_retry(transaction_data)
            else:
                await asyncio.sleep(0.1)  # Short sleep when queue is empty
        except Exception as e:
            logger.error(f"Worker error: {str(e)}")
            await asyncio.sleep(1)


async def retry_scheduler():
    """Background task to handle retry scheduling"""
    while True:
        try:
            # This is a simplified retry scheduler
            # In production, you might want to use Redis sorted sets for better timing
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Retry scheduler error: {str(e)}")


@app.post("/api/transactions", response_model=TransactionResponse)
async def submit_transaction(transaction: TransactionRequest):
    """Submit a transaction for processing"""
    try:
        # Validate transaction ID is proper UUID format
        try:
            uuid.UUID(transaction.id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid transaction ID format. Must be a valid UUID.")

        # Store initial transaction state
        submitted_at = datetime.now(timezone.utc).isoformat()
        await store.store_transaction(
            transaction.id,
            TransactionStatus.PENDING,
            submitted_at
        )

        # Enqueue for processing
        await queue.enqueue(transaction)

        logger.info(f"Transaction {transaction.id} submitted successfully")

        return TransactionResponse(
            transactionId=transaction.id,
            status=TransactionStatus.PENDING,
            submittedAt=submitted_at
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error submitting transaction: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.get("/api/transactions/{transaction_id}", response_model=TransactionResponse)
async def get_transaction(transaction_id: str):
    """Get transaction status and details"""
    transaction = await store.get_transaction(transaction_id)
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return transaction


@app.get("/api/health", response_model=HealthResponse)
async def get_health():
    """Get system health and metrics"""
    try:
        stats = await store.get_stats()
        queue_depth = await queue.get_queue_depth()

        error_rate = 0.0
        if stats["total_transactions"] > 0:
            error_rate = stats["failed_count"] / stats["total_transactions"]

        return HealthResponse(
            status="healthy",
            queue_depth=queue_depth,
            error_rate=error_rate,
            uptime=stats["uptime"],
            processed_count=stats["completed_count"],
            failed_count=stats["failed_count"]
        )
    except Exception as e:
        logger.error(f"Health check error: {str(e)}")
        raise HTTPException(status_code=500, detail="Health check failed")


@app.post("/cleanup")
async def cleanup_transactions():
    """Clear all transactions (testing only)"""
    try:
        # Clear Redis data
        keys = await redis.keys("transaction:*")
        deleted_count = len(keys)

        if keys:
            await redis.delete(*keys)

        # Clear queues
        await redis.delete(queue.queue_name)
        await redis.delete(queue.retry_queue)
        await redis.delete(queue.processing_set)

        # Also cleanup mock service
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post("http://localhost:8080/cleanup")
                logger.info("Mock service cleanup completed")
        except Exception as e:
            logger.warning(f"Mock service cleanup failed (this is OK if mock service is not running): {e}")

        logger.info(f"Cleanup completed: {deleted_count} transactions removed")
        return {"message": "All transactions cleared", "count": deleted_count}

    except Exception as e:
        logger.error(f"Cleanup error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Cleanup failed: {str(e)}")


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=3000,
        reload=True,
        log_level="info"
    )