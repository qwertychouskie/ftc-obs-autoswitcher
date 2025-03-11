# OBS Scene autoswitcher
# https://claude.ai/chat/a8a2e73c-a94c-4180-a0fa-83e4ee2a66f0

import json
import time
import asyncio
import signal
import threading
import websockets
import obswebsocket
from obswebsocket import requests as obsrequests
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

class FTCFieldSwitcher:
    def __init__(self, event_code, scoring_host="localhost", scoring_port=80, obs_host="localhost", obs_port=4455, obs_password=""):
        """
        Initialize the FTC Field Switcher with WebSocket connection details and OBS parameters
        
        Args:
            event_code: FTC event code for the WebSocket connection
            scoring_host: Host address for the FTC scoring system (default: localhost)
            scoring_port: Port for the FTC scoring system (default: 80)
            obs_host: OBS WebSocket host (default: localhost)
            obs_port: OBS WebSocket port (default: 4455)
            obs_password: OBS WebSocket password (default: empty string)
        """
        self.scoring_host = scoring_host
        self.scoring_port = scoring_port
        self.ftc_ws_url = f"ws://{scoring_host}:{scoring_port}/stream/display/command/?code={event_code}"
        self.event_code = event_code
        self.obs_host = obs_host
        self.obs_port = obs_port
        self.obs_password = obs_password
        self.obs_ws = None
        self.current_field = None
        self.field_scene_mapping = {
            1: "Field 1",
            2: "Field 2",
        }
        self.running = False
        self.ftc_websocket = None
        self.log_callback = None
        
    def set_log_callback(self, callback):
        """
        Set a callback function for logging
        
        Args:
            callback: Function that accepts a string message
        """
        self.log_callback = callback
        
    def log(self, message):
        """
        Log a message to console and via callback if set
        
        Args:
            message: Message to log
        """
        print(message)
        if self.log_callback:
            self.log_callback(message)
        
    def connect_to_obs(self):
        """Establish connection to OBS WebSocket server"""
        self.obs_ws = obswebsocket.obsws(self.obs_host, self.obs_port, self.obs_password)
        try:
            self.obs_ws.connect()
            self.log("Connected to OBS WebSocket server")
            return True
        except Exception as e:
            self.log(f"Error connecting to OBS: {e}")
            return False
    
    def disconnect_from_obs(self):
        """Disconnect from OBS WebSocket server"""
        if self.obs_ws and self.obs_ws.ws.connected:
            try:
                self.obs_ws.disconnect()
                self.log("Disconnected from OBS WebSocket server")
            except Exception as e:
                self.log(f"Error disconnecting from OBS: {e}")
    
    def update_field_scene_mapping(self, field_scene_dict):
        """
        Update the mapping between fields and OBS scenes
        
        Args:
            field_scene_dict: Dictionary mapping field numbers to scene names
        """
        self.field_scene_mapping.update(field_scene_dict)
        self.log("Updated field-to-scene mapping")
    
    def switch_scene(self, field_number):
        """
        Switch OBS scene based on field number
        
        Args:
            field_number: Field number (integer)
            
        Returns:
            bool: Success status
        """
        if field_number not in self.field_scene_mapping:
            self.log(f"No scene mapping found for Field {field_number}")
            return False
            
        scene_name = self.field_scene_mapping[field_number]
        try:
            response = self.obs_ws.call(obsrequests.SetCurrentProgramScene(sceneName=scene_name))
            if response.status:
                self.log(f"Switched to scene: {scene_name} for Field {field_number}")
                return True
            else:
                self.log(f"Failed to switch scene: {response.error}")
                return False
        except Exception as e:
            self.log(f"Error switching scene: {e}")
            return False
    
    async def shutdown(self):
        """
        Gracefully shutdown the monitoring
        """
        self.log("Shutting down...")
        self.running = False
        
        # Close FTC WebSocket if it exists
        if self.ftc_websocket and not self.ftc_websocket.closed:
            await self.ftc_websocket.close()
            self.log("Closed FTC WebSocket connection")
            
        # Disconnect from OBS
        self.disconnect_from_obs()
        self.log("Shutdown complete")
    
    async def monitor_ftc_websocket(self):
        """
        Connect to FTC system WebSocket and monitor for SHOW_MATCH messages
        """
        if not self.connect_to_obs():
            self.log("Failed to connect to OBS. Exiting.")
            return
        
        self.log(f"Connecting to FTC WebSocket: {self.ftc_ws_url}")
        self.log(f"Current field-scene mapping: {json.dumps(self.field_scene_mapping, indent=2)}")
        
        self.running = True
        try:
            async with websockets.connect(self.ftc_ws_url) as websocket:
                self.ftc_websocket = websocket
                self.log("Connected to FTC scoring system WebSocket")
                
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
                                self.log(f"Field change detected: {self.current_field} -> {field_number}")
                                if self.switch_scene(field_number):
                                    self.current_field = field_number
                    except asyncio.TimeoutError:
                        # This is expected - it's just to allow checking self.running periodically
                        continue
                    except json.JSONDecodeError as e:
                        if message != "pong": # This is expected to show up periodically
                            self.log(f"Error decoding message: {e}")
                    except websockets.exceptions.ConnectionClosed as e:
                        if self.running:  # Only print errors if we're still supposed to be running
                            raise
                    except Exception as e:
                        if self.running:  # Only print errors if we're still supposed to be running
                            self.log(f"Error processing message: {e}")
                        
        except asyncio.CancelledError:
            self.log("WebSocket monitoring cancelled")
        except websockets.exceptions.ConnectionClosed:
            self.log("Connection to FTC scoring system closed.  Is the scoring server still running, and is the event code correct?")
        except Exception as e:
            if self.running:  # Only print errors if we're still supposed to be running
                self.log(f"WebSocket error: {e}")
                self.log(f"Is the scoring system host correct?")
        finally:
            await self.shutdown()

class FTCSwitcherGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("FTC OBS Scene Switcher")
        self.root.geometry("700x600")
        self.root.resizable(True, True)
        
        self.switcher = None
        self.async_loop = None
        self.monitor_task = None
        self.thread = None
        
        self.create_widgets()
        self.load_config()
        
    def create_widgets(self):
        # Main frame
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Create notebook with tabs
        notebook = ttk.Notebook(main_frame)
        notebook.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Connection settings tab
        conn_frame = ttk.Frame(notebook, padding="10")
        notebook.add(conn_frame, text="Connection Settings")
        
        # FTC Settings
        ttk.Label(conn_frame, text="FTC Settings", font=("", 12, "bold")).grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 5))
        
        ttk.Label(conn_frame, text="Event Code:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.event_code_var = tk.StringVar()
        ttk.Entry(conn_frame, textvariable=self.event_code_var, width=30).grid(row=1, column=1, sticky=tk.W, pady=2)

        ttk.Label(conn_frame, text="Scoring System Host:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.scoring_host_var = tk.StringVar(value="localhost")
        ttk.Entry(conn_frame, textvariable=self.scoring_host_var, width=30).grid(row=2, column=1, sticky=tk.W, pady=2)

        ttk.Label(conn_frame, text="Port:").grid(row=2, column=2, sticky=tk.W, pady=2)
        self.scoring_port_var = tk.StringVar(value="80")
        ttk.Entry(conn_frame, textvariable=self.scoring_port_var, width=6).grid(row=2, column=3, sticky=tk.W, pady=2)

        # OBS Settings
        ttk.Label(conn_frame, text="OBS Settings", font=("", 12, "bold")).grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=(10, 5))
        
        ttk.Label(conn_frame, text="OBS WebSocket Host:").grid(row=4, column=0, sticky=tk.W, pady=2)
        self.obs_host_var = tk.StringVar(value="localhost")
        ttk.Entry(conn_frame, textvariable=self.obs_host_var, width=30).grid(row=4, column=1, sticky=tk.W, pady=2)
        
        ttk.Label(conn_frame, text="Port:").grid(row=4, column=2, sticky=tk.W, pady=2)
        self.obs_port_var = tk.StringVar(value="4455")
        ttk.Entry(conn_frame, textvariable=self.obs_port_var, width=6).grid(row=4, column=3, sticky=tk.W, pady=2)
        
        ttk.Label(conn_frame, text="Password:").grid(row=5, column=0, sticky=tk.W, pady=2)
        self.obs_password_var = tk.StringVar()
        ttk.Entry(conn_frame, textvariable=self.obs_password_var, width=30, show="*").grid(row=5, column=1, sticky=tk.W, pady=2)
        
        # Scene Mapping tab
        mapping_frame = ttk.Frame(notebook, padding="10")
        notebook.add(mapping_frame, text="Scene Mapping")
        
        # Scene Mapping
        ttk.Label(mapping_frame, text="Field to Scene Mapping", font=("", 12, "bold")).grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 10))
        
        # Create a frame for the mapping entries
        mapping_entries_frame = ttk.Frame(mapping_frame)
        mapping_entries_frame.grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=5)
        
        # Create mapping entries for fields 1 and 2
        self.scene_mappings = {}
        for i in range(1, 3):
            ttk.Label(mapping_entries_frame, text=f"Field {i} Scene:").grid(row=i-1, column=0, sticky=tk.W, pady=2)
            scene_var = tk.StringVar(value=f"Field {i}")
            ttk.Entry(mapping_entries_frame, textvariable=scene_var, width=30).grid(row=i-1, column=1, sticky=tk.W, pady=2)
            self.scene_mappings[i] = scene_var
        
        # Add button
        add_frame = ttk.Frame(mapping_frame)
        add_frame.grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=10)
        
        self.new_field_var = tk.StringVar()
        self.new_scene_var = tk.StringVar()
        
        ttk.Label(add_frame, text="New Field Number:").grid(row=0, column=0, sticky=tk.W, pady=2)
        ttk.Entry(add_frame, textvariable=self.new_field_var, width=10).grid(row=0, column=1, sticky=tk.W, pady=2)
        
        ttk.Label(add_frame, text="Scene Name:").grid(row=0, column=2, sticky=tk.W, pady=2, padx=(10, 0))
        ttk.Entry(add_frame, textvariable=self.new_scene_var, width=20).grid(row=0, column=3, sticky=tk.W, pady=2)
        
        ttk.Button(add_frame, text="Add Mapping", command=self.add_mapping).grid(row=0, column=4, sticky=tk.W, pady=2, padx=(10, 0))
        
        # Control & Log Frame
        control_frame = ttk.Frame(main_frame, padding="10")
        control_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Control buttons
        button_frame = ttk.Frame(control_frame)
        button_frame.pack(fill=tk.X)
        
        self.start_button = ttk.Button(button_frame, text="Start Monitoring", command=self.start_monitoring)
        self.start_button.pack(side=tk.LEFT, padx=5)
        
        self.stop_button = ttk.Button(button_frame, text="Stop Monitoring", command=self.stop_monitoring, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(button_frame, text="Save Config", command=self.save_config).pack(side=tk.LEFT, padx=5)
        
        # Status indicator
        self.status_var = tk.StringVar(value="Status: Not Running 🔴")
        status_label = ttk.Label(button_frame, textvariable=self.status_var)
        status_label.pack(side=tk.RIGHT, padx=5)
        
        # Log area
        ttk.Label(control_frame, text="Log", font=("", 10, "bold")).pack(anchor=tk.W, pady=(10, 5))
        
        self.log_text = scrolledtext.ScrolledText(control_frame, height=10)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.config(state=tk.DISABLED)
        
    def add_mapping(self):
        try:
            field_num = int(self.new_field_var.get())
            scene_name = self.new_scene_var.get()
            
            if not scene_name:
                messagebox.showerror("Error", "Scene name cannot be empty")
                return
                
            # Create new entry if it doesn't exist
            if field_num not in self.scene_mappings:
                row_num = len(self.scene_mappings) + 1
                mapping_frame = self.root.nametowidget(self.root.winfo_children()[0].winfo_children()[0].winfo_children()[1])
                entries_frame = mapping_frame.winfo_children()[1]
                
                ttk.Label(entries_frame, text=f"Field {field_num} Scene:").grid(row=row_num, column=0, sticky=tk.W, pady=2)
                scene_var = tk.StringVar(value=scene_name)
                ttk.Entry(entries_frame, textvariable=scene_var, width=30).grid(row=row_num, column=1, sticky=tk.W, pady=2)
                self.scene_mappings[field_num] = scene_var
            else:
                # Update existing entry
                self.scene_mappings[field_num].set(scene_name)
                
            # Clear entry fields
            self.new_field_var.set("")
            self.new_scene_var.set("")
            
            self.log(f"Added mapping: Field {field_num} -> {scene_name}")
        except ValueError:
            messagebox.showerror("Error", "Field number must be a valid integer")
    
    def get_field_scene_mapping(self):
        mapping = {}
        for field_num, scene_var in self.scene_mappings.items():
            mapping[field_num] = scene_var.get()
        return mapping
    
    def log(self, message):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"{time.strftime('%H:%M:%S')} - {message}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
    
    def save_config(self):
        config = {
            "event_code": self.event_code_var.get(),
            "scoring_host": self.scoring_host_var.get(),
            "scoring_port": self.scoring_port_var.get(),
            "obs_host": self.obs_host_var.get(),
            "obs_port": self.obs_port_var.get(),
            "obs_password": self.obs_password_var.get(),
            "scene_mapping": {int(k): v.get() for k, v in self.scene_mappings.items()}
        }
        
        try:
            with open("ftc_obs_config.json", "w") as f:
                json.dump(config, f, indent=2)
            self.log("Configuration saved")
        except Exception as e:
            self.log(f"Error saving configuration: {e}")
            messagebox.showerror("Error", f"Failed to save configuration: {e}")
    
    def load_config(self):
        try:
            with open("ftc_obs_config.json", "r") as f:
                config = json.load(f)
                
            self.event_code_var.set(config.get("event_code", ""))
            self.scoring_host_var.set(config.get("scoring_host", "localhost"))
            self.scoring_port_var.set(config.get("scoring_port", "80"))
            self.obs_host_var.set(config.get("obs_host", "localhost"))
            self.obs_port_var.set(config.get("obs_port", "4455"))
            self.obs_password_var.set(config.get("obs_password", ""))
            
            # Load scene mappings
            scene_mapping = config.get("scene_mapping", {})
            for field_str, scene in scene_mapping.items():
                field = int(field_str)
                if field in self.scene_mappings:
                    self.scene_mappings[field].set(scene)
                else:
                    # Need to add this mapping to the UI
                    self.new_field_var.set(str(field))
                    self.new_scene_var.set(scene)
                    self.add_mapping()
                    
            self.log("Configuration loaded")
        except FileNotFoundError:
            self.log("No configuration file found, using defaults")
        except Exception as e:
            self.log(f"Error loading configuration: {e}")
    
    def start_monitoring(self):
        # Get configuration values
        event_code = self.event_code_var.get()
        scoring_host = self.scoring_host_var.get()
        scoring_port = self.scoring_port_var.get()
        obs_host = self.obs_host_var.get()
        obs_port = self.obs_port_var.get()
        obs_password = self.obs_password_var.get()
        
        if not event_code:
            messagebox.showerror("Error", "Event code is required")
            return
            
        try:
            obs_port = int(obs_port)
        except ValueError:
            messagebox.showerror("Error", "OBS port must be a valid number")
            return
            
        # Create switcher instance
        self.switcher = FTCFieldSwitcher(
            event_code=event_code,
            scoring_host=scoring_host,
            scoring_port=scoring_port,
            obs_host=obs_host,
            obs_port=obs_port,
            obs_password=obs_password
        )
        
        # Set logging callback
        self.switcher.set_log_callback(self.log)
        
        # Update field-scene mapping
        self.switcher.update_field_scene_mapping(self.get_field_scene_mapping())
        
        # Create new event loop
        self.async_loop = asyncio.new_event_loop()
        
        # Start monitoring in a separate thread
        self.thread = threading.Thread(target=self.run_async_monitoring, daemon=True)
        self.thread.start()
        
        # Update UI state
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.status_var.set("Status: Running 🟢")
        
        self.log("Monitoring started")
        
    def run_async_monitoring(self):
        """Run the async monitoring in a separate thread with its own event loop"""
        asyncio.set_event_loop(self.async_loop)
        self.monitor_task = self.async_loop.create_task(self.switcher.monitor_ftc_websocket())
        
        try:
            self.async_loop.run_until_complete(self.monitor_task)
        except asyncio.CancelledError:
            pass
        finally:
            # When the task is done, update UI back on the main thread
            self.root.after(0, self.update_ui_after_stop)
                
    def stop_monitoring(self):
        """Stop the monitoring process"""
        if self.switcher and self.switcher.running:
            self.log("Stopping monitoring...")
            
            # Cancel the monitoring task
            if self.monitor_task and not self.monitor_task.done():
                self.async_loop.call_soon_threadsafe(self.monitor_task.cancel)
                
            # Schedule task to gracefully shutdown
            shutdown_task = asyncio.run_coroutine_threadsafe(self.switcher.shutdown(), self.async_loop)
            
            # Wait for shutdown to complete (with timeout)
            try:
                shutdown_task.result(timeout=5)
            except concurrent.futures.TimeoutError:
                self.log("Shutdown timed out, forcing exit")
            except Exception as e:
                self.log(f"Error during shutdown: {e}")
                
            # Set switcher as not running
            self.switcher.running = False
    
    def update_ui_after_stop(self):
        """Update UI elements after monitoring has stopped"""
        self.start_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        self.status_var.set("Status: Not Running 🔴")
        self.log("Monitoring stopped")
    
    def on_closing(self):
        """Handle window close event"""
        if self.switcher and self.switcher.running:
            self.stop_monitoring()
            # Give it a moment to clean up
            time.sleep(1)
        self.root.destroy()

if __name__ == "__main__":
    # Fix for high DPI displays
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except:
        pass
    
    # Import for run_coroutine_threadsafe
    import concurrent.futures
    
    root = tk.Tk()
    app = FTCSwitcherGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()
