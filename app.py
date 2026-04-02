import os
import sys
import time
import math
import asyncio
import threading
import subprocess
import re
from flask import Flask, request, jsonify, render_template

# 核心連線庫
from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
from pymobiledevice3.services.dvt.dvt_secure_socket_proxy import DvtSecureSocketProxyService
from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation

# --- 打包路徑修正 ---
def get_resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

app = Flask(__name__, template_folder=get_resource_path("templates"))

# 全域狀態
stop_playback_flag = False
current_pos = {"lat": 25.0330, "lng": 121.5654, "status": "idle"}
TUNNEL_INFO = {"address": None, "port": None}

def auto_discover_tunnel():
    """自動執行 start-tunnel 並解析輸出"""
    print("正在自動打通隧道並抓取金鑰...")
    # 注意：這行會需要 sudo 權限，建議打包後從終端機用 sudo 執行該 .app
    cmd = ["sudo", "pymobiledevice3", "remote", "start-tunnel"]
    
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    
    for line in proc.stdout:
        # 抓取地址
        addr_match = re.search(r"RSD Address:\s+([a-f0-9:]+)", line)
        if addr_match: TUNNEL_INFO["address"] = addr_match.group(1)
        
        # 抓取埠號
        port_match = re.search(r"RSD Port:\s+(\d+)", line)
        if port_match: TUNNEL_INFO["port"] = int(port_match.group(1))
        
        if TUNNEL_INFO["address"] and TUNNEL_INFO["port"]:
            print(f"對接成功！目標：{TUNNEL_INFO['address']}:{TUNNEL_INFO['port']}")
            break

def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

async def move_async_task(waypoints, speed_kmh, is_teleport=False):
    global stop_playback_flag, current_pos
    stop_playback_flag = False
    current_pos["status"] = "moving"
    
    try:
        async with RemoteServiceDiscoveryService((TUNNEL_INFO["address"], TUNNEL_INFO["port"])) as rsd:
            with DvtSecureSocketProxyService(rsd) as dvt:
                sim = LocationSimulation(dvt)
                
                if is_teleport:
                    # 單點傳送模式：持續在同一個點發送訊號，防止 snap back
                    p = waypoints[0]
                    while not stop_playback_flag:
                        sim.set(p[0], p[1])
                        current_pos["lat"], current_pos["lng"] = p[0], p[1]
                        await asyncio.sleep(1.0) # 每秒錨定一次
                else:
                    # 多點移動模式 (原邏輯)
                    speed_ms = speed_kmh / 3.6
                    interval = 1.0 
                    for i in range(len(waypoints) - 1):
                        p1, p2 = waypoints[i], waypoints[i+1]
                        dist = calculate_distance(p1[0], p1[1], p2[0], p2[1])
                        steps = int(dist / (speed_ms * interval))
                        for s in range(steps + 1):
                            if stop_playback_flag: break
                            ratio = s / steps if steps > 0 else 1
                            curr_lat = p1[0] + (p2[0] - p1[0]) * ratio
                            curr_lng = p1[1] + (p2[1] - p1[1]) * ratio
                            sim.set(curr_lat, curr_lng)
                            current_pos["lat"], current_pos["lng"] = curr_lat, curr_lng
                            await asyncio.sleep(interval)
                        if stop_playback_flag: break
    except Exception as e:
        print(f"❌ 模擬中斷: {e}")
    finally:
        current_pos["status"] = "idle"

@app.route('/')
def index():
    return render_template('map.html')

@app.route('/get_status')
def get_status():
    return jsonify(current_pos)

@app.route('/set_location', methods=['POST'])
def set_loc():
    global stop_playback_flag
    stop_playback_flag = True # 先停止之前的任務
    time.sleep(0.2)
    
    data = request.json
    lat, lng = float(data.get('lat')), float(data.get('lng'))
    
    def run_teleport():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(move_async_task([[lat, lng]], 0, is_teleport=True))
        
    threading.Thread(target=run_teleport, daemon=True).start()
    return jsonify({"status": "teleporting"})

@app.route('/start_route', methods=['POST'])
def start_route():
    data = request.json
    points, speed = data.get('points', []), float(data.get('speed', 20))
    def run_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(move_async_task(points, speed))
    threading.Thread(target=run_loop, daemon=True).start()
    return jsonify({"status": "moving"})

@app.route('/stop_route', methods=['POST'])
def stop_route():
    global stop_playback_flag
    stop_playback_flag = True
    return jsonify({"status": "stopped"})

if __name__ == '__main__':
    # 啟動自動隧道發現
    threading.Thread(target=auto_discover_tunnel, daemon=True).start()
    print("🚀打包版啟動：http://127.0.0.1:3000")
    app.run(port=3000)