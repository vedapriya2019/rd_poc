#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import http.server
import socketserver
import json
import os

PORT = 8080

class AgentWebHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            html = """
<!DOCTYPE html>
<html>
<head>
    <title>DWService Agent - Remote Desktop</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: Arial, sans-serif; background: #1a1a1a; color: #fff; }
        .header { background: #2d2d2d; padding: 15px 30px; border-bottom: 2px solid #007bff; }
        .header h1 { font-size: 24px; color: #007bff; }
        .container { padding: 20px; }
        .status { padding: 10px 20px; margin: 10px 0; border-radius: 4px; display: inline-block; }
        .status.connected { background: #28a745; }
        .status.disconnected { background: #dc3545; }
        .desktop-container { 
            background: #000; 
            margin: 20px auto; 
            border: 2px solid #444; 
            border-radius: 8px;
            overflow: hidden;
            position: relative;
            max-width: 1920px;
        }
        #desktop-canvas { 
            width: 100%; 
            height: auto; 
            display: block;
            cursor: crosshair;
        }
        .controls { 
            background: #2d2d2d; 
            padding: 15px; 
            border-radius: 4px; 
            margin: 20px 0;
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
        }
        button { 
            background: #007bff; 
            color: white; 
            border: none; 
            padding: 10px 20px; 
            border-radius: 4px; 
            cursor: pointer;
            font-size: 14px;
        }
        button:hover { background: #0056b3; }
        button:disabled { background: #555; cursor: not-allowed; }
        .info { 
            background: #2d2d2d; 
            padding: 15px; 
            border-radius: 4px; 
            margin: 10px 0;
        }
        .stats { 
            display: grid; 
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); 
            gap: 10px;
            margin-top: 10px;
        }
        .stat-item { 
            background: #1a1a1a; 
            padding: 10px; 
            border-radius: 4px;
            text-align: center;
        }
        .stat-value { font-size: 24px; color: #007bff; font-weight: bold; }
        .stat-label { font-size: 12px; color: #999; margin-top: 5px; }
        #log { 
            background: #1a1a1a; 
            padding: 15px; 
            border-radius: 4px; 
            font-family: monospace; 
            font-size: 12px;
            max-height: 200px;
            overflow-y: auto;
            margin-top: 20px;
        }
        .log-entry { padding: 2px 0; }
        .log-error { color: #dc3545; }
        .log-success { color: #28a745; }
        .log-info { color: #17a2b8; }
    </style>
</head>
<body>
    <div class="header">
        <h1>🖥️ DWService Agent - Remote Desktop</h1>
    </div>
    
    <div class="container">
        <div class="info">
            <span id="status" class="status disconnected">⚫ Disconnected</span>
            <div class="stats">
                <div class="stat-item">
                    <div class="stat-value" id="fps">0</div>
                    <div class="stat-label">FPS</div>
                </div>
                <div class="stat-item">
                    <div class="stat-value" id="resolution">0x0</div>
                    <div class="stat-label">Resolution</div>
                </div>
                <div class="stat-item">
                    <div class="stat-value" id="latency">0ms</div>
                    <div class="stat-label">Latency</div>
                </div>
                <div class="stat-item">
                    <div class="stat-value" id="frames">0</div>
                    <div class="stat-label">Frames</div>
                </div>
            </div>
        </div>
        
        <div class="controls">
            <button id="connectBtn" onclick="connect()">🔌 Connect</button>
            <button id="disconnectBtn" onclick="disconnect()" disabled>⏹️ Disconnect</button>
            <button onclick="toggleFullscreen()">⛶ Fullscreen</button>
            <button onclick="clearLog()">🗑️ Clear Log</button>
        </div>
        
        <div class="desktop-container" id="desktopContainer">
            <canvas id="desktop-canvas"></canvas>
        </div>
        
        <div id="log"></div>
    </div>

    <script>
        let ws = null;
        let canvas = document.getElementById('desktop-canvas');
        let ctx = canvas.getContext('2d');
        let frameCount = 0;
        let lastFrameTime = Date.now();
        let fps = 0;
        
        function log(message, type = 'info') {
            const logDiv = document.getElementById('log');
            const entry = document.createElement('div');
            entry.className = 'log-entry log-' + type;
            entry.textContent = new Date().toLocaleTimeString() + ' - ' + message;
            logDiv.appendChild(entry);
            logDiv.scrollTop = logDiv.scrollHeight;
        }
        
        function connect() {
            const wsUrl = 'ws://' + window.location.hostname + ':8765';
            log('Connecting to ' + wsUrl + '...', 'info');
            
            ws = new WebSocket(wsUrl);
            
            ws.onopen = () => {
                log('Connected successfully!', 'success');
                document.getElementById('status').className = 'status connected';
                document.getElementById('status').textContent = '🟢 Connected';
                document.getElementById('connectBtn').disabled = true;
                document.getElementById('disconnectBtn').disabled = false;
            };
            
            ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    
                    if (data.type === 'init') {
                        canvas.width = data.width;
                        canvas.height = data.height;
                        document.getElementById('resolution').textContent = data.width + 'x' + data.height;
                        log('Screen initialized: ' + data.width + 'x' + data.height, 'success');
                    }
                    else if (data.type === 'frame') {
                        frameCount++;
                        document.getElementById('frames').textContent = frameCount;
                        
                        const now = Date.now();
                        const elapsed = now - lastFrameTime;
                        if (elapsed >= 1000) {
                            fps = Math.round((frameCount / elapsed) * 1000);
                            document.getElementById('fps').textContent = fps;
                            frameCount = 0;
                            lastFrameTime = now;
                        }
                        
                        if (data.data && data.format === 'jpeg') {
                            const img = new Image();
                            img.onload = () => {
                                ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
                            };
                            img.src = 'data:image/jpeg;base64,' + data.data;
                        } else {
                            ctx.fillStyle = '#1a1a1a';
                            ctx.fillRect(0, 0, canvas.width, canvas.height);
                            ctx.fillStyle = '#fff';
                            ctx.font = '20px Arial';
                            ctx.textAlign = 'center';
                            ctx.fillText('Screen capture not available', canvas.width/2, canvas.height/2);
                            ctx.font = '14px Arial';
                            ctx.fillText('Native libraries may not be loaded', canvas.width/2, canvas.height/2 + 30);
                        }
                    }
                } catch (e) {
                    log('Error processing frame: ' + e.message, 'error');
                }
            };
            
            ws.onerror = (error) => {
                log('WebSocket error occurred', 'error');
            };
            
            ws.onclose = () => {
                log('Disconnected from server', 'error');
                document.getElementById('status').className = 'status disconnected';
                document.getElementById('status').textContent = '⚫ Disconnected';
                document.getElementById('connectBtn').disabled = false;
                document.getElementById('disconnectBtn').disabled = true;
                ws = null;
            };
        }
        
        function disconnect() {
            if (ws) {
                ws.close();
                log('Disconnecting...', 'info');
            }
        }
        
        function toggleFullscreen() {
            const container = document.getElementById('desktopContainer');
            if (!document.fullscreenElement) {
                container.requestFullscreen();
            } else {
                document.exitFullscreen();
            }
        }
        
        function clearLog() {
            document.getElementById('log').innerHTML = '';
        }
        
        canvas.addEventListener('mousemove', (e) => {
            if (ws && ws.readyState === WebSocket.OPEN) {
                const rect = canvas.getBoundingClientRect();
                const x = Math.floor((e.clientX - rect.left) / rect.width * canvas.width);
                const y = Math.floor((e.clientY - rect.top) / rect.height * canvas.height);
                ws.send(JSON.stringify({type: 'mouse', x: x, y: y}));
            }
        });
        
        canvas.addEventListener('click', (e) => {
            if (ws && ws.readyState === WebSocket.OPEN) {
                const rect = canvas.getBoundingClientRect();
                const x = Math.floor((e.clientX - rect.left) / rect.width * canvas.width);
                const y = Math.floor((e.clientY - rect.top) / rect.height * canvas.height);
                ws.send(JSON.stringify({type: 'click', x: x, y: y, button: e.button}));
            }
        });
        
        log('Ready to connect', 'info');
    </script>
</body>
</html>
            """
            self.wfile.write(html.encode())
        else:
            self.send_response(404)
            self.end_headers()

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    with socketserver.TCPServer(("0.0.0.0", PORT), AgentWebHandler) as httpd:
        print(f"🌐 DWService Remote Desktop Web Interface")
        print(f"📡 HTTP Server: http://0.0.0.0:{PORT}")
        print(f"🔗 Access: http://YOUR_IP:{PORT}")
        print(f"⏹️  Press Ctrl+C to stop")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n👋 Server stopped")
