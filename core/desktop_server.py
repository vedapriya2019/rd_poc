#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import http.server
import socketserver
import json
import base64
import subprocess
import os
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse

PORT = 8080

class DesktopHandler(http.server.SimpleHTTPRequestHandler):
    
    def do_GET(self):
        parsed = urlparse(self.path)
        
        if parsed.path == '/' or parsed.path == '/index.html':
            self.serve_main_page()
        elif parsed.path == '/desktop':
            self.serve_desktop_page()
        elif parsed.path == '/api/screenshot':
            self.serve_screenshot()
        elif parsed.path == '/api/status':
            self.serve_status()
        else:
            self.send_error(404)
    
    def do_POST(self):
        parsed = urlparse(self.path)
        
        if parsed.path == '/api/mouse':
            self.handle_mouse()
        elif parsed.path == '/api/keyboard':
            self.handle_keyboard()
        else:
            self.send_error(404)
    
    def serve_main_page(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        html = """
<!DOCTYPE html>
<html>
<head>
    <title>DWService Agent - Local Desktop</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        h1 { color: #333; }
        .status { padding: 15px; margin: 20px 0; border-radius: 4px; background: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
        .apps { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 15px; margin-top: 20px; }
        .app-card { background: #f8f9fa; padding: 20px; border-radius: 4px; border: 1px solid #dee2e6; cursor: pointer; transition: all 0.3s; }
        .app-card:hover { background: #e9ecef; transform: translateY(-2px); box-shadow: 0 4px 8px rgba(0,0,0,0.1); }
        .app-card h3 { margin-top: 0; color: #495057; }
        button { background: #007bff; color: white; border: none; padding: 12px 24px; border-radius: 4px; cursor: pointer; font-size: 16px; }
        button:hover { background: #0056b3; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🖥️ DWService Agent - Local Mode</h1>
        
        <div class="status">
            <strong>✅ Status:</strong> Agent Running (Local Mode - No Cloud)
        </div>
        
        <h2>Available Applications</h2>
        <div class="apps">
            <div class="app-card" onclick="location.href='/desktop'">
                <h3>🖥️ Remote Desktop</h3>
                <p>View and control this computer's desktop</p>
                <p><strong>Status:</strong> <span style="color: green;">● Available</span></p>
            </div>
            <div class="app-card" onclick="alert('File browser coming soon')">
                <h3>📁 File System</h3>
                <p>Browse and manage files</p>
                <p><strong>Status:</strong> <span style="color: orange;">● Coming Soon</span></p>
            </div>
            <div class="app-card" onclick="alert('Terminal coming soon')">
                <h3>💻 Terminal</h3>
                <p>Command line access</p>
                <p><strong>Status:</strong> <span style="color: orange;">● Coming Soon</span></p>
            </div>
            <div class="app-card" onclick="alert('System monitor coming soon')">
                <h3>📊 System Monitor</h3>
                <p>CPU, Memory, Disk usage</p>
                <p><strong>Status:</strong> <span style="color: orange;">● Coming Soon</span></p>
            </div>
        </div>
    </div>
</body>
</html>
        """
        self.wfile.write(html.encode())
    
    def serve_desktop_page(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        html = """
<!DOCTYPE html>
<html>
<head>
    <title>Remote Desktop - DWService</title>
    <style>
        body { margin: 0; padding: 0; background: #2c3e50; font-family: Arial, sans-serif; overflow: hidden; }
        .toolbar { background: #34495e; padding: 10px; color: white; display: flex; align-items: center; gap: 15px; }
        .toolbar button { background: #3498db; color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; }
        .toolbar button:hover { background: #2980b9; }
        .toolbar .status { margin-left: auto; }
        .desktop-container { position: relative; width: 100%; height: calc(100vh - 50px); overflow: auto; background: #1a1a1a; display: flex; align-items: center; justify-content: center; }
        #screen { max-width: 100%; max-height: 100%; border: 2px solid #34495e; cursor: crosshair; }
        .loading { color: white; font-size: 18px; }
        .controls { position: fixed; bottom: 20px; right: 20px; background: rgba(52, 73, 94, 0.9); padding: 15px; border-radius: 8px; color: white; }
        .controls label { display: block; margin: 5px 0; }
    </style>
</head>
<body>
    <div class="toolbar">
        <button onclick="location.href='/'">← Back</button>
        <span style="font-size: 18px; font-weight: bold;">🖥️ Remote Desktop</span>
        <button onclick="toggleFullscreen()">⛶ Fullscreen</button>
        <button onclick="refreshScreen()">🔄 Refresh</button>
        <span class="status">FPS: <span id="fps">0</span> | Quality: <span id="quality">High</span></span>
    </div>
    
    <div class="desktop-container">
        <img id="screen" src="" alt="Loading desktop...">
        <div class="loading" id="loading">Loading desktop...</div>
    </div>
    
    <div class="controls">
        <label><input type="checkbox" id="autoRefresh" checked> Auto-refresh</label>
        <label>Refresh Rate: <select id="refreshRate">
            <option value="100">10 FPS</option>
            <option value="200">5 FPS</option>
            <option value="500" selected>2 FPS</option>
            <option value="1000">1 FPS</option>
        </select></label>
        <label>Quality: <select id="qualitySelect">
            <option value="high" selected>High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
        </select></label>
    </div>
    
    <script>
        const screen = document.getElementById('screen');
        const loading = document.getElementById('loading');
        const fpsDisplay = document.getElementById('fps');
        let refreshInterval = null;
        let frameCount = 0;
        let lastFpsUpdate = Date.now();
        
        function refreshScreen() {
            const timestamp = new Date().getTime();
            fetch('/api/screenshot?t=' + timestamp)
                .then(response => response.json())
                .then(data => {
                    if (data.image) {
                        screen.src = 'data:image/jpeg;base64,' + data.image;
                        loading.style.display = 'none';
                        screen.style.display = 'block';
                        
                        frameCount++;
                        const now = Date.now();
                        if (now - lastFpsUpdate >= 1000) {
                            fpsDisplay.textContent = frameCount;
                            frameCount = 0;
                            lastFpsUpdate = now;
                        }
                    }
                })
                .catch(err => {
                    console.error('Screenshot error:', err);
                    loading.textContent = 'Error loading desktop. Retrying...';
                });
        }
        
        function startAutoRefresh() {
            const rate = parseInt(document.getElementById('refreshRate').value);
            if (refreshInterval) clearInterval(refreshInterval);
            refreshInterval = setInterval(refreshScreen, rate);
        }
        
        function toggleFullscreen() {
            if (!document.fullscreenElement) {
                document.documentElement.requestFullscreen();
            } else {
                document.exitFullscreen();
            }
        }
        
        document.getElementById('autoRefresh').addEventListener('change', (e) => {
            if (e.target.checked) {
                startAutoRefresh();
            } else {
                if (refreshInterval) clearInterval(refreshInterval);
            }
        });
        
        document.getElementById('refreshRate').addEventListener('change', () => {
            if (document.getElementById('autoRefresh').checked) {
                startAutoRefresh();
            }
        });
        
        document.getElementById('qualitySelect').addEventListener('change', (e) => {
            document.getElementById('quality').textContent = e.target.value.charAt(0).toUpperCase() + e.target.value.slice(1);
        });
        
        // Mouse and keyboard events
        screen.addEventListener('click', (e) => {
            const rect = screen.getBoundingClientRect();
            const x = (e.clientX - rect.left) / rect.width;
            const y = (e.clientY - rect.top) / rect.height;
            
            fetch('/api/mouse', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({action: 'click', x: x, y: y, button: e.button})
            });
        });
        
        document.addEventListener('keydown', (e) => {
            if (e.target.tagName !== 'INPUT' && e.target.tagName !== 'SELECT') {
                fetch('/api/keyboard', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({action: 'keydown', key: e.key, code: e.code})
                });
                e.preventDefault();
            }
        });
        
        // Start
        refreshScreen();
        startAutoRefresh();
    </script>
</body>
</html>
        """
        self.wfile.write(html.encode())
    
    def serve_screenshot(self):
        try:
            # Capture screenshot using macOS screencapture
            screenshot_path = '/tmp/dwagent_screenshot.jpg'
            subprocess.run(['screencapture', '-x', '-t', 'jpg', screenshot_path], 
                         check=True, capture_output=True)
            
            # Read and encode
            with open(screenshot_path, 'rb') as f:
                img_data = f.read()
            
            img_base64 = base64.b64encode(img_data).decode('utf-8')
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            
            response = {'image': img_base64, 'timestamp': time.time()}
            self.wfile.write(json.dumps(response).encode())
            
            # Cleanup
            try:
                os.remove(screenshot_path)
            except:
                pass
                
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())
    
    def handle_mouse(self):
        try:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))
            
            # Get screen resolution
            result = subprocess.run(['system_profiler', 'SPDisplaysDataType'], 
                                  capture_output=True, text=True)
            # Parse resolution (simplified)
            
            if data['action'] == 'click':
                x = int(data['x'] * 1920)  # Adjust based on actual resolution
                y = int(data['y'] * 1080)
                
                # Use cliclick or osascript for mouse control
                applescript = f'''
                tell application "System Events"
                    click at {{{x}, {y}}}
                end tell
                '''
                subprocess.run(['osascript', '-e', applescript], capture_output=True)
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'ok'}).encode())
            
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())
    
    def handle_keyboard(self):
        try:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))
            
            if data['action'] == 'keydown':
                key = data['key']
                # Use osascript to send keystrokes
                applescript = f'''
                tell application "System Events"
                    keystroke "{key}"
                end tell
                '''
                subprocess.run(['osascript', '-e', applescript], capture_output=True)
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'ok'}).encode())
            
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())
    
    def serve_status(self):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        status = {
            "running": True,
            "desktop_available": True,
            "os": "macOS"
        }
        self.wfile.write(json.dumps(status).encode())
    
    def log_message(self, format, *args):
        # Suppress default logging
        pass

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
    print("=" * 60)
    print("🖥️  DWService Agent - Local Remote Desktop")
    print("=" * 60)
    print(f"📡 Server: http://0.0.0.0:{PORT}")
    print(f"🔗 Access: http://192.168.88.9:{PORT}")
    print(f"🖱️  Features: Screen viewing + Mouse/Keyboard control")
    print("=" * 60)
    print("⚠️  IMPORTANT: Grant screen recording permissions:")
    print("   System Settings → Privacy & Security → Screen Recording")
    print("   → Enable for Terminal/Python")
    print("=" * 60)
    print("⏹️  Press Ctrl+C to stop\n")
    
    with socketserver.TCPServer(("0.0.0.0", PORT), DesktopHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n👋 Server stopped")
