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
current_pos = {"lat": 25.0330, "lng": 121.5654, "status": "idle"}
TUNNEL_INFO = {"address": None, "port": None}

# --- 任務管理 ---
# 用一個專屬的 worker thread 跑 asyncio loop，避免每次新開 thread 搶連線
worker_loop = None
worker_ready = threading.Event()
current_task = None           # 目前在跑的 asyncio.Task
task_lock = threading.Lock()  # 保護 current_task 的切換

def start_worker_loop():
    """背景啟動一個常駐的 asyncio event loop"""
    global worker_loop
    worker_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(worker_loop)
    worker_ready.set()
    worker_loop.run_forever()

def auto_discover_tunnel():
    """自動執行 start-tunnel 並解析輸出"""
    print("正在自動打通隧道並抓取金鑰...")
    cmd = ["sudo", "pymobiledevice3", "remote", "start-tunnel"]
    
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    
    for line in proc.stdout:
        addr_match = re.search(r"RSD Address:\s+([a-f0-9:]+)", line)
        if addr_match: TUNNEL_INFO["address"] = addr_match.group(1)
        
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
    global current_pos
    current_pos["status"] = "moving"
    
    try:
        async with RemoteServiceDiscoveryService((TUNNEL_INFO["address"], TUNNEL_INFO["port"])) as rsd:
            with DvtSecureSocketProxyService(rsd) as dvt:
                sim = LocationSimulation(dvt)
                
                if is_teleport:
                    # 單點傳送：持續錨定，直到被 cancel
                    p = waypoints[0]
                    while True:
                        sim.set(p[0], p[1])
                        current_pos["lat"], current_pos["lng"] = p[0], p[1]
                        await asyncio.sleep(1.0)
                else:
                    # 多點移動
                    speed_ms = speed_kmh / 3.6
                    interval = 1.0 
                    for i in range(len(waypoints) - 1):
                        p1, p2 = waypoints[i], waypoints[i+1]
                        dist = calculate_distance(p1[0], p1[1], p2[0], p2[1])
                        steps = int(dist / (speed_ms * interval))
                        for s in range(steps + 1):
                            ratio = s / steps if steps > 0 else 1
                            curr_lat = p1[0] + (p2[0] - p1[0]) * ratio
                            curr_lng = p1[1] + (p2[1] - p1[1]) * ratio
                            sim.set(curr_lat, curr_lng)
                            current_pos["lat"], current_pos["lng"] = curr_lat, curr_lng
                            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        # 被新任務取代，這是正常流程
        print("🔄 舊任務已取消，準備切換")
        raise
    except Exception as e:
        print(f"❌ 模擬中斷: {e}")
    finally:
        current_pos["status"] = "idle"

def submit_task(coro):
    """
    把新任務丟進 worker loop，並先取消舊任務、等它完全結束。
    這樣新任務拿到的 RSD 連線一定是乾淨的。
    """
    global current_task
    
    worker_ready.wait()  # 確保 loop 已啟動
    
    with task_lock:
        old_task = current_task
        
        # 取消舊任務並等它真的結束
        if old_task and not old_task.done():
            worker_loop.call_soon_threadsafe(old_task.cancel)
            # 等舊任務 finally 跑完（含 RSD 連線釋放）
            done_event = threading.Event()
            def _wait_old():
                def _cb(_):
                    done_event.set()
                old_task.add_done_callback(_cb)
            worker_loop.call_soon_threadsafe(_wait_old)
            done_event.wait(timeout=3.0)
        
        # 在 worker loop 上排程新任務
        future = asyncio.run_coroutine_threadsafe(
            _wrap_and_track(coro), worker_loop
        )
        # 等 _wrap_and_track 把 current_task 設好
        future.result(timeout=2.0)

async def _wrap_and_track(coro):
    """把 coroutine 包成 task 並登記為 current_task"""
    global current_task
    current_task = asyncio.create_task(coro)

@app.route('/')
def index():
    return render_template('map.html')

@app.route('/get_status')
def get_status():
    return jsonify(current_pos)

@app.route('/set_location', methods=['POST'])
def set_loc():
    data = request.json
    lat, lng = float(data.get('lat')), float(data.get('lng'))
    
    submit_task(move_async_task([[lat, lng]], 0, is_teleport=True))
    return jsonify({"status": "teleporting"})

@app.route('/start_route', methods=['POST'])
def start_route():
    data = request.json
    points, speed = data.get('points', []), float(data.get('speed', 20))
    
    submit_task(move_async_task(points, speed))
    return jsonify({"status": "moving"})

@app.route('/stop_route', methods=['POST'])
def stop_route():
    global current_task
    with task_lock:
        if current_task and not current_task.done():
            worker_loop.call_soon_threadsafe(current_task.cancel)
    return jsonify({"status": "stopped"})

if __name__ == '__main__':
    # 啟動常駐 worker loop
    threading.Thread(target=start_worker_loop, daemon=True).start()
    # 啟動自動隧道發現
    threading.Thread(target=auto_discover_tunnel, daemon=True).start()
    print("🚀打包版啟動：http://127.0.0.1:3000")
    app.run(port=3000)