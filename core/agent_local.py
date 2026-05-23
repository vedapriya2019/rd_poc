#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Local DWService Agent - No Remote Server Required
"""

import os
import sys
import threading
import time
import json
import utils
import listener
import applications
import native

class LocalAgent:
    def __init__(self):
        self._logger = utils.Logger({})
        self._brun = True
        self._listener_ipc = None
        self._listener_http = None
        self._agent_status = 1  # ONLINE
        self._agent_name = "Local Agent"
        self._sessions = {}
        self._sessions_semaphore = threading.Condition()
        self._apps = {}
        self._libs = {}
        self._libs_apps_semaphore = threading.Condition()
        self._osmodule = native.get_instance()
        self._config = {
            "enabled": True,
            "listener_ipc_enable": True,
            "listener_http_enable": False,
            "listener_http_port": 8950,
            "unattended_access": True,
            "recovery_session": True,
            "debug_mode": True
        }
        
    def write_info(self, msg):
        self._logger.write(utils.LOGGER_INFO, msg)
    
    def write_err(self, msg):
        self._logger.write(utils.LOGGER_ERROR, msg)
    
    def write_debug(self, msg):
        self._logger.write(utils.LOGGER_DEBUG, msg)
    
    def write_except(self, e, tx=""):
        self._logger.write(utils.LOGGER_ERROR, utils.get_exception_string(e, tx))
    
    def get_osmodule(self):
        return self._osmodule
    
    def get_name(self):
        return self._agent_name
    
    def get_status(self):
        return self._agent_status
    
    def get_session_count(self):
        self._sessions_semaphore.acquire()
        try:
            return len(self._sessions)
        finally:
            self._sessions_semaphore.release()
    
    def get_active_sessions_status(self, ckint=30):
        return []
    
    def get_config(self, key, default=None):
        if key in self._config:
            return self._config[key]
        return default
    
    def get_config_str(self, key):
        if key == "enabled":
            return "True"
        elif key == "key":
            return "LOCAL_AGENT"
        elif key == "proxy_type":
            return "SYSTEM"
        elif key == "proxy_host":
            return ""
        elif key == "proxy_port":
            return ""
        elif key == "proxy_user":
            return ""
        elif key == "monitor_desktop_notification":
            return "visible"
        elif key == "monitor_tray_icon":
            return "True"
        elif key == "recovery_session":
            return "True"
        elif key == "unattended_access":
            return "True"
        return ""
    
    def set_config_str(self, key, val):
        self._config[key] = val
    
    def check_config_auth(self, usr, pwd):
        # Default admin with no password for local access
        return usr == "admin" and pwd == utils.hash_password("")
    
    def set_config_password(self, pwd):
        pass
    
    def set_session_password(self, pwd):
        pass
    
    def accept_session(self, sid):
        pass
    
    def reject_session(self, sid):
        pass
    
    def get_supported_applications(self):
        return applications.get_supported(self)
    
    def get_app(self, name):
        return None
    
    def is_run(self):
        if utils.path_exists("dwagent.stop"):
            return False
        return self._brun
    
    def destroy(self):
        self._brun = False
    
    def start(self):
        self.write_info("=" * 60)
        self.write_info("LOCAL DWSERVICE AGENT - NO REMOTE SERVER")
        self.write_info("=" * 60)
        self.write_info("Starting local agent...")
        
        # Start IPC listener for local control
        try:
            self._listener_ipc = listener.IPCServer(self)
            self._listener_ipc.start()
            self.write_info("IPC Server started - Local control enabled")
        except Exception as e:
            self.write_except(e, "IPC Server failed: ")
        
        # Start HTTP listener for local web interface
        if self.get_config('listener_http_enable', False):
            try:
                port = self.get_config('listener_http_port', 8950)
                self._listener_http = listener.HttpServer(port, self)
                self._listener_http.start()
                self.write_info(f"HTTP Server started on port {port}")
                self.write_info(f"Access at: http://127.0.0.1:{port}")
            except Exception as e:
                self.write_except(e, "HTTP Server failed: ")
        
        self.write_info("=" * 60)
        self.write_info("Agent Status: ONLINE (Local Mode)")
        self.write_info("Agent Name: " + self._agent_name)
        self.write_info("Supported Apps: " + ", ".join(self.get_supported_applications()))
        self.write_info("=" * 60)
        self.write_info("")
        self.write_info("Agent is running in LOCAL MODE")
        self.write_info("No remote server connection required")
        self.write_info("Press Ctrl+C to stop or create 'dwagent.stop' file")
        self.write_info("")
        
        # Main loop
        try:
            while self.is_run():
                time.sleep(1)
        except KeyboardInterrupt:
            self.write_info("Keyboard interrupt received")
            self.destroy()
        
        # Cleanup
        if self._listener_http is not None:
            try:
                self._listener_http.close()
            except Exception as e:
                self.write_except(e, "HTTP Server cleanup: ")
        
        if self._listener_ipc is not None:
            try:
                self._listener_ipc.close()
            except Exception as e:
                self.write_except(e, "IPC Server cleanup: ")
        
        self.write_info("Local agent stopped")

def main(args):
    agent = LocalAgent()
    agent.start()
    sys.exit(0)

if __name__ == "__main__":
    main(sys.argv)
