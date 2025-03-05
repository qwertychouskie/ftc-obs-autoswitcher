import json
import time
import asyncio
import signal
import websockets
import obswebsocket
from obswebsocket import requests as obsrequests

class FTCFieldSwitcher:
    def __init__(self, event_code, obs_host="localhost", obs_port=4444, obs_password=""):
        """
        Initialize the FTC Field Switcher with WebSocket connection details and OBS parameters
        
        Args:
            event_code: FTC event code for the WebSocket connection
            obs_host: OBS WebSocket host (default: localhost)
            obs_port: OBS WebSocket port (default: 4444)
            obs_password: OBS WebSocket password (default: empty string)
        """
        self.ftc_ws_url = f"ws://localhost:8080/stream/display/command/?code={event_code}"
        self.obs_host = obs_host
        self.obs_port = obs_port
        self.obs_password = obs_password
        self.obs_ws = None
        self.current_field = None
        self.field_scene_mapping = {
            1: "Field 1",
            2: "Field 2",
            # Add more field-to-scene mappings as needed
        }
        self.running = False
        self.ftc_websocket = None
        
    def connect_to_obs(self):
        """Establish connection to OBS WebSocket server"""
        self.obs_ws = obswebsocket.obsws(self.obs_host, self.obs_port, self.obs_password)
        try:
            self.obs_ws.connect()
            print("Connected to OBS WebSocket server")
            return True
        except Exception as e:
            print(f"Error connecting to OBS: {e}")
            return False
    
    def disconnect_from_obs(self):
        """Disconnect from OBS WebSocket server"""
        if self.obs_ws and self.obs_ws.ws.connected:
            try:
                self.obs_ws.disconnect()
                print("Disconnected from OBS WebSocket server")
            except Exception as e:
                print(f"Error disconnecting from OBS: {e}")
    
    def update_field_scene_mapping(self, field_scene_dict):
        """
        Update the mapping between fields and OBS scenes
        
        Args:
            field_scene_dict: Dictionary mapping field numbers to scene names
        """
        self.field_scene_mapping.update(field_scene_dict)
        print("Updated field-to-scene mapping")
    
    def switch_scene(self, field_number):
        """
        Switch OBS scene based on field number
        
        Args:
            field_number: Field number (integer)
            
        Returns:
            bool: Success status
        """
        if field_number not in self.field_scene_mapping:
            print(f"No scene mapping found for Field {field_number}")
            return False
            
        scene_name = self.field_scene_mapping[field_number]
        try:
            response = self.obs_ws.call(obsrequests.SetCurrentProgramScene(sceneName=scene_name))
            if response.status:
                print(f"Switched to scene: {scene_name} for Field {field_number}")
                return True
            else:
                print(f"Failed to switch scene: {response.error}")
                return False
        except Exception as e:
            print(f"Error switching scene: {e}")
            return False
    
    async def shutdown(self):
        """
        Gracefully shutdown the application
        """
        print("\nShutting down...")
        self.running = False
        
        # Close FTC WebSocket if it exists
        if self.ftc_websocket and not self.ftc_websocket.closed:
            await self.ftc_websocket.close()
            print("Closed FTC WebSocket connection")
            
        # Disconnect from OBS
        self.disconnect_from_obs()
        print("Shutdown complete")
    
    async def monitor_ftc_websocket(self):
        """
        Connect to FTC system WebSocket and monitor for SHOW_MATCH messages
        """
        if not self.connect_to_obs():
            print("Failed to connect to OBS. Exiting.")
            return
        
        print(f"Connecting to FTC WebSocket: {self.ftc_ws_url}")
        print(f"Current field-scene mapping: {json.dumps(self.field_scene_mapping, indent=2)}")
        print("Press Ctrl+C to exit")
        
        self.running = True
        try:
            async with websockets.connect(self.ftc_ws_url) as websocket:
                self.ftc_websocket = websocket
                print("Connected to FTC scoring system WebSocket")
                
                while self.running:
                    try:
                        # Set a timeout so we can check if we should still be running
                        message = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                        data = json.loads(message)
                        
                        # Check if it's a SHOW_MATCH message
                        if data.get("type") == "SHOW_MATCH":
                            # Extract field number
                            field_number = data.get("field")
                            if field_number is None and "params" in data:
                                field_number = data["params"].get("field")
                            
                            if field_number is not None and field_number != self.current_field:
                                print(f"Field change detected: {self.current_field} -> {field_number}")
                                if self.switch_scene(field_number):
                                    self.current_field = field_number
                    except asyncio.TimeoutError:
                        # This is expected - it's just to allow checking self.running periodically
                        continue
                    except json.JSONDecodeError as e:
                        if message != "pong": # This is expected to show up periodically
                            print(f"Error decoding message: {e}\nMessage: {message}")
                    except Exception as e:
                        if self.running:  # Only print errors if we're still supposed to be running
                            print(f"Error processing message: {e}")
                        
        except asyncio.CancelledError:
            print("WebSocket monitoring cancelled")
        except websockets.exceptions.ConnectionClosed:
            print("Connection to FTC scoring system closed")
        except Exception as e:
            if self.running:  # Only print errors if we're still supposed to be running
                print(f"WebSocket error: {e}")
        finally:
            await self.shutdown()

def handle_keyboard_interrupt():
    """
    Set up keyboard interrupt handling for the async main function
    """
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown_task()))

async def shutdown_task():
    """
    Signal handler to gracefully shutdown the application
    """
    print("\nInterrupt received, shutting down...")
    # Cancelling all tasks except the current one
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.get_event_loop().stop()

# Example usage
async def main():
    # Replace with your actual FTC event code
    EVENT_CODE = "your_event_code_here"
    
    # Create the switcher
    switcher = FTCFieldSwitcher(
        event_code=EVENT_CODE,
        obs_host="localhost", 
        obs_port=4444,
        obs_password="your_password_here"  # Set your OBS WebSocket password
    )
    
    # Customize field-to-scene mapping if needed
    # switcher.update_field_scene_mapping({
    #     1: "Field One Camera",
    #     2: "Field Two Camera"
    # })
    
    # Set up signal handling
    handle_keyboard_interrupt()
    
    # Start monitoring
    await switcher.monitor_ftc_websocket()

if __name__ == "__main__":
    try:
        # Run the async main function
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nScript terminated by user")
