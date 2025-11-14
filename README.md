# Medi-Minds

A real-time voice interaction application using OpenAI's Realtime API with a React frontend and FastAPI backend.

## Features

- Real-time voice recording and transcription
- WebSocket-based communication
- Modern React UI with audio playback
- FastAPI backend with OpenAI Realtime API integration

## Prerequisites

- Python 3.9+
- Node.js 18+
- `uv` package manager (recommended) or pip
- OpenAI API key

## Setup

### Backend Setup

1. Navigate to the backend directory:
```bash
cd backend
```

2. Install dependencies using `uv` (recommended):
```bash
uv sync
```

Or using pip:
```bash
pip install -r requirements.txt
```

3. Create a `.env` file in the `backend` directory:
```bash
OPENAI_API_KEY=your_api_key_here
```

### Frontend Setup

1. Navigate to the frontend directory:
```bash
cd frontend
```

2. Install dependencies:
```bash
npm install
```

## Running the Application

### Start the Backend Server

From the `backend` directory:

```bash
# Using uv
uv run uvicorn api_server:app --reload --host 0.0.0.0 --port 8000

# Or using python directly
python -m uvicorn api_server:app --reload --host 0.0.0.0 --port 8000
```

The backend will be available at `http://localhost:8000`

### Start the Frontend

From the `frontend` directory:

```bash
npm run dev
```

The frontend will be available at `http://localhost:5173` (or the port shown in the terminal)

## Usage

1. Make sure both backend and frontend servers are running
2. Open your browser and navigate to the frontend URL (usually `http://localhost:5173`)
3. Allow microphone permissions when prompted
4. Click "Start Recording" to begin voice interaction
5. Speak into your microphone
6. Click "Stop Recording" when finished
7. The transcript will appear in real-time, and audio responses will be played back

## Project Structure

```
Medi-Minds/
├── backend/
│   ├── api_server.py      # FastAPI server with WebSocket support
│   ├── main.py            # Original TUI application (kept for reference)
│   ├── audio_util.py      # Audio utilities
│   └── pyproject.toml     # Python dependencies
├── frontend/
│   ├── src/
│   │   ├── App.jsx        # Main React component
│   │   ├── App.css        # Styles
│   │   └── main.jsx       # React entry point
│   └── package.json       # Node dependencies
└── README.md
```

## Troubleshooting

### Backend Issues

- **Connection refused**: Make sure the backend server is running on port 8000
- **OpenAI API errors**: Verify your API key is set correctly in the `.env` file
- **Import errors**: Make sure all dependencies are installed

### Frontend Issues

- **WebSocket connection failed**: Check that the backend is running and accessible
- **Microphone not working**: Check browser permissions and ensure HTTPS (or localhost) is used
- **Audio playback issues**: Check browser console for errors

## Development

### Backend Development

The backend uses FastAPI with WebSocket support. The main server file is `api_server.py`.

### Frontend Development

The frontend uses React with Vite. Hot module replacement is enabled for development.

## License

Apache-2.0

