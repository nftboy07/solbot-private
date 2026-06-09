import asyncio
from curl_cffi.requests import AsyncSession
import logging

# Configure logging to see output clearly
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_direct")

async def test_direct_connection():
    """
    Tests a direct connection to the Pump.fun API using curl_cffi.
    If this works, the VPS IP is not hard-blocked, only flagged for TLS fingerprinting.
    """
    test_url = "https://frontend-api.pump.fun/coins/latest"
    
    logger.info(f"Attempting direct connection to {test_url}...")
    
    try:
        # Impersonate Chrome 120 to provide a valid JA3/TLS fingerprint
        async with AsyncSession(impersonate="chrome120") as session:
            response = await session.get(test_url, timeout=10)
            
            if response.status_code == 200:
                logger.info("SUCCESS: Direct connection established with 200 OK.")
                logger.info("Cloudflare bypass successful using curl_cffi JA3 fingerprint.")
                # Print a small snippet of data to confirm it's real
                data = response.json()
                if isinstance(data, list) and len(data) > 0:
                    logger.info(f"Received data for token: {data[0].get('symbol', 'Unknown')}")
            else:
                logger.warning(f"FAILED: Received HTTP {response.status_code}.")
                if response.status_code == 403:
                    logger.warning("Cloudflare is still blocking the request (403 Forbidden).")
                elif response.status_code == 530:
                    logger.warning("Cloudflare Ray ID issue or origin DNS error (530).")
                    
    except Exception as e:
        logger.error(f"ERROR: An unexpected exception occurred: {e}")

if __name__ == "__main__":
    asyncio.run(test_direct_connection())
