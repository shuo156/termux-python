from flask import Flask, render_template_string, request, Response, jsonify
import time
import os
import json
import requests
from datetime import datetime, timedelta
from pathlib import Path

app = Flask(__name__)

# 数据存储文件
DATA_FILE = 'speedtest_data.json'

# 初始化数据文件
def init_data():
    if not Path(DATA_FILE).exists():
        data = {
            'records': [],
            'weekly_top': []
        }
        save_data(data)
    return load_data()

def load_data():
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {'records': [], 'weekly_top': []}

def save_data(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_ip_info(ip=None):
    """获取IP地址信息 - 使用ipapi.co"""
    try:
        if ip is None:
            # 获取本机公网IP
            response = requests.get('https://ipapi.co/json/', timeout=10)
        else:
            # 获取指定IP的信息
            response = requests.get(f'https://ipapi.co/{ip}/json/', timeout=10)
        
        if response.status_code == 200:
            info = response.json()
            return {
                'ip': info.get('ip', 'Unknown'),
                'country': info.get('country_name', 'Unknown'),
                'city': info.get('city', 'Unknown'),
                'isp': info.get('org', 'Unknown')
            }
        else:
            # API限制或错误时的备用方案
            if ip is None:
                try:
                    ip_response = requests.get('https://ipinfo.io/json', timeout=5)
                    ip = ip_response.json()['ip']
                except:
                    ip = 'Unknown'
            
            return {
                'ip': ip,
                'country': 'Unknown',
                'city': 'Unknown',
                'isp': 'Unknown'
            }
    except Exception as e:
        print(f"获取IP信息失败: {e}")
        return {
            'ip': ip if ip else 'Unknown',
            'country': 'Unknown',
            'city': 'Unknown',
            'isp': 'Unknown'
        }

def generate_random_data(size_mb):
    """生成指定大小的随机数据"""
    chunk_size = 10240 * 1024  # 10MB chunks
    total_size = size_mb * chunk_size
    
    def generate():
        sent = 0
        data = os.urandom(chunk_size)
        while sent < total_size:
            yield data
            sent += chunk_size
    
    return generate()

def update_records(client_ip, download_speed, upload_speed, latency):
    """更新测试记录"""
    data = load_data()
    
    # 添加新记录
    record = {
        'ip': client_ip,
        'download': round(download_speed, 2),
        'upload': round(upload_speed, 2),
        'latency': round(latency, 2),
        'timestamp': datetime.now().isoformat(),
        'date': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    
    data['records'].append(record)
    
    # 清理7天前的记录
    week_ago = datetime.now() - timedelta(days=7)
    data['records'] = [r for r in data['records'] 
                       if datetime.fromisoformat(r['timestamp']) > week_ago]
    
    # 更新每周排名（按下载速度）
    weekly_sorted = sorted(data['records'], key=lambda x: x['download'], reverse=True)
    data['weekly_top'] = weekly_sorted[:10]
    
    save_data(data)
    return record

def get_client_best_record(client_ip):
    """获取客户端最佳记录"""
    data = load_data()
    client_records = [r for r in data['records'] if r['ip'] == client_ip]
    
    if not client_records:
        return None
    
    best = {
        'download': max(client_records, key=lambda x: x['download']),
        'upload': max(client_records, key=lambda x: x['upload']),
        'latency': min(client_records, key=lambda x: x['latency'])
    }
    
    return best

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>专业网络测速平台 | Pro Network SpeedTest</title>
    <style>
        :root {
            --bg-body: #0f172a;
            --bg-card: #1e293b;
            --bg-card-hover: #334155;
            --primary: #3b82f6;
            --secondary: #06b6d4;
            --success: #10b981;
            --warning: #f59e0b;
            --danger: #ef4444;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --border: rgba(255, 255, 255, 0.08);
            --glow: 0 0 20px rgba(59, 130, 246, 0.15);
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            -webkit-font-smoothing: antialiased;
        }

        body {
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
            background-color: var(--bg-body);
            background-image: radial-gradient(circle at top right, #1e293b 0%, #0f172a 40%);
            color: var(--text-main);
            min-height: 100vh;
            line-height: 1.6;
            padding-bottom: 40px;
        }

        /* SVG 图标通用样式 */
        .icon {
            width: 20px;
            height: 20px;
            fill: none;
            stroke: currentColor;
            stroke-width: 2;
            stroke-linecap: round;
            stroke-linejoin: round;
        }

        /* ================== 导航栏 ================== */
        .navbar {
            background: rgba(15, 23, 42, 0.85);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border-bottom: 1px solid var(--border);
            position: sticky;
            top: 0;
            z-index: 100;
            padding: 12px 0;
        }

        .navbar-content {
            max-width: 1200px;
            margin: 0 auto;
            padding: 0 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .logo {
            display: flex;
            align-items: center;
            gap: 12px;
            font-weight: 700;
            font-size: 1.2rem;
            letter-spacing: 0.5px;
        }

        .logo-icon-bg {
            width: 36px;
            height: 36px;
            background: linear-gradient(135deg, var(--primary), var(--secondary));
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
        }

        .server-info {
            font-size: 0.85rem;
            text-align: right;
            line-height: 1.3;
            color: var(--text-muted);
        }

        .highlight-ip {
            color: var(--secondary);
            font-family: 'SF Mono', 'Roboto Mono', monospace;
        }

        /* ================== 主容器与卡片 ================== */
        .container {
            max-width: 1100px;
            margin: 0 auto;
            padding: 30px 20px;
        }

        .card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 30px;
            margin-bottom: 24px;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
        }

        .card-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 24px;
            border-bottom: 1px solid var(--border);
            padding-bottom: 15px;
        }

        .card-title {
            font-size: 1.25rem;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .badge {
            font-size: 0.7rem;
            padding: 4px 8px;
            border-radius: 4px;
            background: rgba(59, 130, 246, 0.15);
            color: var(--primary);
            font-weight: 600;
            letter-spacing: 1px;
        }

        /* ================== 仪表盘区域 ================== */
        .status-banner {
            text-align: center;
            padding: 12px;
            border-radius: 8px;
            margin-bottom: 30px;
            font-size: 0.95rem;
            font-weight: 500;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
            transition: all 0.3s ease;
        }

        .status-idle { background: rgba(148, 163, 184, 0.1); color: var(--text-muted); border: 1px solid rgba(148, 163, 184, 0.2); }
        .status-testing { background: rgba(245, 158, 11, 0.1); color: var(--warning); border: 1px solid rgba(245, 158, 11, 0.2); }
        .status-complete { background: rgba(16, 185, 129, 0.1); color: var(--success); border: 1px solid rgba(16, 185, 129, 0.2); }

        .speed-display {
            text-align: center;
            padding: 40px 0;
        }

        .speed-value {
            font-family: 'SF Mono', 'Roboto Mono', monospace;
            font-size: 5rem;
            font-weight: 700;
            background: linear-gradient(135deg, white, var(--text-muted));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            line-height: 1;
            margin-bottom: 10px;
            letter-spacing: -2px;
            font-feature-settings: "tnum"; /* 等宽数字，防止跳动 */
        }

        .speed-unit {
            color: var(--text-muted);
            font-size: 1.2rem;
            font-weight: 500;
        }

        /* 进度条 */
        .progress-container {
            height: 6px;
            background: rgba(0, 0, 0, 0.3);
            border-radius: 10px;
            overflow: hidden;
            margin: 30px 0;
        }

        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, var(--primary), var(--secondary));
            width: 0%;
            transition: width 0.2s linear;
            box-shadow: 0 0 10px var(--primary);
        }

        /* 按钮组 */
        .test-controls {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 15px;
            margin-top: 30px;
        }

        .btn {
            border: none;
            padding: 16px;
            border-radius: 12px;
            cursor: pointer;
            font-weight: 600;
            font-size: 0.95rem;
            color: white;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            gap: 8px;
            transition: transform 0.2s, box-shadow 0.2s;
            background-size: 200% auto;
        }

        .btn svg { width: 24px; height: 24px; stroke-width: 2; }
        .btn:hover:not(:disabled) { transform: translateY(-2px); box-shadow: 0 8px 15px rgba(0,0,0,0.3); }
        .btn:active:not(:disabled) { transform: translateY(0); }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; filter: grayscale(100%); }

        .btn-primary { background: linear-gradient(135deg, var(--primary), #1d4ed8); }
        .btn-success { background: linear-gradient(135deg, var(--success), #059669); }
        .btn-warning { background: linear-gradient(135deg, var(--warning), #d97706); }
        .btn-danger { background: linear-gradient(135deg, var(--danger), #b91c1c); }

        /* ================== 实时数据网格 ================== */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 20px;
        }

        .stat-card {
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid var(--border);
            padding: 20px;
            border-radius: 12px;
            text-align: center;
        }

        .stat-icon-wrapper {
            width: 40px;
            height: 40px;
            margin: 0 auto 12px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            background: rgba(255,255,255,0.05);
            color: var(--primary);
        }
        
        .stat-label { font-size: 0.8rem; text-transform: uppercase; color: var(--text-muted); margin-bottom: 5px; }
        .stat-value { font-size: 1.5rem; font-weight: 700; color: white; font-family: 'SF Mono', monospace; }
        .stat-unit { font-size: 0.75rem; color: var(--text-muted); }

        /* ================== 列表与表格 ================== */
        .info-row {
            display: flex;
            justify-content: space-between;
            padding: 15px;
            border-bottom: 1px solid var(--border);
        }
        .info-row:last-child { border-bottom: none; }
        .info-label { color: var(--text-muted); }
        .info-value { font-weight: 600; font-family: 'SF Mono', monospace; }

        /* 历史最佳 */
        .record-stats {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            text-align: center;
            gap: 10px;
        }
        .record-val { font-size: 1.25rem; font-weight: 700; color: var(--success); font-family: 'SF Mono', monospace; }

        /* 排行榜表格 */
        .leaderboard { overflow-x: auto; -webkit-overflow-scrolling: touch; }
        .leaderboard-table { width: 100%; border-collapse: collapse; white-space: nowrap; font-size: 0.9rem; }
        .leaderboard-table th { 
            text-align: left; padding: 15px; 
            color: var(--text-muted); font-weight: 600; font-size: 0.75rem; text-transform: uppercase;
            border-bottom: 1px solid var(--border);
        }
        .leaderboard-table td { padding: 15px; border-bottom: 1px solid var(--border); color: var(--text-main); }
        .leaderboard-table tr:hover { background: rgba(255,255,255,0.02); }
        
        .rank-num { font-weight: 700; color: var(--secondary); }
        .rank-1 .rank-num { color: #fbbf24; }
        .rank-2 .rank-num { color: #e2e8f0; }
        .rank-3 .rank-num { color: #b45309; }

        .loading-text { font-style: italic; opacity: 0.7; }

        /* ================== 移动端适配 (核心 CSS) ================== */
        @media (max-width: 768px) {
            .navbar-content {
                flex-direction: column;
                gap: 10px;
                padding: 10px;
            }
            .server-info { text-align: center; font-size: 0.75rem; }

            .container { padding: 15px; }
            .card { padding: 20px; border-radius: 12px; }

            /* 速度大字适配 */
            .speed-value { font-size: 3.5rem; }

            /* 按钮变为2列 */
            .test-controls { grid-template-columns: 1fr 1fr; }
            /* 完整测试按钮独占一行，便于点击 */
            .btn-danger { grid-column: 1 / -1; }

            /* 数据卡片变为2列 */
            .stats-grid { grid-template-columns: 1fr 1fr; gap: 10px; }
            .stat-card { padding: 15px; }
            .stat-value { font-size: 1.2rem; }

            /* 信息行 */
            .info-row { flex-direction: column; gap: 5px; align-items: flex-start; }
            .info-value { font-size: 0.95rem; word-break: break-all; }

            /* 表格隐藏次要列 */
            .leaderboard-table th:nth-child(4), .leaderboard-table td:nth-child(4),
            .leaderboard-table th:nth-child(6), .leaderboard-table td:nth-child(6) {
                display: none;
            }
        }
    </style>
</head>
<body>
            <div class="server-info">
                <div>节点: <span id="serverLocation" class="loading-text">检测中...</span></div>
                <div style="margin-top:2px;">IP: <span class="highlight-ip" id="serverIp">---</span></div>
            </div>
        </div>
    </nav>
    
    <div class="container">
        <div class="card">
            <div id="status" class="status-banner status-idle">
                <svg class="icon" viewBox="0 0 24 24"><path d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                <span>系统就绪 - 准备测速</span>
            </div>
            
            <div class="speed-display">
                <div class="speed-unit" style="margin-bottom: 5px;">当前速度</div>
                <div class="speed-value" id="speedValue">0.00</div>
                <div class="speed-unit">Mbps</div>
            </div>
            
            <div class="progress-container">
                <div class="progress-fill" id="progressFill"></div>
            </div>
            
            <div class="test-controls">
                <button class="btn btn-primary" onclick="startDownloadTest()">
                    <svg class="icon" viewBox="0 0 24 24"><path d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/></svg>
                    <span>下载测速</span>
                </button>
                <button class="btn btn-success" onclick="startUploadTest()">
                    <svg class="icon" viewBox="0 0 24 24"><path d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"/></svg>
                    <span>上传测速</span>
                </button>
                <button class="btn btn-warning" onclick="testLatency()">
                    <svg class="icon" viewBox="0 0 24 24"><path d="M8.111 16.404a5.5 5.5 0 017.778 0M12 20h.01m-7.08-7.071c3.904-3.905 10.236-3.905 14.141 0M1.394 9.393c5.857-5.857 15.355-5.857 21.213 0"/></svg>
                    <span>延迟测试</span>
                </button>
                <button class="btn btn-danger" onclick="startFullTest()">
                    <svg class="icon" viewBox="0 0 24 24"><path d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                    <span>完整测试</span>
                </button>
            </div>
        </div>
        
        <div class="card">
            <div class="card-header">
                <h2 class="card-title">
                    <svg class="icon" viewBox="0 0 24 24" style="color:var(--secondary)"><path d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>
                    本次结果
                </h2>
                <span class="badge">SESSION</span>
            </div>
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-icon-wrapper">
                        <svg class="icon" viewBox="0 0 24 24"><path d="M19 14l-7 7m0 0l-7-7m7 7V3"/></svg>
                    </div>
                    <div class="stat-label">下载</div>
                    <div class="stat-value" id="downloadSpeed">--</div>
                    <div class="stat-unit">Mbps</div>
                </div>
                <div class="stat-card">
                    <div class="stat-icon-wrapper">
                        <svg class="icon" viewBox="0 0 24 24"><path d="M5 10l7-7m0 0l7 7m-7-7v18"/></svg>
                    </div>
                    <div class="stat-label">上传</div>
                    <div class="stat-value" id="uploadSpeed">--</div>
                    <div class="stat-unit">Mbps</div>
                </div>
                <div class="stat-card">
                    <div class="stat-icon-wrapper">
                        <svg class="icon" viewBox="0 0 24 24"><path d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                    </div>
                    <div class="stat-label">延迟</div>
                    <div class="stat-value" id="latency">--</div>
                    <div class="stat-unit">ms</div>
                </div>
                <div class="stat-card">
                    <div class="stat-icon-wrapper">
                        <svg class="icon" viewBox="0 0 24 24"><path d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
                    </div>
                    <div class="stat-label">抖动</div>
                    <div class="stat-value" id="jitter">--</div>
                    <div class="stat-unit">ms</div>
                </div>
            </div>
        </div>
        
        <div class="card">
            <div class="card-header">
                <h2 class="card-title">
                    <svg class="icon" viewBox="0 0 24 24" style="color:var(--text-muted)"><path d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/></svg>
                    客户端信息
                </h2>
            </div>
            <div class="info-row">
                <span class="info-label">您的 IP 地址</span>
                <span class="info-value loading-text" id="clientIp">加载中...</span>
            </div>
            <div class="info-row">
                <span class="info-label">物理位置</span>
                <span class="info-value loading-text" id="clientLocation">加载中...</span>
            </div>
            <div class="info-row">
                <span class="info-label">运营商 (ISP)</span>
                <span class="info-value loading-text" id="clientIsp">加载中...</span>
            </div>
        </div>
        
        <div class="card">
            <div class="card-header">
                <h2 class="card-title">
                    <svg class="icon" viewBox="0 0 24 24" style="color:#fbbf24"><path d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z"/></svg>
                    个人历史最佳
                </h2>
                <span class="badge">BEST</span>
            </div>
            <div id="bestRecords">
                <p style="text-align: center; color: var(--text-muted); padding: 15px;">暂无历史记录，完成测试后将显示您的最佳成绩</p>
            </div>
        </div>
        
        <div class="card">
            <div class="card-header">
                <h2 class="card-title">
                    <svg class="icon" viewBox="0 0 24 24" style="color:var(--success)"><path d="M9 12l2 2 4-4M7.835 4.697a3.42 3.42 0 001.946-.806 3.42 3.42 0 014.438 0 3.42 3.42 0 001.946.806 3.42 3.42 0 013.138 3.138 3.42 3.42 0 00.806 1.946 3.42 3.42 0 010 4.438 3.42 3.42 0 00-.806 1.946 3.42 3.42 0 01-3.138 3.138 3.42 3.42 0 00-1.946.806 3.42 3.42 0 01-4.438 0 3.42 3.42 0 00-1.946-.806 3.42 3.42 0 01-3.138-3.138 3.42 3.42 0 00-.806-1.946 3.42 3.42 0 010-4.438 3.42 3.42 0 00.806-1.946 3.42 3.42 0 013.138-3.138z"/></svg>
                    周速度排行
                </h2>
                <span class="badge">TOP 10</span>
            </div>
            <div class="leaderboard">
                <table class="leaderboard-table">
                    <thead>
                        <tr>
                            <th style="width: 50px;">#</th>
                            <th>IP地址</th>
                            <th>下载</th>
                            <th>上传</th>
                            <th>延迟</th>
                            <th>时间</th>
                        </tr>
                    </thead>
                    <tbody id="leaderboardBody">
                        <tr>
                            <td colspan="6" style="text-align: center; padding: 30px; color: var(--text-muted);">暂无排行数据</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>
    
<script>
// 核心变量
let currentDownload = 0;
let currentUpload = 0;
let currentLatency = 0;
let jitterValues = [];

// UI更新辅助函数
function setStatus(text, type) {
    const el = document.getElementById('status');
    // 使用SVG图标替换原来的emoji逻辑
    let icon = '';
    if(type === 'testing') icon = '<svg class="icon" viewBox="0 0 24 24"><path d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>';
    else if(type === 'complete') icon = '<svg class="icon" viewBox="0 0 24 24"><path d="M5 13l4 4L19 7"/></svg>';
    else icon = '<svg class="icon" viewBox="0 0 24 24"><path d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>';
    
    el.className = `status-banner status-${type}`;
    el.innerHTML = `${icon} <span>${text}</span>`;
}

function setSpeed(value) {
    document.getElementById('speedValue').innerText = value.toFixed(2);
}

function setProgress(percent) {
    document.getElementById('progressFill').style.width = percent + '%';
}

/* ================== 基础信息 ================== */

async function loadClientInfo() {
    try {
        const r = await fetch('/api/client-info');
        const d = await r.json();
        document.getElementById('clientIp').innerText = d.ip;
        document.getElementById('clientLocation').innerText = `${d.city}, ${d.country}`;
        document.getElementById('clientIsp').innerText = d.isp;
        
        // 移除加载样式
        ['clientIp', 'clientLocation', 'clientIsp'].forEach(id => {
            document.getElementById(id).classList.remove('loading-text');
        });
    } catch(e) {
        console.error("Failed to load client info");
    }
}

async function loadServerInfo() {
    try {
        const r = await fetch('/api/server-info');
        const d = await r.json();
        document.getElementById('serverIp').innerText = d.ip;
        document.getElementById('serverLocation').innerText = `${d.city}, ${d.country}`;
        document.getElementById('serverLocation').classList.remove('loading-text');
    } catch(e) {
        console.error("Failed to load server info");
    }
}

/* ================== 延迟测试 ================== */

async function testLatency() {
    setStatus('正在测试延迟…', 'testing');
    jitterValues = [];

    let total = 0;
    const count = 5;

    for (let i = 0; i < count; i++) {
        const start = performance.now();
        try {
            await fetch('/ping?_=' + Math.random());
            const end = performance.now();
            const t = end - start;
            jitterValues.push(t);
            total += t;
            setProgress((i + 1) / count * 100);
        } catch(e) {
            console.error("Ping failed");
        }
    }

    currentLatency = total / count;
    const jitter = jitterValues.length > 0 ? (Math.max(...jitterValues) - Math.min(...jitterValues)) : 0;

    document.getElementById('latency').innerText = currentLatency.toFixed(1);
    document.getElementById('jitter').innerText = jitter.toFixed(1);

    setStatus('延迟测试完成', 'complete');
}

/* ================== 下载测速 ================== */

async function startDownloadTest() {
    setStatus('正在进行下载测速…', 'testing');
    setProgress(0);

    const sizeMB = 20;
    const start = performance.now();

    try {
        const response = await fetch(`/download/${sizeMB}`);
        const reader = response.body.getReader();

        let received = 0;
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            received += value.length;
            setProgress(Math.min(received / (sizeMB * 1024 * 1024) * 100, 100));
            
            // 简单的实时计算，实际项目中可优化平滑度
            const now = performance.now();
            const duration = (now - start) / 1000;
            if(duration > 0) {
                 const instantSpeed = (received * 8) / duration / 1024 / 1024;
                 setSpeed(instantSpeed);
            }
        }
    } catch(e) {
        console.error("Download test failed");
    }

    const end = performance.now();
    const seconds = (end - start) / 1000;
    currentDownload = (seconds > 0) ? ((sizeMB * 1024 * 1024 * 8) / seconds / 1024 / 1024) : 0;

    setSpeed(currentDownload);
    document.getElementById('downloadSpeed').innerText = currentDownload.toFixed(2);

    setStatus('下载测速完成', 'complete');
}

/* ================== 上传测速 ================== */

async function startUploadTest() {
    setStatus('正在进行上传测速…', 'testing');
    setProgress(0);

    const sizeMB = 10;
    const data = new Uint8Array(sizeMB * 1024 * 1024);

    const start = performance.now();
    try {
        await fetch('/upload', {
            method: 'POST',
            body: data
        });
    } catch(e) {
        console.error("Upload test failed");
    }
    const end = performance.now();

    const seconds = (end - start) / 1000;
    currentUpload = (seconds > 0) ? ((data.length * 8) / seconds / 1024 / 1024) : 0;

    setSpeed(currentUpload);
    document.getElementById('uploadSpeed').innerText = currentUpload.toFixed(2);

    setProgress(100);
    setStatus('上传测速完成', 'complete');
}

/* ================== 完整测试 ================== */

async function startFullTest() {
    // 禁用按钮防止重复点击
    const btns = document.querySelectorAll('button');
    btns.forEach(b => b.disabled = true);

    currentDownload = 0;
    currentUpload = 0;
    currentLatency = 0;

    await testLatency();
    await startDownloadTest();
    await startUploadTest();

    try {
        await fetch('/api/save-result', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                download: currentDownload,
                upload: currentUpload,
                latency: currentLatency
            })
        });
    } catch(e) {
        console.error("Failed to save result");
    }

    loadBestRecords();
    loadLeaderboard();
    
    // 恢复按钮
    btns.forEach(b => b.disabled = false);
}

/* ================== 历史 & 排行 ================== */

async function loadBestRecords() {
    try {
        const r = await fetch('/api/best-records');
        const d = await r.json();
        if (!d.records) return;

        document.getElementById('bestRecords').innerHTML = `
            <div class="record-stats" style="padding:15px;">
                <div>
                    <div style="font-size:0.8rem;color:#94a3b8">下载</div>
                    <div class="record-val">${parseFloat(d.records.download.download).toFixed(2)}</div>
                    <div style="font-size:0.7rem;color:#64748b">Mbps</div>
                </div>
                <div>
                    <div style="font-size:0.8rem;color:#94a3b8">上传</div>
                    <div class="record-val" style="color:#3b82f6">${parseFloat(d.records.upload.upload).toFixed(2)}</div>
                    <div style="font-size:0.7rem;color:#64748b">Mbps</div>
                </div>
                <div>
                    <div style="font-size:0.8rem;color:#94a3b8">延迟</div>
                    <div class="record-val" style="color:#f59e0b">${parseFloat(d.records.latency.latency).toFixed(1)}</div>
                    <div style="font-size:0.7rem;color:#64748b">ms</div>
                </div>
            </div>
        `;
    } catch(e) {
        console.error("Load records failed");
    }
}

async function loadLeaderboard() {
    try {
        const r = await fetch('/api/leaderboard');
        const d = await r.json();
        const body = document.getElementById('leaderboardBody');

        if (!d.top || d.top.length === 0) return;

        body.innerHTML = '';
        d.top.forEach((r, i) => {
            const rankClass = i < 3 ? `rank-${i + 1}` : '';
            body.innerHTML += `
                <tr class="${rankClass}">
                    <td class="rank-num">${i + 1}</td>
                    <td style="font-family:monospace">${r.ip}</td>
                    <td style="color:var(--success);font-weight:700">${parseFloat(r.download).toFixed(2)}</td>
                    <td>${parseFloat(r.upload).toFixed(2)}</td>
                    <td>${parseFloat(r.latency).toFixed(1)}</td>
                    <td style="color:#64748b;font-size:0.85em">${r.date}</td>
                </tr>
            `;
        });
    } catch(e) {
        console.error("Load leaderboard failed");
    }
}

/* ================== 启动 ================== */

window.onload = () => {
    loadClientInfo();
    loadServerInfo();
    loadLeaderboard();
};
</script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/download/<int:size_mb>')
def download_test(size_mb):
    """下载测速端点"""
    size_mb = min(size_mb, 100)  # 限制最大100MB
    return Response(
        generate_random_data(size_mb),
        mimetype='application/octet-stream',
        headers={
            'Content-Disposition': f'attachment; filename=test_{size_mb}mb.bin',
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0'
        }
    )

@app.route('/upload', methods=['POST'])
def upload_test():
    """上传测速端点"""
    data = request.get_data()
    return jsonify({'received': len(data), 'status': 'ok'})

@app.route('/ping')
def ping():
    """延迟测试端点"""
    return jsonify({'timestamp': time.time(), 'status': 'pong'})

@app.route('/api/server-info')
def server_info():
    """获取服务器信息"""
    return jsonify(get_ip_info())

@app.route('/api/client-info')
def client_info():
    """获取客户端信息"""
    try:
        # 获取客户端真实IP（支持代理）
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        if ',' in client_ip:
            client_ip = client_ip.split(',')[0].strip()
        
        # 如果是本地IP，尝试获取公网IP
        if client_ip in ['127.0.0.1', 'localhost', '::1']:
            info = get_ip_info()  # 不传IP参数，获取服务器的公网IP
        else:
            info = get_ip_info(client_ip)
        
        return jsonify(info)
    except Exception as e:
        print(f"获取客户端信息失败: {e}")
        return jsonify({
            'ip': request.remote_addr,
            'country': 'Unknown',
            'city': 'Unknown',
            'isp': 'Unknown'
        })

@app.route('/api/save-result', methods=['POST'])
def save_result():
    """保存测试结果"""
    try:
        results = request.json
        
        # 获取客户端IP
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        if ',' in client_ip:
            client_ip = client_ip.split(',')[0].strip()
        
        # 如果没有有效的IP数据，返回错误
        if not client_ip or client_ip in ['127.0.0.1', 'localhost', '::1']:
            # 尝试获取真实公网IP
            ip_info = get_ip_info()
            client_ip = ip_info.get('ip', 'Unknown')
        
        update_records(
            client_ip,
            results.get('download', 0),
            results.get('upload', 0),
            results.get('latency', 0)
        )
        
        return jsonify({'status': 'success', 'ip': client_ip})
    except Exception as e:
        print(f"保存结果失败: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/best-records')
def best_records():
    """获取客户端最佳记录"""
    try:
        # 获取客户端IP
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        if ',' in client_ip:
            client_ip = client_ip.split(',')[0].strip()
        
        # 如果是本地IP，尝试获取公网IP
        if client_ip in ['127.0.0.1', 'localhost', '::1']:
            ip_info = get_ip_info()
            client_ip = ip_info.get('ip', 'Unknown')
        
        records = get_client_best_record(client_ip)
        return jsonify({'records': records, 'ip': client_ip})
    except Exception as e:
        print(f"获取最佳记录失败: {e}")
        return jsonify({'records': None, 'ip': 'Unknown'})

@app.route('/api/leaderboard')
def leaderboard():
    """获取排行榜"""
    data = load_data()
    return jsonify({'top': data['weekly_top']})

if __name__ == '__main__':
    print("=" * 60)
    print("🚀 专业网络测速平台启动中...")
    print("=" * 60)
    
    # 初始化数据
    init_data()
    
    # 获取服务器IP信息
    print("\n正在获取服务器信息...")
    print("⏳ 请稍候，正在连接 ipapi.co API...")
    server_ip_info = get_ip_info()
    print(f"✅ 服务器IP: {server_ip_info['ip']}")
    print(f"✅ 位置: {server_ip_info['city']}, {server_ip_info['country']}")
    print(f"✅ 运营商: {server_ip_info['isp']}")
    
    print("\n访问地址：")
    print("  📱 本地访问: http://127.0.0.1:8080")
    print("  🌐 局域网访问: http://0.0.0.0:8080")
    print("\n功能特性：")
    print("  ✅ 下载/上传速度测试")
    print("  ✅ 延迟和抖动测试")
    print("  ✅ 个人历史最佳记录")
    print("  ✅ 本周速度排行榜 TOP 10")
    print("  ✅ 使用 ipapi.co API 获取IP信息")
    print("  ✅ 专业化界面设计")
    print("  ✅ JSON数据持久化存储")
    print("\n📝 注意事项：")
    print("  - ipapi.co 免费版限制：1000次/天")
    print("  - 如遇到IP加载失败，请稍后重试")
    print("  - 建议使用公网环境测试以获得准确IP信息")
    print("\n按 Ctrl+C 停止服务器")
    print("=" * 60)
    
    app.run(host='0.0.0.0', port=8080, debug=False, threaded=True)