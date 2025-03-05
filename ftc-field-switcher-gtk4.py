import json
import time
import asyncio
import signal
import threading
import gi
import concurrent.futures
import websockets
import obswebsocket
from obswebsocket import requests as obsrequests

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gdk, Gio, GObject

class FTCFieldSwitcher:
    def __init__(self, event_code, scoring_host="localhost", obs_host="localhost", obs_port=4444, obs_password=""):
        """
        Initialize the FTC Field Switcher with WebSocket connection details and OBS parameters
        
        Args:
            event_code: FTC event code for the WebSocket connection
            scoring_host: Host address for the FTC scoring system (default: localhost)
            obs_host: OBS WebSocket host (default: localhost)
            obs_port: OBS WebSocket port (default: 4444)
            obs_password: OBS WebSocket password (default: empty string)
        """
        self.scoring_host = scoring_host
        self.ftc_ws_url = f"ws://{scoring_host}:8080/stream/display/command/?code={event_code}"
        self.event_code = event_code
        self.obs_host = obs_host
        self.obs_port = obs_port
        self.obs_password = obs_password
        self.obs_ws = None
        self.current_field = None
        self.field_scene_mapping = {
            1: "Field 1 Scene",
            2: "Field 2 Scene",
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
        """Establish connection to OBS WebSocket server with timeout"""
        self.log("Connecting to OBS WebSocket...")
        self.obs_ws = obswebsocket.obsws(self.obs_host, self.obs_port, self.obs_password)
        try:
            # Use a thread with timeout to prevent blocking
            connect_thread = threading.Thread(target=self._connect_thread)
            connect_thread.daemon = True
            connect_thread.start()
            connect_thread.join(timeout=5)  # 5 second timeout
            
            # Check if connection was successful
            if connect_thread.is_alive() or not self.obs_ws.ws.connected:
                self.log("Connection to OBS failed.  Are the host, port, and password correct?")
                # Clean up the thread and connection
                try:
                    if hasattr(self.obs_ws, 'ws'):
                        self.obs_ws.ws.close()
                except:
                    pass
                return False
                
            self.log("Connected to OBS WebSocket server")
            return True
        except Exception as e:
            self.log(f"Error connecting to OBS: {e}")
            return False

    def _connect_thread(self):
        """Helper method to perform the actual connection in a separate thread"""
        try:
            self.obs_ws.connect()
        except Exception:
            # Exceptions will be handled in the parent method
            pass
    
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
    
    async def monitor_ftc_websocket(self, status_label):
        """
        Connect to FTC system WebSocket and monitor for SHOW_MATCH messages
        """
        if not self.connect_to_obs():
            self.log("Failed to connect to OBS. Exiting.")
            return
        
        self.log(f"Connecting to FTC WebSocket: {self.ftc_ws_url}")
        self.log(f"Current field-scene mapping: \n{json.dumps(self.field_scene_mapping, indent=2)}")
        
        self.running = True
        try:
            async with websockets.connect(self.ftc_ws_url) as websocket:
                self.ftc_websocket = websocket
                self.log(f"Connected to FTC scoring system WebSocket at {self.scoring_host}")
                self.log(f"Monitoring started")
                status_label.set_text("Status: Connected 🟢")
                
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

class SceneMappingRow(Gtk.Box):
    def __init__(self, field_num, scene_name="", remove_callback=None, change_callback=None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.set_margin_top(5)
        self.set_margin_bottom(5)
        
        self.field_num = field_num
        self.change_callback = change_callback
        
        # Field label
        field_label = Gtk.Label(label=f"Field {field_num} Scene:")
        field_label.set_halign(Gtk.Align.START)
        field_label.set_width_chars(15)
        self.append(field_label)
        
        # Scene name entry
        self.scene_entry = Gtk.Entry()
        self.scene_entry.set_text(scene_name)
        self.scene_entry.set_hexpand(True)
        # Connect to the changed signal
        if change_callback:
            self.scene_entry.connect("changed", lambda entry: change_callback())
        self.append(self.scene_entry)
        
        # Only add remove button for dynamically added mappings (field > 2)
        if field_num > 2 and remove_callback:
            remove_button = Gtk.Button()
            remove_button.set_icon_name("user-trash-symbolic")
            remove_button.connect("clicked", lambda btn: remove_callback(self))
            self.append(remove_button)
    
    def get_value(self):
        return self.scene_entry.get_text()
    
    def set_value(self, scene_name):
        self.scene_entry.set_text(scene_name)
        # Trigger change callback if set
        if self.change_callback and scene_name != self.scene_entry.get_text():
            self.change_callback()

class FTCSwitcherWindow(Adw.ApplicationWindow):
    def __init__(self, application):
        super().__init__(application=application)
        self.set_title("FTC OBS Scene Switcher")
        self.set_default_size(700, 600)
        
        self.switcher = None
        self.async_loop = None
        self.monitor_task = None
        self.thread = None
        self.running = False
        
        self.setup_ui()
        self.load_config()
        
    def setup_ui(self):
        # Main structure
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_content(self.main_box)
        
        # Create header bar with menu
        self.create_header_bar()
        
        # Create a GTK4 Notebook instead of Adw.TabView
        self.notebook = Gtk.Notebook()
        self.notebook.set_vexpand(True)
        self.notebook.set_scrollable(True)
        self.notebook.set_show_border(True)
        
        # Add notebook to main box
        self.main_box.append(self.notebook)
        
        # Create connection settings page
        self.create_connection_page()
        
        # Create scene mapping page
        self.create_scene_mapping_page()
        
        # Create bottom controls
        self.create_control_section()
        
        # Create log section
        self.create_log_section()
        
    def create_header_bar(self):
        header = Adw.HeaderBar()
        box = Gtk.Box.new(1, 0)
        self.main_box.append(header)
        
        # Menu button with only about action
        menu = Gio.Menu()
        menu.append("About", "app.about")
        
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu_button.set_menu_model(menu)
        header.pack_end(menu_button)
        
        # Setup about action
        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self.on_about)
        self.get_application().add_action(about_action)
        
    def create_connection_page(self):
        page_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        page_box.set_margin_start(20)
        page_box.set_margin_end(20)
        page_box.set_margin_top(20)
        page_box.set_margin_bottom(20)
        
        # FTC Settings
        ftc_group = Adw.PreferencesGroup(title="FTC Settings")
        page_box.append(ftc_group)
        
        # Event Code
        event_row = Adw.EntryRow(title="Event Code")
        self.event_code_entry = event_row
        event_row.connect("changed", self.on_config_changed)
        ftc_group.add(event_row)
        
        # Scoring System Host
        scoring_host_row = Adw.EntryRow(title="Scoring System Host")
        scoring_host_row.set_text("localhost")
        self.scoring_host_entry = scoring_host_row
        scoring_host_row.connect("changed", self.on_config_changed)
        ftc_group.add(scoring_host_row)
        
        # OBS Settings
        obs_group = Adw.PreferencesGroup(title="OBS Settings")
        page_box.append(obs_group)
        
        # Host
        host_row = Adw.EntryRow(title="Host")
        host_row.set_text("localhost")
        self.obs_host_entry = host_row
        host_row.connect("changed", self.on_config_changed)
        obs_group.add(host_row)
        
        # Port
        port_row = Adw.EntryRow(title="Port")
        port_row.set_text("4444")
        self.obs_port_entry = port_row
        port_row.connect("changed", self.on_config_changed)
        obs_group.add(port_row)
        
        # Password
        password_row = Adw.PasswordEntryRow(title="Password")
        self.obs_password_entry = password_row
        password_row.connect("changed", self.on_config_changed)
        obs_group.add(password_row)
        
        # Create tab label with icon and text
        tab_label = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        icon = Gtk.Image.new_from_icon_name("network-server-symbolic")
        label = Gtk.Label(label="Connection Settings")
        tab_label.append(icon)
        tab_label.append(label)
        
        # Add page to notebook with the custom label
        self.notebook.append_page(page_box, tab_label)
        
    def create_scene_mapping_page(self):
        page_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        page_box.set_margin_start(20)
        page_box.set_margin_end(20)
        page_box.set_margin_top(20)
        page_box.set_margin_bottom(20)
        
        # Title
        mapping_group = Adw.PreferencesGroup(title="Field to Scene Mapping")
        page_box.append(mapping_group)
        
        # Scrolled window for mapping entries
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        mapping_group.add(scroll)
        
        # Box for mapping entries
        self.mapping_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        scroll.set_child(self.mapping_box)
        
        # Create initial field mappings
        self.scene_mappings = {}
        for i in range(1, 3):
            row = SceneMappingRow(i, f"Field {i} Scene", None, self.on_config_changed)
            self.mapping_box.append(row)
            self.scene_mappings[i] = row
        
        # Add mapping section
        add_group = Adw.PreferencesGroup(title="Add New Mapping")
        page_box.append(add_group)
        
        add_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        add_box.set_margin_top(10)
        add_box.set_margin_bottom(10)
        add_group.add(add_box)
        
        # Field number entry
        field_label = Gtk.Label(label="Field Number:")
        field_label.set_halign(Gtk.Align.START)
        add_box.append(field_label)
        
        self.new_field_entry = Gtk.SpinButton.new_with_range(1, 20, 1)
        add_box.append(self.new_field_entry)
        
        # Scene name entry
        scene_label = Gtk.Label(label="Scene Name:")
        scene_label.set_halign(Gtk.Align.START)
        scene_label.set_margin_start(10)
        add_box.append(scene_label)
        
        self.new_scene_entry = Gtk.Entry()
        self.new_scene_entry.set_hexpand(True)
        add_box.append(self.new_scene_entry)
        
        # Add button
        add_button = Gtk.Button(label="Add Mapping")
        add_button.set_margin_start(10)
        add_button.connect("clicked", self.on_add_mapping)
        add_box.append(add_button)
        
        # Create tab label with icon and text
        tab_label = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        icon = Gtk.Image.new_from_icon_name("view-grid-symbolic")
        label = Gtk.Label(label="Scene Mapping")
        tab_label.append(icon)
        tab_label.append(label)
        
        # Add page to notebook with the custom label
        self.notebook.append_page(page_box, tab_label)
    
    def remove_mapping(self, row):
        field_num = row.field_num
        if field_num in self.scene_mappings:
            del self.scene_mappings[field_num]
            self.mapping_box.remove(row)
            self.log(f"Removed mapping for Field {field_num}")
            # Auto-save after removal
            self.save_config()
    
    def create_control_section(self):
        control_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        control_box.set_margin_start(20)
        control_box.set_margin_end(20)
        control_box.set_margin_top(10)
        control_box.set_margin_bottom(10)
        self.main_box.append(control_box)
        
        # Start button
        self.start_button = Gtk.Button(label="Start Monitoring")
        self.start_button.connect("clicked", self.on_start_monitoring)
        control_box.append(self.start_button)
        
        # Stop button
        self.stop_button = Gtk.Button(label="Stop Monitoring")
        self.stop_button.connect("clicked", self.on_stop_monitoring)
        self.stop_button.set_sensitive(False)
        control_box.append(self.stop_button)
        
        # Status label
        self.status_label = Gtk.Label(label="Status: Not Running 🔴")
        self.status_label.set_hexpand(True)
        self.status_label.set_halign(Gtk.Align.END)
        
        control_box.append(self.status_label)
    
    def create_log_section(self):
        log_group = Adw.PreferencesGroup(title="Log")
        log_group.set_margin_start(20)
        log_group.set_margin_end(20)
        log_group.set_margin_bottom(20)
        self.main_box.append(log_group)
        
        # Create text view inside a scrolled window
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_min_content_height(150)
        log_group.add(scroll)
        
        self.log_buffer = Gtk.TextBuffer()
        self.log_view = Gtk.TextView.new_with_buffer(self.log_buffer)
        self.log_view.set_editable(False)
        self.log_view.set_cursor_visible(False)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        scroll.set_child(self.log_view)
        
        # Add tag for timestamps
        self.log_buffer.create_tag("timestamp", foreground="#888888")
    
    def on_add_mapping(self, button):
        try:
            field_num = int(self.new_field_entry.get_value())
            scene_name = self.new_scene_entry.get_text()
            
            if not scene_name:
                self.show_error_dialog("Scene name cannot be empty")
                return
                
            # Check if mapping already exists
            if field_num in self.scene_mappings:
                # Update existing mapping
                self.scene_mappings[field_num].set_value(scene_name)
                self.log(f"Updated mapping: Field {field_num} -> {scene_name}")
            else:
                # Create new mapping
                row = SceneMappingRow(field_num, scene_name, self.remove_mapping, self.on_config_changed)
                self.mapping_box.append(row)
                self.scene_mappings[field_num] = row
                self.log(f"Added mapping: Field {field_num} -> {scene_name}")
                
            # Clear the inputs
            self.new_scene_entry.set_text("")
            
            # Auto-save after adding/updating mapping
            self.save_config()
        except ValueError as e:
            self.show_error_dialog(f"Invalid field number: {e}")
            
    def get_field_scene_mapping(self):
        mapping = {}
        for field_num, row in self.scene_mappings.items():
            mapping[field_num] = row.get_value()
        return mapping
        
    def on_config_changed(self, *args):
        # Cancel any pending save operations
        if hasattr(self, '_save_timeout_id') and self._save_timeout_id > 0:
            GLib.source_remove(self._save_timeout_id)
        
        # Schedule a new save operation with delay
        self._save_timeout_id = GLib.timeout_add(500, self._do_save_config)
    
    def log(self, message):
        # Create timestamp
        timestamp = time.strftime("%H:%M:%S")
        
        def _update_log():
            # Get end iterator
            end_iter = self.log_buffer.get_end_iter()
            
            # Insert timestamp with tag
            self.log_buffer.insert_with_tags_by_name(end_iter, f"{timestamp} - ", "timestamp")
            
            # Insert message
            self.log_buffer.insert(end_iter, f"{message}\n")
            
            # Scroll to end
            mark = self.log_buffer.create_mark(None, self.log_buffer.get_end_iter(), False)
            self.log_view.scroll_to_mark(mark, 0.0, False, 0.0, 0.0)
            
            return False
        
        # Schedule update on main thread
        GLib.idle_add(_update_log)
    
    def on_save_config(self, action, param):
        self.save_config()
    
    def on_about(self, widget, _):
        about = Adw.AboutWindow(transient_for=self.get_application().props.active_window,
            application_name="FTC OBS Scene Switcher",
            application_icon="video-display-symbolic",
            developer_name="QwertyChouskie, Claude 3.7 Sonnet",
            version="1.0",
            copyright="© 2025 QwertyChouskie",
            #website="https://example.com/ftc-tools",
            #issue_url="https://example.com/ftc-tools/issues",
            license_type=Gtk.License.MIT_X11
        )
        about.present()

    def _do_save_config(self):
        self.save_config()
        self._save_timeout_id = 0  # Reset the timeout ID
        return False  # Don't repeat

    def save_config(self):
        config = {
            "event_code": self.event_code_entry.get_text(),
            "scoring_host": self.scoring_host_entry.get_text(),
            "obs_host": self.obs_host_entry.get_text(),
            "obs_port": self.obs_port_entry.get_text(),
            "obs_password": self.obs_password_entry.get_text(),
            "scene_mapping": {str(k): v.get_value() for k, v in self.scene_mappings.items()}
        }
        
        try:
            with open("ftc_obs_config.json", "w") as f:
                json.dump(config, f, indent=2)
            self.log("Configuration auto-saved")
        except Exception as e:
            self.log(f"Error saving configuration: {e}")
            self.show_error_dialog(f"Failed to save configuration: {e}")
    
    def load_config(self):
        try:
            with open("ftc_obs_config.json", "r") as f:
                config = json.load(f)
                
            self.event_code_entry.set_text(config.get("event_code", ""))
            self.scoring_host_entry.set_text(config.get("scoring_host", "localhost"))
            self.obs_host_entry.set_text(config.get("obs_host", "localhost"))
            self.obs_port_entry.set_text(config.get("obs_port", "4444"))
            self.obs_password_entry.set_text(config.get("obs_password", ""))
            
            # Load scene mappings
            scene_mapping = config.get("scene_mapping", {})
            for field_str, scene in scene_mapping.items():
                try:
                    field = int(field_str)
                    if field in self.scene_mappings:
                        self.scene_mappings[field].set_value(scene)
                    else:
                        # Need to add this mapping to the UI
                        row = SceneMappingRow(field, scene, self.remove_mapping)
                        self.mapping_box.append(row)
                        self.scene_mappings[field] = row
                except ValueError:
                    self.log(f"Invalid field number in config: {field_str}")
                    
            self.log("Configuration loaded")
        except FileNotFoundError:
            self.log("No configuration file found, using defaults")
        except Exception as e:
            self.log(f"Error loading configuration: {e}")
    
    def on_start_monitoring(self, button):
        # Get configuration values
        event_code = self.event_code_entry.get_text()
        scoring_host = self.scoring_host_entry.get_text()
        obs_host = self.obs_host_entry.get_text()
        obs_port = self.obs_port_entry.get_text()
        obs_password = self.obs_password_entry.get_text()
        
        if not event_code:
            self.show_error_dialog("Event code is required")
            return
            
        try:
            obs_port = int(obs_port)
        except ValueError:
            self.show_error_dialog("OBS port must be a valid number")
            return
            
        # Create switcher instance
        self.switcher = FTCFieldSwitcher(
            event_code=event_code,
            scoring_host=scoring_host,
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
        self.running = True
        self.start_button.set_sensitive(False)
        self.stop_button.set_sensitive(True)
        self.status_label.set_text("Status: Connecting 🟡")
        
        self.log("Monitoring starting...")
        
    def run_async_monitoring(self):
        """Run the async monitoring in a separate thread with its own event loop"""
        asyncio.set_event_loop(self.async_loop)
        self.monitor_task = self.async_loop.create_task(self.switcher.monitor_ftc_websocket(self.status_label))
        
        try:
            self.async_loop.run_until_complete(self.monitor_task)
        except asyncio.CancelledError:
            pass
        finally:
            # When the task is done, update UI back on the main thread
            GLib.idle_add(self.update_ui_after_stop)
                
    def on_stop_monitoring(self, button):
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
        self.running = False
        self.start_button.set_sensitive(True)
        self.stop_button.set_sensitive(False)
        self.status_label.set_text("Status: Not Running 🔴")
        
        self.log("Monitoring stopped")
        return False  # Return False to not be called again
    
    def show_error_dialog(self, message):
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Error",
            body=message
        )
        dialog.add_response("ok", "OK")
        dialog.set_default_response("ok")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.present()
    
    def on_close_request(self):
        if self.switcher and self.switcher.running:
            self.on_stop_monitoring(None)
            # Give it a moment to clean up
            time.sleep(1)
        return False

class FTCSwitcherApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id="org.ftc.obs.switcher",
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        
    def do_activate(self):
        window = FTCSwitcherWindow(application=self)
        window.present()
        window.connect("close-request", lambda win: win.on_close_request())

if __name__ == "__main__":
    app = FTCSwitcherApplication()
    app.run(None)
