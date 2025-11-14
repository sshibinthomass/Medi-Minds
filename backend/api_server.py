#!/usr/bin/env python3
"""
FastAPI server for Realtime API with WebSocket support
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from openai import AsyncOpenAI
from openai.resources.realtime.realtime import AsyncRealtimeConnection
from openai.types.realtime.session_updated_event import Session

from prompt import SYSTEM_PROMPT
from tools.multiply import get_multiply_tool_definition, execute_multiply

# Import for conversation item creation (for tool outputs)
try:
    from openai.types.beta.realtime.conversation_item_param import ConversationItemParam
except ImportError:
    # Fallback for different SDK versions
    try:
        from openai.types.realtime.conversation_item_param import ConversationItemParam
    except ImportError:
        # If both fail, we'll use dict format
        ConversationItemParam = None

project_root = Path(__file__).parent
load_dotenv(dotenv_path=project_root / ".env")

app = FastAPI()

# Enable CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],  # Vite default port
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = AsyncOpenAI()


class ConnectionManager:
    """Manages WebSocket connections and Realtime API connections"""

    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}
        self.realtime_connections: dict[str, AsyncRealtimeConnection] = {}
        self.sessions: dict[str, Session] = {}
        self.acc_items: dict[str, dict[str, Any]] = {}
        self.last_audio_item_ids: dict[str, str | None] = {}
        self.can_accept_audio: dict[str, bool] = {}
        self.valid_audio_item_ids: dict[str, set[str]] = {}  # Track valid item_ids per client
        self.audio_generation: dict[str, int] = {}  # Track audio generation per client

    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        self.active_connections[client_id] = websocket
        self.acc_items[client_id] = {}
        self.last_audio_item_ids[client_id] = None
        self.can_accept_audio[client_id] = False
        self.valid_audio_item_ids[client_id] = set()
        self.audio_generation[client_id] = 0

    def disconnect(self, client_id: str):
        if client_id in self.active_connections:
            del self.active_connections[client_id]
        if client_id in self.realtime_connections:
            del self.realtime_connections[client_id]
        if client_id in self.sessions:
            del self.sessions[client_id]
        if client_id in self.acc_items:
            del self.acc_items[client_id]
        if client_id in self.last_audio_item_ids:
            del self.last_audio_item_ids[client_id]
        if client_id in self.can_accept_audio:
            del self.can_accept_audio[client_id]
        if client_id in self.valid_audio_item_ids:
            del self.valid_audio_item_ids[client_id]
        if client_id in self.audio_generation:
            del self.audio_generation[client_id]

    async def send_personal_message(self, message: dict, client_id: str):
        if client_id in self.active_connections:
            await self.active_connections[client_id].send_json(message)

    async def handle_realtime_connection(self, client_id: str):
        """Handle the OpenAI Realtime API connection"""
        async with client.realtime.connect(model="gpt-realtime") as conn:
            self.realtime_connections[client_id] = conn

            # Configure session with server VAD and medical assistant prompt
            tool_def = get_multiply_tool_definition()
            print(f"Registering tool: {json.dumps(tool_def, indent=2)}")

            await conn.session.update(
                session={
                    "instructions": SYSTEM_PROMPT,
                    "voice": "sage",  # Professional and warm voice
                    "temperature": 0.7,  # Balanced between consistency and naturalness
                    "max_response_output_tokens": 4096,
                    "tools": [tool_def],
                    "tool_choice": "auto",  # Enable automatic tool calling
                    "turn_detection": {"type": "server_vad"},  # Match example pattern
                    "input_audio_transcription": {"model": "whisper-1"},  # Match example
                    "input_audio_format": "pcm16",  # Match example
                    "model": "gpt-realtime",
                    "type": "realtime",
                }
            )
            print("Session updated with tools")

            # Send connection ready message
            await self.send_personal_message({"type": "connection_ready", "status": "connected"}, client_id)

            async for event in conn:
                # Debug: log all events temporarily to diagnose
                event_dict = event.model_dump()
                if (
                    "function" in event.type.lower()
                    or "tool" in event.type.lower()
                    or "call" in event.type.lower()
                    or "action" in event.type.lower()
                ):
                    print(f"Function/Tool event: {event.type}")
                    print(f"Event data: {event_dict}")

                # Also log response events to see what's happening
                if event.type.startswith("response."):
                    print(f"Response event: {event.type}")
                    # Log content_part events as they might contain function calls
                    if event.type == "response.content_part.added":
                        event_dict = event.model_dump()
                        print(f"Content part added: {event_dict}")
                        # Check if it's a function call
                        if "function" in str(event_dict).lower() or "tool" in str(event_dict).lower():
                            print(f"FUNCTION CALL IN CONTENT PART: {event_dict}")

                if event.type == "session.created":
                    self.sessions[client_id] = event.session
                    if event.session.id:
                        await self.send_personal_message(
                            {
                                "type": "session_created",
                                "session_id": event.session.id,
                            },
                            client_id,
                        )
                    continue

                if event.type == "session.updated":
                    self.sessions[client_id] = event.session
                    continue

                if event.type == "response.output_audio.delta":
                    valid_ids = self.valid_audio_item_ids.get(client_id, set())
                    # If valid_ids is empty, this is the first audio after an interrupt - add it
                    if len(valid_ids) == 0:
                        valid_ids.add(event.item_id)
                        self.valid_audio_item_ids[client_id] = valid_ids

                    # Only send audio if this item_id is valid for the current generation
                    if event.item_id in valid_ids:
                        if event.item_id != self.last_audio_item_ids[client_id]:
                            self.last_audio_item_ids[client_id] = event.item_id

                        # Send audio data to frontend
                        await self.send_personal_message(
                            {
                                "type": "audio_delta",
                                "item_id": event.item_id,
                                "delta": event.delta,
                            },
                            client_id,
                        )
                    # If item_id is not valid, it's from a canceled response - ignore it
                    continue

                if event.type == "response.output_audio_transcript.delta":
                    # Only process transcript if this item_id is valid
                    if event.item_id in self.valid_audio_item_ids.get(client_id, set()):
                        try:
                            text = self.acc_items[client_id][event.item_id]
                        except KeyError:
                            self.acc_items[client_id][event.item_id] = event.delta
                        else:
                            self.acc_items[client_id][event.item_id] = text + event.delta

                        # Send transcript update to frontend
                        await self.send_personal_message(
                            {
                                "type": "transcript_delta",
                                "item_id": event.item_id,
                                "text": self.acc_items[client_id][event.item_id],
                            },
                            client_id,
                        )
                    continue

                # Track new response creation - mark its item_ids as valid
                if event.type == "response.created":
                    # New response started - clear old valid IDs, new ones will be added as they arrive
                    self.valid_audio_item_ids[client_id] = set()
                    # Log response creation to see if it has function calls
                    response_dict = event.model_dump()
                    print(f"RESPONSE CREATED - Full event: {json.dumps(response_dict, indent=2)}")
                    if "function" in str(response_dict).lower() or "tool" in str(response_dict).lower():
                        print(f"RESPONSE CREATED WITH FUNCTION: {response_dict}")
                    continue

                # Check content_part events for function calls - this is where function calls appear in Realtime API
                if event.type == "response.content_part.added":
                    event_dict = event.model_dump()
                    print(f"CONTENT PART ADDED - Full event: {json.dumps(event_dict, indent=2)}")

                    # Try multiple ways to access the part
                    part = (
                        getattr(event, "part", None) or event_dict.get("part") or event_dict.get("content_part") or {}
                    )

                    # Check if it's a dict or object
                    if hasattr(part, "model_dump"):
                        part = part.model_dump()
                    elif not isinstance(part, dict):
                        part = {"raw": str(part)}

                    part_type = part.get("type") if isinstance(part, dict) else None
                    print(f"Part type: {part_type}, Part data: {part}")

                    # Check for function_call in various formats
                    function_call = None
                    tool_call_id = None
                    function_name = None
                    arguments = {}

                    # Try different paths to find function call
                    if part_type == "function_call":
                        function_call = part.get("function_call") or part
                    elif "function_call" in part:
                        function_call = part["function_call"]
                    elif "function" in str(part).lower():
                        # Try to extract from part directly
                        function_call = part

                    if function_call:
                        if isinstance(function_call, dict):
                            function_name = (
                                function_call.get("name") or function_call.get("function_name") or part.get("name")
                            )
                            arguments = function_call.get("arguments") or function_call.get("args") or {}
                            tool_call_id = (
                                part.get("id")
                                or function_call.get("id")
                                or function_call.get("tool_call_id")
                                or part.get("tool_call_id")
                            )

                        print(f"FUNCTION CALL FOUND: name={function_name}, id={tool_call_id}, args={arguments}")

                        if function_name == "multiply" and tool_call_id:
                            try:
                                if isinstance(arguments, str):
                                    arguments = json.loads(arguments)

                                a = float(arguments.get("a", 0))
                                b = float(arguments.get("b", 0))
                                result = await execute_multiply(a, b)

                                print(f"Multiply result: {result}")

                                await conn.submit_tool_outputs(
                                    tool_outputs=[
                                        {
                                            "tool_call_id": tool_call_id,
                                            "output": str(result["result"]),
                                        }
                                    ]
                                )
                                print("Tool output submitted from content_part")
                            except Exception as e:
                                print(f"Error in content_part handler: {e}")
                                import traceback

                                traceback.print_exc()
                    continue

                # Handle function calls - check multiple event types
                function_call_handled = False
                event_dict = event.model_dump()

                # Handle response.requires_action - this is the key event for function calls
                if event.type == "response.requires_action":
                    print("RESPONSE REQUIRES ACTION - Function call detected!")
                    print(f"Full event: {event_dict}")

                    # Get the required action (should contain tool calls)
                    required_action = getattr(event, "required_action", None) or event_dict.get("required_action", {})
                    tool_calls = required_action.get("submit_tool_outputs", {}).get("tool_calls", [])

                    if not tool_calls:
                        # Try alternative paths
                        tool_calls = event_dict.get("tool_calls", [])
                        if not tool_calls and "tool_calls" in str(event_dict):
                            # Try to extract from the event structure
                            tool_calls = getattr(event, "tool_calls", [])

                    print(f"Tool calls found: {len(tool_calls)}")

                    tool_outputs = []
                    for tool_call in tool_calls:
                        tool_call_id = tool_call.get("id") or tool_call.get("tool_call_id")
                        function_name = tool_call.get("function", {}).get("name") or tool_call.get("name")
                        arguments_str = tool_call.get("function", {}).get("arguments") or tool_call.get(
                            "arguments", "{}"
                        )

                        print(f"Processing tool call: id={tool_call_id}, name={function_name}, args={arguments_str}")

                        if function_name == "multiply" and tool_call_id:
                            try:
                                # Parse arguments
                                if isinstance(arguments_str, str):
                                    arguments = json.loads(arguments_str)
                                else:
                                    arguments = arguments_str

                                a = float(arguments.get("a", 0))
                                b = float(arguments.get("b", 0))
                                result = await execute_multiply(a, b)

                                print(f"Multiply result: {result}")

                                tool_outputs.append(
                                    {
                                        "tool_call_id": tool_call_id,
                                        "output": str(result["result"]),
                                    }
                                )
                            except Exception as e:
                                print(f"Error executing multiply: {e}")
                                import traceback

                                traceback.print_exc()
                                tool_outputs.append(
                                    {
                                        "tool_call_id": tool_call_id,
                                        "output": f"Error: {str(e)}",
                                    }
                                )

                    if tool_outputs:
                        print(f"Submitting {len(tool_outputs)} tool outputs")
                        await conn.submit_tool_outputs(tool_outputs=tool_outputs)
                        function_call_handled = True
                        print("Tool outputs submitted successfully")

                # Handle response.function_call_arguments.done - this is the main event for function calls in Realtime API
                elif event.type == "response.function_call_arguments.done":
                    # Extract function call details from the event (following the example pattern)
                    call_id = getattr(event, "call_id", None) or event_dict.get("call_id")
                    function_name = getattr(event, "name", None) or event_dict.get("name")
                    arguments_str = getattr(event, "arguments", None) or event_dict.get("arguments", "{}")

                    print(f"Function call detected: name={function_name}, call_id={call_id}, arguments={arguments_str}")

                    if function_name == "multiply" and call_id:
                        try:
                            # Parse arguments
                            if isinstance(arguments_str, str):
                                arguments = json.loads(arguments_str)
                            else:
                                arguments = arguments_str

                            a = float(arguments.get("a", 0))
                            b = float(arguments.get("b", 0))
                            result = await execute_multiply(a, b)

                            print(f"Multiply result: {result}")

                            # Submit tool output using conversation.item.create (as per Realtime API pattern)
                            if ConversationItemParam:
                                await conn.conversation.item.create(
                                    item=ConversationItemParam(
                                        type="function_call_output",
                                        call_id=call_id,
                                        output=str(result["result"]),
                                    )
                                )
                            else:
                                # Fallback to dict format if ConversationItemParam is not available
                                await conn.conversation.item.create(
                                    item={
                                        "type": "function_call_output",
                                        "call_id": call_id,
                                        "output": str(result["result"]),
                                    }
                                )

                            print("Tool output submitted via conversation.item.create")

                            # Create a new response to continue the conversation
                            await conn.response.create(
                                response={
                                    "instructions": SYSTEM_PROMPT,
                                }
                            )

                            function_call_handled = True
                            print("New response created after tool execution")
                        except Exception as e:
                            print(f"Error executing multiply: {e}")
                            import traceback

                            traceback.print_exc()

                            # Submit error output
                            if call_id:
                                if ConversationItemParam:
                                    await conn.conversation.item.create(
                                        item=ConversationItemParam(
                                            type="function_call_output",
                                            call_id=call_id,
                                            output=f"Error: {str(e)}",
                                        )
                                    )
                                else:
                                    # Fallback to dict format
                                    await conn.conversation.item.create(
                                        item={
                                            "type": "function_call_output",
                                            "call_id": call_id,
                                            "output": f"Error: {str(e)}",
                                        }
                                    )

                            # Create response to inform user of error
                            await conn.response.create(
                                response={
                                    "instructions": "Inform the user that there was an error with the calculation.",
                                }
                            )

                            function_call_handled = True

                # Track function call start events
                if event.type in ["response.function_call_arguments.delta", "response.function_call.delta"]:
                    name = getattr(event, "name", None) or event_dict.get("name", "unknown")
                    print(f"Function call delta: {event.type}, name={name}")

                if function_call_handled:
                    continue

                # Forward other events to frontend
                await self.send_personal_message({"type": "realtime_event", "event": event.model_dump()}, client_id)


manager = ConnectionManager()


@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    await manager.connect(websocket, client_id)

    # Start the realtime connection handler
    realtime_task = asyncio.create_task(manager.handle_realtime_connection(client_id))

    try:
        while True:
            data = await websocket.receive_json()

            if data["type"] == "audio_chunk":
                # Only accept audio if we're in recording mode
                if client_id in manager.realtime_connections and manager.can_accept_audio.get(client_id, False):
                    conn = manager.realtime_connections[client_id]
                    audio_data = data["audio"]  # Base64 encoded audio
                    await conn.input_audio_buffer.append(audio=audio_data)

            elif data["type"] == "start_recording":
                # Cancel any ongoing response, clear buffer, and enable audio acceptance
                if client_id in manager.realtime_connections:
                    conn = manager.realtime_connections[client_id]
                    # Increment generation to invalidate all current audio
                    manager.audio_generation[client_id] = manager.audio_generation.get(client_id, 0) + 1
                    # Clear valid item IDs - old response audio will be ignored
                    manager.valid_audio_item_ids[client_id] = set()
                    # Cancel any ongoing response first
                    await conn.send({"type": "response.cancel"})
                    # Wait a tiny bit to ensure cancel is processed
                    await asyncio.sleep(0.05)
                    # Clear input audio buffer to ensure clean start
                    try:
                        await conn.input_audio_buffer.clear()
                    except Exception:
                        pass  # Buffer might already be empty
                    # Clear accumulated items
                    if client_id in manager.acc_items:
                        manager.acc_items[client_id] = {}
                    if client_id in manager.last_audio_item_ids:
                        manager.last_audio_item_ids[client_id] = None
                    # Now allow audio to be accepted
                    manager.can_accept_audio[client_id] = True
                    await manager.send_personal_message({"type": "recording_started"}, client_id)

            elif data["type"] == "hard_stop":
                # Hard stop - cancel everything immediately
                if client_id in manager.realtime_connections:
                    conn = manager.realtime_connections[client_id]
                    # Stop accepting audio
                    manager.can_accept_audio[client_id] = False
                    # Cancel any ongoing response
                    await conn.send({"type": "response.cancel"})
                    # Clear input audio buffer
                    try:
                        await conn.input_audio_buffer.clear()
                    except Exception:
                        pass
                    # Clear accumulated items
                    if client_id in manager.acc_items:
                        manager.acc_items[client_id] = {}
                    if client_id in manager.last_audio_item_ids:
                        manager.last_audio_item_ids[client_id] = None
                    await manager.send_personal_message({"type": "hard_stopped"}, client_id)

            elif data["type"] == "stop_recording":
                # Stop accepting audio first
                manager.can_accept_audio[client_id] = False
                # Commit audio buffer and create response
                if client_id in manager.realtime_connections:
                    conn = manager.realtime_connections[client_id]
                    await conn.input_audio_buffer.commit()
                    await conn.response.create(
                        response={
                            "instructions": SYSTEM_PROMPT,
                            # Don't pass tools here - they're already in session.update
                        }
                    )
                    await manager.send_personal_message({"type": "recording_stopped"}, client_id)

    except WebSocketDisconnect:
        realtime_task.cancel()
        manager.disconnect(client_id)


@app.get("/")
async def root():
    return {"message": "Medi-Minds Realtime API Server"}


@app.get("/health")
async def health():
    return {"status": "healthy"}
