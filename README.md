# YubiLab Worker v2.1

AI-powered autonomous agent backend for the YubiLab Cloud IDE. Flask + SocketIO with Groq LLM integration.

## Features

- **Autonomous AI Agent** with plan-execute-verify loop
- **Context Window Management** (4096 tokens) with part-by-part processing for large codebases
- **Real-time Streaming** via WebSocket (SocketIO)
- **Terminal Sessions** with command execution
- **Workspace Management** with file CRUD and project templates
- **File Upload** support (zip, rar, tar, 7z)
- **JWT Authentication** and API key security

## Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your Groq API key and secrets
python app.py
```

Server starts on `http://localhost:8000`.

## Architecture

```
yubilab-worker/
├── app.py                 # Main Flask + SocketIO application
├── agent/
│   ├── core.py            # AIAgent - autonomous agent engine
│   ├── context_manager.py # Splits large codebases into parts (4096 token limit)
│   ├── planner.py         # LLM-powered planning and action generation
│   └── executor.py        # Code execution engine
├── services/
│   ├── groq_service.py    # Groq LLM API (synchronous)
│   ├── terminal.py        # Terminal session management
│   └── workspace.py       # Workspace/file management
└── utils/
    └── security.py        # JWT auth, rate limiting, API key verification
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GROQ_API_KEY` | (required) | Groq API key |
| `GROQ_DEFAULT_MODEL` | `openai/gpt-oss-120b` | LLM model |
| `JWT_SECRET` | `yubilab-secret` | JWT signing secret |
| `WORKER_API_KEY` | (required) | API key for auth |
| `PORT` | `8000` | Server port |
| `MAX_OUTPUT_SIZE` | `4096` | Max output truncation size |
| `EXECUTION_TIMEOUT` | `30` | Command timeout (seconds) |

## License

MIT
