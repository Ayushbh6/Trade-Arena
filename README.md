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
- MongoDB

### Installation

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd investment-agent
   ```

2. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Install frontend dependencies:
   ```bash
   cd frontend
   npm install
   ```

4. Set up environment variables:
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

### Running the System

1. Start the backend server:
   ```bash
   python server/main.py
   ```

2. Start the frontend:
   ```bash
   cd frontend
   npm run dev
   ```

3. Access the dashboard at `http://localhost:3000`

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