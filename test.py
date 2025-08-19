#!/usr/bin/env python3
"""
Load testing script for the transaction queue system
"""
import asyncio
import aiohttp
import time
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class LoadTester:
    def __init__(self, base_url="http://localhost:3000", mock_url="http://localhost:8080"):
        self.base_url = base_url
        self.mock_url = mock_url
        self.results = {
            "submitted": 0,
            "completed": 0,
            "failed": 0,
            "response_times": [],
            "errors": []
        }

    def generate_transaction(self):
        """Generate a test transaction"""
        return {
            "id": str(uuid.uuid4()),
            "amount": float(Decimal(str(round(100 + (hash(time.time()) % 900), 2)))),
            "currency": "USD",
            "description": f"Test transaction {int(time.time())}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": {"test": True}
        }

    async def submit_transaction(self, session, transaction):
        """Submit a single transaction"""
        start_time = time.time()
        try:
            async with session.post(
                    f"{self.base_url}/api/transactions",
                    json=transaction,
                    timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                response_time = (time.time() - start_time) * 1000
                self.results["response_times"].append(response_time)

                if response.status == 200:
                    self.results["submitted"] += 1
                    result = await response.json()
                    return result["transactionId"]
                else:
                    self.results["errors"].append(f"HTTP {response.status}")
                    logger.error(f"Failed to submit transaction: {response.status}")
                    return None

        except Exception as e:
            response_time = (time.time() - start_time) * 1000
            self.results["response_times"].append(response_time)
            self.results["errors"].append(str(e))
            logger.error(f"Error submitting transaction: {e}")
            return None

    async def check_transaction_status(self, session, transaction_id):
        """Check transaction status"""
        try:
            async with session.get(
                    f"{self.base_url}/api/transactions/{transaction_id}",
                    timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result["status"]
                return None
        except Exception as e:
            logger.error(f"Error checking transaction status: {e}")
            return None

    async def cleanup(self, session):
        """Clean up test data"""
        try:
            async with session.post(f"{self.base_url}/cleanup") as response:
                logger.info("Cleanup completed")
        except Exception as e:
            logger.error(f"Cleanup failed: {e}")

    async def get_health(self, session):
        """Get system health"""
        try:
            async with session.get(f"{self.base_url}/api/health") as response:
                if response.status == 200:
                    return await response.json()
        except Exception as e:
            logger.error(f"Health check failed: {e}")
        return None

    async def run_load_test(self, num_transactions=100, concurrent_requests=10):
        """Run load test with specified parameters"""
        logger.info(f"Starting load test: {num_transactions} transactions, {concurrent_requests} concurrent")

        connector = aiohttp.TCPConnector(limit=concurrent_requests * 2)
        async with aiohttp.ClientSession(connector=connector) as session:
            # Cleanup before test
            await self.cleanup(session)
            await asyncio.sleep(2)

            start_time = time.time()
            transaction_ids = []

            # Create semaphore to limit concurrent requests
            semaphore = asyncio.Semaphore(concurrent_requests)

            async def submit_with_semaphore(transaction):
                async with semaphore:
                    return await self.submit_transaction(session, transaction)

            # Generate and submit transactions
            transactions = [self.generate_transaction() for _ in range(num_transactions)]
            tasks = [submit_with_semaphore(tx) for tx in transactions]

            # Submit all transactions concurrently
            results = await asyncio.gather(*tasks, return_exceptions=True)
            transaction_ids = [r for r in results if isinstance(r, str)]

            submission_time = time.time() - start_time
            logger.info(f"Submitted {len(transaction_ids)} transactions in {submission_time:.2f}s")

            # Wait for processing and monitor status
            logger.info("Monitoring transaction processing...")
            await self.monitor_processing(session, transaction_ids)

            total_time = time.time() - start_time

            # Get final health status
            health = await self.get_health(session)

            # Print results
            self.print_results(total_time, health)

    async def monitor_processing(self, session, transaction_ids, max_wait_time=60):
        """Monitor transaction processing until completion"""
        start_time = time.time()
        completed_ids = set()

        while len(completed_ids) < len(transaction_ids):
            if time.time() - start_time > max_wait_time:
                logger.warning(f"Timeout reached. {len(completed_ids)}/{len(transaction_ids)} completed")
                break

            # Check status of incomplete transactions
            remaining_ids = [tid for tid in transaction_ids if tid not in completed_ids]

            for transaction_id in remaining_ids[:20]:  # Check 20 at a time
                status = await self.check_transaction_status(session, transaction_id)
                if status in ["completed", "failed"]:
                    completed_ids.add(transaction_id)
                    if status == "completed":
                        self.results["completed"] += 1
                    else:
                        self.results["failed"] += 1

            if len(completed_ids) < len(transaction_ids):
                await asyncio.sleep(1)

        logger.info(f"Processing complete: {len(completed_ids)}/{len(transaction_ids)} finished")

    def print_results(self, total_time, health):
        """Print test results"""
        print("\n" + "=" * 50)
        print("LOAD TEST RESULTS")
        print("=" * 50)
        print(f"Total time: {total_time:.2f}s")
        print(f"Transactions submitted: {self.results['submitted']}")
        print(f"Transactions completed: {self.results['completed']}")
        print(f"Transactions failed: {self.results['failed']}")
        print(f"Errors: {len(self.results['errors'])}")

        if self.results["response_times"]:
            avg_response = sum(self.results["response_times"]) / len(self.results["response_times"])
            max_response = max(self.results["response_times"])
            min_response = min(self.results["response_times"])

            print(f"\nResponse Times:")
            print(f"  Average: {avg_response:.2f}ms")
            print(f"  Min: {min_response:.2f}ms")
            print(f"  Max: {max_response:.2f}ms")

            # Check if meets performance requirements
            if avg_response < 100:
                print(f"  ✅ Average response time meets requirement (<100ms)")
            else:
                print(f"  ❌ Average response time exceeds requirement (>100ms)")

        if self.results["submitted"] > 0:
            throughput = self.results["submitted"] / total_time
            print(f"\nThroughput: {throughput:.2f} transactions/second")

            if throughput > 1000:
                print("  ✅ Throughput meets requirement (>1000 TPS)")
            else:
                print("  ❌ Throughput below requirement (<1000 TPS)")

        if health:
            print(f"\nSystem Health:")
            print(f"  Status: {health['status']}")
            print(f"  Queue depth: {health['queue_depth']}")
            print(f"  Error rate: {health['error_rate']:.2%}")
            print(f"  Uptime: {health['uptime']:.2f}s")

        if self.results["errors"]:
            print(f"\nFirst 10 errors:")
            for error in self.results["errors"][:10]:
                print(f"  - {error}")

        print("=" * 50)


async def main():
    """Main function to run different test scenarios"""
    tester = LoadTester()

    # Test scenarios
    scenarios = [
        {"name": "Warm-up Test", "transactions": 10, "concurrent": 5},
        {"name": "Performance Test", "transactions": 100, "concurrent": 20},
        {"name": "Stress Test", "transactions": 1000, "concurrent": 50},
    ]

    for scenario in scenarios:
        print(f"\n🚀 Running {scenario['name']}")
        print(f"Transactions: {scenario['transactions']}, Concurrent: {scenario['concurrent']}")

        tester.results = {
            "submitted": 0,
            "completed": 0,
            "failed": 0,
            "response_times": [],
            "errors": []
        }

        await tester.run_load_test(
            num_transactions=scenario["transactions"],
            concurrent_requests=scenario["concurrent"]
        )

        # Wait between tests
        if scenario != scenarios[-1]:
            print("Waiting 10 seconds before next test...")
            await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())