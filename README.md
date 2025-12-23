# AI Investment Agent System

This project implements an AI-powered investment agent system designed for automated trading and portfolio management. The system consists of multiple agents that work together to analyze markets, execute trades, and manage risk.

## Architecture

The system is organized into the following components:

- **Agent Core**: The main agent logic and management system
- **Database**: Data persistence layer using MongoDB
- **Frontend**: Next.js web interface for monitoring and control
- **Server**: Python backend services
- **Tools**: Market data integration and trading tools
- **Utils**: Utility functions and API integrations

## Features

- Multi-agent architecture for distributed decision making
- Real-time market data integration
- Risk management and portfolio optimization
- Web-based dashboard for monitoring
- Automated trading execution
- Memory and learning capabilities

## Getting Started

### Prerequisites

- Python 3.8+
- Node.js 18+
- Docker & Docker Compose
- MongoDB & Redis (Managed via Docker)

### Installation

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd investment-agent
   ```

2. Install Python dependencies:
   ```bash
   pip install -r _Investment_v2/requirements.txt
   ```

3. Install frontend dependencies:
   ```bash
   cd _Investment_v2/frontend
   npm install
   ```

### Running the System

The system uses a decoupled architecture where the API, Worker, and Databases run independently.

1. **Start Infrastructure (Databases):**
   ```bash
   cd _Investment_v2
   docker-compose up -d
   ```

2. **Start the Backend API:**
   In a new terminal:
   ```bash
   cd _Investment_v2
   python server/main.py
   ```

3. **Start the Agent Worker (The Brain):**
   In another terminal:
   ```bash
   cd _Investment_v2
   python server/worker.py
   ```

4. **Start the Frontend:**
   In a final terminal:
   ```bash
   cd _Investment_v2/frontend
   npm run dev
   ```

5. **Access the dashboard:**
   Open `http://localhost:3000` in your browser.

## Configuration

The system uses environment variables for configuration. Key settings include:

- Database connection strings
- API keys for market data providers
- Trading platform credentials
- Risk management parameters

## Development

### Running Tests

```bash
pytest tests/
```

### Code Structure

- `agent/`: Agent core logic
- `database/`: Database models and connections
- `frontend/`: Next.js web application
- `server/`: Backend API services
- `tools/`: Trading and data tools
- `utils/`: Utility functions

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests
5. Submit a pull request

## License

This project is licensed under the MIT License.