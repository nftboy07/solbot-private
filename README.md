# Solbot 🤖

A high-performance Solana trading bot built for speed, reliability, and intelligence.

## 🏛 Architecture
- **Dual-Node RPC Strategy**: Intelligent load balancing and fallback between multiple RPC providers.
- **Websocket Real-time Engine**: Sub-millisecond market data ingestion and transaction monitoring.
- **Proxy Fleet**: Distributed proxy management to avoid rate limits and enhance anonymity.
- **Metrics Pipeline**: Integrated monitoring and performance tracking for every trade.

## ✨ Features
- **AI Llama 3.1 405B Scoring**: Advanced token analysis and social sentiment scoring using state-of-the-art LLMs.
- **Jupiter & Pump.fun Sniping**: Lightning-fast execution on Solana's most popular DEXs and launchpads.
- **Secret Sanitization**: Built-in protection to ensure sensitive state and credentials are never leaked.
- **Asynchronous Architecture**: Fully non-blocking I/O for maximum throughput.

## 🚀 Getting Started

### Prerequisites
- Python 3.10+
- Solana RPC Endpoints
- Environment variables configured (see `.env.example`)

### Installation
1. Clone the repository:
   ```bash
   git clone https://github.com/nftboy07/solbot.git
   cd solbot
   ```
2. Set up a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows use `venv\Scripts\activate`
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Configure your environment:
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

## 📜 License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
