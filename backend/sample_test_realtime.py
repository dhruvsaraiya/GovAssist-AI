#!/usr/bin/env python3
"""
Autonomous Realtime Drone Agent using GPT-4o Realtime API
Provides speech input, image input, speech output, and drone control.
"""

import asyncio
import json
import base64
import logging
import os
import sys
import time
import threading
import queue
import argparse
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, asdict
import websockets
import pyaudio
import cv2
import numpy as np
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# Import vision analyzer and drone controller
from vision_analyzer import VisionAnalyzer
from drone_controller import DroneController

# Use our own settings class that reads from environment variables
# (bypassing the original settings.py to avoid pydantic validation errors)
class EnvironmentSettings:
    def __init__(self):
        self.azure_openai_endpoint = os.getenv('AZURE_OPENAI_ENDPOINT', 'https://your-resource.openai.azure.com')
        self.azure_openai_api_key = os.getenv('AZURE_OPENAI_API_KEY', 'your-api-key')
        self.realtime_deployment_name = os.getenv('AZURE_OPENAI_REALTIME_DEPLOYMENT', 'gpt-4o-realtime-preview')

settings = EnvironmentSettings()

try:
    from drone.simple_tello import SimpleTello
except ImportError:
    # Mock SimpleTello for testing
    class SimpleTello:
        def __init__(self):
            self.is_connected = False
        def connect(self): return True
        def streamon(self): pass
        def takeoff(self): return True
        def land(self): return True
        def move_up(self, distance): return True
        def move_down(self, distance): return True
        def move_left(self, distance): return True
        def move_right(self, distance): return True
        def move_forward(self, distance): return True
        def move_back(self, distance): return True
        def rotate_clockwise(self, angle): return True
        def rotate_counter_clockwise(self, angle): return True
        def get_battery(self): return 100
        def get_height(self): return 50
        def get_frame(self): return np.zeros((480, 640, 3), dtype=np.uint8)  # Mock camera frame
        def emergency(self): return True
        def streamoff(self): pass
        def end(self): pass

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class DroneState:
    """Track drone state and flight history."""
    is_flying: bool = False
    battery: int = 100
    height: int = 0
    last_image_analysis: str = ""
    movement_count: int = 0
    obstacles_detected: List[str] = None
    
    def __post_init__(self):
        if self.obstacles_detected is None:
            self.obstacles_detected = []

class RealtimeDroneAgent:
    """Real-time speech and vision drone controller using GPT-4o Realtime API."""
    
    def __init__(self, vision_only: bool = False):
        self.logger = logging.getLogger(__name__)
        self.vision_only = vision_only
        
        # Drone components
        self.drone = SimpleTello()  # Always create drone instance (mock or real)
        self.drone_state = DroneState()
        
        # Audio configuration
        self.CHUNK = 1024
        self.FORMAT = pyaudio.paInt16
        self.CHANNELS = 1
        self.RATE = 24000  # GPT-4o Realtime expects 24kHz
        
        # Audio streams
        self.audio = pyaudio.PyAudio()
        self.input_stream = None
        self.output_stream = None
        
        # WebSocket connection
        self.websocket = None
        self.is_connected = False
        
        # Audio queues
        self.input_audio_queue = queue.Queue()
        self.output_audio_queue = queue.Queue()
        
        # Control flags
        self.recording = False
        self.playing = False
        self.running = False
        
        # Vision analyzer for image processing
        self.vision_analyzer = VisionAnalyzer()
        
        # Drone controller for movement functions
        self.drone_controller = DroneController(self.drone, self.drone_state, vision_only)
        
        # Function registry for drone commands
        self.functions = {}
        self._register_drone_functions()
        
        # Image streaming
        self.last_image_time = 0
        self.image_stream_interval = 3.0  # Send image every 3 seconds
        
    def _register_drone_functions(self):
        """Register drone control functions."""
        
        # Define all drone control functions using the drone controller
        self.functions = {
            "takeoff": self.drone_controller.takeoff,
            "land": self.drone_controller.land,
            "move_forward": self.drone_controller.move_forward,
            "move_backward": self.drone_controller.move_backward,
            "move_left": self.drone_controller.move_left,
            "move_right": self.drone_controller.move_right,
            "move_up": self.drone_controller.move_up,
            "move_down": self.drone_controller.move_down,
            "rotate_clockwise": self.drone_controller.rotate_clockwise,
            "rotate_counter_clockwise": self.drone_controller.rotate_counter_clockwise,
            "curve_xyz_speed": self.drone_controller.curve_xyz_speed,
            "go_xyz_speed": self.drone_controller.go_xyz_speed,
            "get_drone_status": self.drone_controller.get_drone_status,
            "capture_and_analyze_image": self._capture_and_analyze_image
        }
    
    async def connect_realtime(self):
        """Connect to Azure OpenAI Realtime API."""
        try:
            # Convert HTTPS to WSS for WebSocket connection
            endpoint = settings.azure_openai_endpoint.replace('https://', 'wss://').replace('http://', 'ws://')
            
            # WebSocket URL for Azure OpenAI Realtime
            ws_url = f"{endpoint}/openai/realtime?api-version=2024-10-01-preview&deployment={settings.realtime_deployment_name}"
            
            headers = {
                "api-key": settings.azure_openai_api_key,
                "OpenAI-Beta": "realtime=v1"
            }
            
            self.logger.info(f"🔌 Connecting to realtime API...")
            
            self.websocket = await websockets.connect(
                ws_url,
                additional_headers=headers,
                ping_interval=20,
                ping_timeout=10
            )
            
            self.is_connected = True
            self.logger.info("✅ Connected to GPT-4o Realtime API")
            
            # Configure session with drone tools
            await self._configure_realtime_session()
            
            # Set up vision analyzer with websocket
            self.vision_analyzer.set_websocket(self.websocket)
            
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Connection failed: {e}")
            return False
    
    async def _configure_realtime_session(self):
        """Configure the realtime session with drone tools."""
        
        session_config = {
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],  # Vision is handled separately
                "instructions": """You are an intelligent real-time drone controller assistant. You can do simple movements or complex maneuvers by moving in multiple steps

🚁 EXECUTION STYLE:
- WHen given complex tasks, you break them into smaller tasks and execute continuously. 
- If asked go in a circle of 100cm, you break circle into smaller waypoints, calculate where to move and execute each step without pausing untill you complete circle
- Execute multi-step commands smoothly: "move forward 50cm then turn right and go forward 50cm again" should flow naturally without any stop in between
- Don't ask for confirmation or stop between steps
- Full 360 rotation is allowed, no need to pause or complete in smaller steps
- When asked to move while captuing, capture images every 1 sec and check for obstacles before moving next step 

🎙️ COMMUNICATION:
- Give brief status updates only when asked
- Dont announce any result of any operations or movements performed unless i ask for status
- Be conversational but efficient and as short as possible


🚁 DRONE CONTROL:
- Take off and land the drone safely
- Move the drone in all directions (forward, backward, up, down, left, right, curve ) 
- Rotate the drone clockwise and counter-clockwise  
- Get real-time drone status (battery, height, flight state)
- Capture and analyze live camera images from the drone
""",

                "voice": "alloy",
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "input_audio_transcription": {
                    "model": "whisper-1"
                },
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "prefix_padding_ms": 300,
                    "silence_duration_ms": 500
                },
                "tools": [
                    {
                        "type": "function",
                        "name": "takeoff",
                        "description": "Take off the drone safely - only use when drone is on ground",
                        "parameters": {"type": "object", "properties": {}}
                    },
                    {
                        "type": "function",
                        "name": "land", 
                        "description": "Land the drone safely at current location",
                        "parameters": {"type": "object", "properties": {}}
                    },
                    {
                        "type": "function",
                        "name": "move_forward",
                        "description": "Move drone forward by specified distance",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "distance": {
                                    "type": "integer",
                                    "description": "Distance in centimeters (5-100cm recommended)",
                                    "minimum": 5,
                                    "maximum": 300
                                }
                            },
                            "required": ["distance"]
                        }
                    },
                    {
                        "type": "function",
                        "name": "move_backward",
                        "description": "Move drone backward by specified distance", 
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "distance": {
                                    "type": "integer",
                                    "description": "Distance in centimeters (5-100cm)",
                                    "minimum": 5,
                                    "maximum": 100
                                }
                            },
                            "required": ["distance"]
                        }
                    },
                    {
                        "type": "function",
                        "name": "move_left",
                        "description": "Move drone left by specified distance",
                        "parameters": {
                            "type": "object", 
                            "properties": {
                                "distance": {
                                    "type": "integer",
                                    "description": "Distance in centimeters (5-100cm)",
                                    "minimum": 5,
                                    "maximum": 100
                                }
                            },
                            "required": ["distance"]
                        }
                    },
                    {
                        "type": "function",
                        "name": "move_right",
                        "description": "Move drone right by specified distance",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "distance": {
                                    "type": "integer", 
                                    "description": "Distance in centimeters (5-100cm)",
                                    "minimum": 5,
                                    "maximum": 100
                                }
                            },
                            "required": ["distance"]
                        }
                    },
                    {
                        "type": "function",
                        "name": "move_up",
                        "description": "Move drone up by specified distance",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "distance": {
                                    "type": "integer",
                                    "description": "Distance in centimeters (20-80cm recommended)",
                                    "minimum": 20,
                                    "maximum": 100
                                }
                            },
                            "required": ["distance"]
                        }
                    },
                    {
                        "type": "function",
                        "name": "move_down", 
                        "description": "Move drone down by specified distance",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "distance": {
                                    "type": "integer",
                                    "description": "Distance in centimeters (20-80cm recommended)",
                                    "minimum": 20,
                                    "maximum": 100
                                }
                            },
                            "required": ["distance"]
                        }
                    },
                    {
                        "type": "function",
                        "name": "rotate_clockwise",
                        "description": "Rotate drone clockwise by specified angle",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "angle": {
                                    "type": "integer",
                                    "description": "Rotation angle in degrees (30-180°)",
                                    "minimum": 30,
                                    "maximum": 180
                                }
                            },
                            "required": ["angle"]
                        }
                    },
                    {
                        "type": "function",
                        "name": "rotate_counter_clockwise",
                        "description": "Rotate drone counter-clockwise by specified angle", 
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "angle": {
                                    "type": "integer",
                                    "description": "Rotation angle in degrees (30-180°)",
                                    "minimum": 30,
                                    "maximum": 180
                                }
                            },
                            "required": ["angle"]
                        }
                    },
                    {
                        "type": "function",
                        "name": "get_drone_status",
                        "description": "Get current drone status including battery, height, and flight state",
                        "parameters": {"type": "object", "properties": {}}
                    },
                    {
                        "type": "function",
                        "name": "capture_and_analyze_image",
                        "description": "Capture current camera view and provide description of what the drone sees",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "focus": {
                                    "type": "string",
                                    "description": "What to focus on: obstacles, objects, navigation, landing_spot",
                                    "enum": ["obstacles", "objects", "navigation", "landing_spot"]
                                }
                            },
                            "required": ["focus"]
                        }
                    },
                    {
                        "type": "function",
                        "name": "curve_xyz_speed",
                        "description": "Fly in a curve from current position to specified coordinates with speed control. Tello requires: (1) each waypoint must be at least ~20 cm away from the current position, (2) the curve radius must be between 50 cm and 1000 cm, and (3) the flight speed must be between 10–60 cm/s. The schema enforces per-field ranges, but your runtime code should also validate the waypoint distance and curve radius before sending the command.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                            "x1": {
                                "type": "integer",
                                "description": "First waypoint X coordinate in cm (recommended range: -30 to 30). Must be ≥20 cm from origin together with y1/z1."
                            },
                            "y1": {
                                "type": "integer",
                                "description": "First waypoint Y coordinate in cm (recommended range: -30 to 30). Must be ≥20 cm from origin together with x1/z1."
                            },
                            "z1": {
                                "type": "integer",
                                "description": "First waypoint Z coordinate in cm (recommended range: -30 to 30). Must be ≥20 cm from origin together with x1/y1."
                            },
                            "x2": {
                                "type": "integer",
                                "description": "Second waypoint X coordinate in cm (recommended range: -30 to 30). Must be ≥20 cm from origin together with y2/z2."
                            },
                            "y2": {
                                "type": "integer",
                                "description": "Second waypoint Y coordinate in cm (recommended range: -30 to 30). Must be ≥20 cm from origin together with x2/z2."
                            },
                            "z2": {
                                "type": "integer",
                                "description": "Second waypoint Z coordinate in cm (recommended range: -30 to 30). Must be ≥20 cm from origin together with x2/y2."
                            },
                            "speed": {
                                "type": "integer",
                                "description": "Flight speed in cm/s (allowed range: 10–60)."
                            }
                            },
                            "required": ["x1", "y1", "z1", "x2", "y2", "z2", "speed"],
                            "examples": [
                                { "x1": 20, "y1": 0, "z1": 0, "x2": 20, "y2": 30, "z2": 0, "speed": 30 },
                                { "x1": 15, "y1": -20, "z1": 0, "x2": -15, "y2": 25, "z2": 0, "speed": 25 }
                            ]
                        }
                    },
                    {
                        "type": "function",
                        "name": "go_xyz_speed",
                        "description": "Fly directly to specified coordinates relative to current position with speed control",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "x": {
                                    "type": "integer",
                                    "description": "X coordinate in cm (-30 to 30, positive=right)",
                                    "minimum": -30,
                                    "maximum": 30
                                },
                                "y": {
                                    "type": "integer",
                                    "description": "Y coordinate in cm (-30 to 30, positive=forward)",
                                    "minimum": -30,
                                    "maximum": 30
                                },
                                "z": {
                                    "type": "integer",
                                    "description": "Z coordinate in cm (-30 to 30, positive=up)",
                                    "minimum": -30,
                                    "maximum": 30
                                },
                                "speed": {
                                    "type": "integer",
                                    "description": "Flight speed in cm/s (10-100)",
                                    "minimum": 10,
                                    "maximum": 100
                                }
                            },
                            "required": ["x", "y", "z", "speed"]
                        }
                    }
                ],
                "tool_choice": "auto",
                "temperature": 0.7,
                "max_response_output_tokens": 4096
            }
        }
        
        await self.websocket.send(json.dumps(session_config))
        self.logger.info("🔧 Realtime session configured with drone tools")
    
    async def _capture_and_analyze_image(self, focus: str = "objects", **kwargs) -> str:
        """Capture and analyze image from drone camera using the vision analyzer."""
        self.logger.info(f"📸 Delegating to vision analyzer: {focus}")
        
        try:
            result = await self.vision_analyzer.capture_and_analyze_image(
                drone=self.drone, 
                focus=focus, 
                vision_only=self.vision_only
            )
            
            # Update drone state with analysis result
            self.drone_state.last_image_analysis = result
            
            return result
            
        except Exception as e:
            error_msg = f"Vision analysis error: {str(e)}"
            self.logger.error(f"❌ {error_msg}")
            return error_msg
    
    def start_audio_streams(self):
        """Start audio input and output streams."""
        try:
            # Input stream (microphone)
            self.input_stream = self.audio.open(
                format=self.FORMAT,
                channels=self.CHANNELS,
                rate=self.RATE,
                input=True,
                frames_per_buffer=self.CHUNK
            )
            
            # Output stream (speakers)
            self.output_stream = self.audio.open(
                format=self.FORMAT,
                channels=self.CHANNELS,
                rate=self.RATE,
                output=True,
                frames_per_buffer=self.CHUNK
            )
            
            self.recording = True
            self.playing = True
            
            self.logger.info("🎤🔊 Audio streams started")
            
            # Start audio worker threads
            threading.Thread(target=self._audio_input_worker, daemon=True).start()
            threading.Thread(target=self._audio_output_worker, daemon=True).start()
            
        except Exception as e:
            self.logger.error(f"❌ Failed to start audio streams: {e}")
    
    def _audio_input_worker(self):
        """Worker thread for audio input."""
        while self.recording and self.running:
            try:
                if self.input_stream:
                    data = self.input_stream.read(self.CHUNK, exception_on_overflow=False)
                    self.input_audio_queue.put(data)
            except Exception as e:
                self.logger.error(f"❌ Audio input error: {e}")
                time.sleep(0.1)
    
    def _audio_output_worker(self):
        """Worker thread for audio output."""
        while self.playing and self.running:
            try:
                if not self.output_audio_queue.empty():
                    audio_data = self.output_audio_queue.get(timeout=0.1)
                    if self.output_stream:
                        self.output_stream.write(audio_data)
            except queue.Empty:
                continue
            except Exception as e:
                self.logger.error(f"❌ Audio output error: {e}")
                time.sleep(0.1)
    
    async def _send_audio_to_api(self):
        """Send microphone audio to the realtime API."""
        while self.is_connected and self.running:
            try:
                if not self.input_audio_queue.empty():
                    audio_chunk = self.input_audio_queue.get_nowait()
                    
                    # Convert to base64
                    audio_base64 = base64.b64encode(audio_chunk).decode()
                    
                    # Send to API
                    message = {
                        "type": "input_audio_buffer.append",
                        "audio": audio_base64
                    }
                    
                    await self.websocket.send(json.dumps(message))
                
                await asyncio.sleep(0.01)  # Small delay
                
            except queue.Empty:
                await asyncio.sleep(0.01)
            except Exception as e:
                self.logger.error(f"❌ Audio send error: {e}")
                await asyncio.sleep(0.1)
    
    async def _handle_realtime_messages(self):
        """Handle messages from the realtime API."""
        try:
            async for message in self.websocket:
                data = json.loads(message)
                await self._process_message(data)
                
        except websockets.exceptions.ConnectionClosed:
            self.logger.info("🔌 WebSocket connection closed")
            self.is_connected = False
        except Exception as e:
            self.logger.error(f"❌ Message handling error: {e}")
            self.is_connected = False
    
    async def _process_message(self, data):
        """Process individual message from the API."""
        msg_type = data.get("type")
        
        if msg_type == "session.created":
            self.logger.info("🎉 Realtime session created")
            
        elif msg_type == "input_audio_buffer.speech_started":
            self.logger.info("🗣️  Speech detected - listening...")
            
        elif msg_type == "input_audio_buffer.speech_stopped": 
            self.logger.info("⏸️  Speech ended - processing...")
            
        elif msg_type == "conversation.item.input_audio_transcription.completed":
            transcript = data.get("transcript", "")
            self.logger.info(f"📝 You said: {transcript}")
            
        elif msg_type == "response.audio.delta":
            # Stream audio response to speakers
            if "delta" in data:
                audio_data = base64.b64decode(data["delta"])
                self.output_audio_queue.put(audio_data)
                
        elif msg_type == "response.function_call_arguments.delta":
            # Function call in progress
            function_name = data.get("name", "")
            if function_name:
                self.logger.info(f"🔧 Calling function: {function_name}")
                
        elif msg_type == "response.function_call_arguments.done":
            # Execute function call
            call_id = data.get("call_id")
            function_name = data.get("name")
            arguments_str = data.get("arguments", "{}")
            
            try:
                arguments = json.loads(arguments_str)
                result = await self._execute_function(function_name, arguments)
                
                # Send result back to API
                response = {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": result
                    }
                }
                
                await self.websocket.send(json.dumps(response))
                
                # Check if this is a vision analysis that's already handling its own response
                if result.startswith("[PROCESSING]"):
                    self.logger.info(f"🔄 {function_name} handling its own response - skipping duplicate trigger")
                else:
                    # Automatically trigger next step for multi-step commands
                    self.logger.info(f"🎙️ Triggering continuation for: {function_name}")
                    
                    # Small delay to ensure function result is processed
                    await asyncio.sleep(0.1)
                    
                    # Trigger response generation to continue with next steps
                    response_request = {
                        "type": "response.create"
                    }
                    await self.websocket.send(json.dumps(response_request))
                
            except Exception as e:
                self.logger.error(f"❌ Function execution error: {e}")
                
        elif msg_type == "error":
            self.logger.error(f"❌ API Error: {data}")
    
    async def _execute_function(self, function_name: str, arguments: dict) -> str:
        """Execute a drone function and return result."""
        try:
            if function_name in self.functions:
                func = self.functions[function_name]
                result = await func(**arguments)
                self.logger.info(f"✅ {function_name}: {result}")
                return result
            else:
                error_msg = f"Unknown function: {function_name}"
                self.logger.error(f"❌ {error_msg}")
                return error_msg
                
        except Exception as e:
            error_msg = f"Function {function_name} error: {str(e)}"
            self.logger.error(f"❌ {error_msg}")
            return error_msg
    
    async def start_realtime_control(self):
        """Start the real-time drone control session."""
        self.running = True
        
        try:
            # Initialize drone if not vision-only
            if not self.vision_only:
                self._setup_drone()
            
            # Start audio streams
            self.start_audio_streams()
            
            self.logger.info("🚁🎙️ Real-time drone control started!")
            self.logger.info("💡 Speak your commands! Examples:")
            self.logger.info("   • 'Take off'")
            self.logger.info("   • 'Move forward 50 centimeters'") 
            self.logger.info("   • 'Rotate 90 degrees clockwise'")
            self.logger.info("   • 'What do you see?' (for camera analysis)")
            self.logger.info("   • 'Land'")
            self.logger.info("   • 'Emergency stop'")
            self.logger.info("💡 Press Ctrl+C to stop")
            
            # Start main communication loops
            await asyncio.gather(
                self._send_audio_to_api(),
                self._handle_realtime_messages()
            )
            
        except KeyboardInterrupt:
            self.logger.info("👋 Real-time control stopped by user")
        except Exception as e:
            self.logger.error(f"❌ Real-time control error: {e}")
        finally:
            await self.cleanup()
    
    def _setup_drone(self):
        """Initialize drone connection.""" 
        try:
            self.logger.info("Connecting to Tello drone...")
            if self.drone.connect():
                self.drone.streamon()
                self.drone_state.battery = self.drone.get_battery()
                self.logger.info(f"✅ Drone connected, battery: {self.drone_state.battery}%")
            else:
                self.logger.error("❌ Failed to connect to drone")
        except Exception as e:
            self.logger.error(f"❌ Drone setup error: {e}")
    
    async def cleanup(self):
        """Clean up all resources."""
        self.logger.info("🧹 Cleaning up...")
        
        self.running = False
        self.recording = False
        self.playing = False
        self.is_connected = False
        
        # Clean up audio
        if self.input_stream:
            try:
                self.input_stream.stop_stream()
                self.input_stream.close()
            except:
                pass
                
        if self.output_stream:
            try:
                self.output_stream.stop_stream() 
                self.output_stream.close()
            except:
                pass
                
        try:
            self.audio.terminate()
        except:
            pass
        
        # Clean up drone
        if self.drone and not self.vision_only:
            try:
                if self.drone_state.is_flying:
                    self.logger.info("Landing drone before cleanup...")
                    self.drone.land()
                    time.sleep(2)
                
                self.drone.streamoff()
                self.drone.end()
            except Exception as e:
                self.logger.warning(f"Drone cleanup warning: {e}")
        
        # Close WebSocket
        if self.websocket:
            try:
                await self.websocket.close()
            except:
                pass
        
        self.logger.info("✅ Cleanup completed")

async def main():
    """Main function to run the realtime drone agent."""
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Autonomous Realtime Drone Agent')
    parser.add_argument('--vision-only', action='store_true', default=True,
                        help='Run in vision-only mode (simulation) - default: True')
    parser.add_argument('--real-drone', action='store_true', default=False,
                        help='Connect to real drone (overrides --vision-only)')
    
    args = parser.parse_args()
    
    # Determine vision_only mode
    vision_only = not args.real_drone  # If real_drone is True, vision_only is False
    
    print(f"🤖 Mode: {'VISION ONLY (Simulation)' if vision_only else 'REAL DRONE'}")
    
    # Check environment variables
    required_vars = ['AZURE_OPENAI_ENDPOINT', 'AZURE_OPENAI_API_KEY']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        logger.error(f"❌ Missing environment variables: {missing_vars}")
        logger.info("💡 Set them with:")
        for var in missing_vars:
            logger.info(f"   export {var}='your-value'")
        return
    
    # Create agent with specified mode
    agent = RealtimeDroneAgent(vision_only=vision_only)
    
    try:
        # Connect to realtime API
        if await agent.connect_realtime():
            # Start real-time control
            await agent.start_realtime_control()
        else:
            logger.error("❌ Failed to connect to realtime API")
            
    except Exception as e:
        logger.error(f"❌ Main error: {e}")

if __name__ == "__main__":
    print("🚁🎙️ GPT-4o Realtime Drone Agent")
    print("=" * 50)
    print()
    print("📋 SETUP INSTRUCTIONS:")
    print("1. Install dependencies:")
    print("   pip install websockets pyaudio opencv-python numpy")
    print("2. Set environment variables:")
    print("   export AZURE_OPENAI_ENDPOINT='https://your-resource.openai.azure.com'")
    print("   export AZURE_OPENAI_API_KEY='your-api-key'")
    print("   export AZURE_OPENAI_REALTIME_DEPLOYMENT='gpt-4o-realtime-preview'")
    print("3. Make sure you have:")
    print("   • Microphone and speakers connected")
    print("   • Tello drone (for real drone mode)")
    print()
    print("🚀 USAGE:")
    print("   • Vision Only (Safe Simulation):  python autonomous_realtime_drone_agent.py")
    print("   • Real Drone Mode:               python autonomous_realtime_drone_agent.py --real-drone")
    print()
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
    except Exception as e:
        logger.error(f"❌ Startup error: {e}")