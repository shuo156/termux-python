import sqlite3
import time
import re
from flask import Flask, request, jsonify, render_template_string, g

app = Flask(__name__)
DB_FILE = 'benchmark_v3.db'

# --- 数据库处理 ---
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DB_FILE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        # 表结构：增加 ip_display 字段用于直接存储脱敏IP
        db.execute('''
            CREATE TABLE IF NOT EXISTS scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                score INTEGER NOT NULL,
                device_name TEXT,
                gpu_renderer TEXT,
                platform TEXT,
                is_vm INTEGER,
                ip TEXT,
                ip_display TEXT,
                timestamp INTEGER
            )
        ''')
        db.execute('CREATE INDEX IF NOT EXISTS idx_score_plat ON scores(score DESC, platform)')
        db.commit()

# --- 辅助函数：IP 脱敏 ---
def mask_ip(ip):
    if not ip: return "未知IP"
    parts = ip.split('.')
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.*.*"
    # 处理 IPv6 (简单截取)
    if ':' in ip:
        return "IPv6"
    return "Hidden"

# --- 辅助函数：后端设备名解析 (作为前端数据的补充) ---
def parse_device_name_backend(ua, renderer):
    device_name = "未知设备"
    
    # 尝试提取 Android 型号
    if 'Android' in ua:
        match = re.search(r';\s?([^;]+?)\s?Build/', ua)
        if match: device_name = match.group(1)
        else: device_name = "Android Generic"
    elif 'iPhone' in ua:
        device_name = "Apple iPhone"
    elif 'iPad' in ua:
        device_name = "Apple iPad"
    elif 'Macintosh' in ua:
        device_name = "Mac"
    elif 'Windows' in ua:
        device_name = "Windows PC"
    elif 'Linux' in ua:
        device_name = "Linux PC"

    # 显卡信息补充
    if renderer and renderer != 'Unknown':
        clean_renderer = renderer.replace('ANGLE (', '').replace(')', '')
        # 移除一些冗余厂商前缀，保持简洁
        clean_renderer = clean_renderer.replace('NVIDIA Corporation', '').replace('Intel Inc.', '').strip()
        device_name = f"{device_name} / {clean_renderer}"

    return device_name

# --- 前端页面 ---
HTML_TEMPLATE = r'''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>系统图形性能极限测试</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
    <script>
        tailwind.config = {
            theme: {
                extend: {
                    colors: {
                        'sys-bg': '#0a0a0a',
                        'sys-panel': '#141414',
                        'sys-border': '#333333',
                        'sys-accent': '#00ff9d', 
                        'sys-danger': '#ff4444',
                        'sys-text': '#e0e0e0',
                        'sys-dim': '#666666'
                    },
                    fontFamily: {
                        mono: ['"JetBrains Mono"', 'monospace'], // 强制等宽
                    }
                }
            }
        }
    </script>
    <style>
        body { background-color: #0a0a0a; color: #e0e0e0; font-family: 'JetBrains Mono', monospace; }
        /* 网格背景 */
        .grid-bg {
            background-image: 
                linear-gradient(rgba(255, 255, 255, 0.03) 1px, transparent 1px),
                linear-gradient(90deg, rgba(255, 255, 255, 0.03) 1px, transparent 1px);
            background-size: 20px 20px;
        }
        canvas { display: block; }
        .no-scrollbar::-webkit-scrollbar { display: none; }
        .no-scrollbar { -ms-overflow-style: none; scrollbar-width: none; }
        .blink { animation: blinker 1.5s step-end infinite; }
        @keyframes blinker { 50% { opacity: 0; } }
    </style>
</head>
<body class="min-h-screen grid-bg flex flex-col items-center py-4 px-2 select-none">

    <header class="w-full max-w-4xl border-b border-sys-border pb-4 mb-4 flex justify-between items-end">
        <div>
            <h1 class="text-xl font-bold tracking-tighter text-white">SYS_BENCH<span class="text-sys-accent text-sm">_CN</span></h1>
            <p class="text-xs text-sys-dim mt-1">WEBGL 图形算力压力测试</p>
        </div>
        <div class="text-right text-[10px] leading-tight">
            <div id="sysPlatform" class="text-sys-accent font-bold">DETECTING...</div>
            <div id="sysIp" class="text-sys-dim">IP: 正在获取...</div>
        </div>
    </header>

    <main class="w-full max-w-4xl grid grid-cols-1 lg:grid-cols-3 gap-4">
        
        <div class="lg:col-span-2 flex flex-col gap-4">
            <div class="relative bg-sys-panel border border-sys-border shadow-2xl">
                <div class="absolute top-2 left-2 z-10 text-[10px] text-white mix-blend-difference pointer-events-none">
                    <div>实体数量: <span id="valEntities" class="font-bold">0</span></div>
                    <div>实时帧率: <span id="valFps" class="font-bold">--</span></div>
                </div>

                <canvas id="mainCv" class="w-full h-[320px] bg-black"></canvas>
                
                <div id="overlay" class="absolute inset-0 bg-black/80 backdrop-blur-sm flex flex-col items-center justify-center z-20">
                    <button onclick="startTest()" class="group relative px-6 py-2 border border-sys-accent text-sys-accent hover:bg-sys-accent hover:text-black transition-all">
                        <span class="font-bold tracking-widest">初始化测试程序</span>
                    </button>
                    <div class="mt-4 text-[10px] text-sys-dim text-center">
                        <div>警告：该测试将满载您的 GPU</div>
                        <div id="cheatWarning" class="text-sys-danger hidden mt-1">检测到虚拟机环境 - 成绩无效</div>
                    </div>
                </div>
            </div>

            <div class="bg-sys-panel border border-sys-border h-[100px] relative">
                <div class="absolute top-1 left-2 text-[10px] text-sys-dim">FPS 波动记录</div>
                <canvas id="graphCv" class="w-full h-full"></canvas>
            </div>
        </div>

        <div class="lg:col-span-1 flex flex-col gap-4 h-[440px]">
            <div class="flex-1 bg-sys-panel border border-sys-border flex flex-col overflow-hidden">
                <div class="bg-sys-border/20 px-3 py-2 text-[10px] font-bold flex justify-between text-white border-b border-sys-border">
                    <span>桌面端排行 (PC)</span>
                    <span class="text-sys-dim">TOP 10</span>
                </div>
                <div id="rankPC" class="flex-1 overflow-y-auto no-scrollbar p-2 space-y-1">
                    <div class="text-[10px] text-sys-dim text-center py-4">数据加载中...</div>
                </div>
            </div>

            <div class="flex-1 bg-sys-panel border border-sys-border flex flex-col overflow-hidden">
                <div class="bg-sys-border/20 px-3 py-2 text-[10px] font-bold flex justify-between text-white border-b border-sys-border">
                    <span>移动端排行 (Mobile)</span>
                    <span class="text-sys-dim">TOP 10</span>
                </div>
                <div id="rankMobile" class="flex-1 overflow-y-auto no-scrollbar p-2 space-y-1">
                    <div class="text-[10px] text-sys-dim text-center py-4">数据加载中...</div>
                </div>
            </div>
        </div>

    </main>

    <script>
        // --- 核心变量 ---
        const mainCv = document.getElementById('mainCv');
        const mainCtx = mainCv.getContext('2d', { alpha: false, desynchronized: true });
        const graphCv = document.getElementById('graphCv');
        const graphCtx = graphCv.getContext('2d');
        
        let state = {
            running: false,
            particles: [],
            lastTime: 0,
            frameCount: 0,
            fps: 60,
            fpsHistory: new Array(100).fill(0),
            rendererName: 'Unknown',
            isVM: false,
            detectedPlatform: 'PC' // 默认为PC，检测后修正
        };

        // --- 1. 强力平台检测 (修复桌面模式问题) ---
        function detectPlatform() {
            const ua = navigator.userAgent;
            const hasTouch = (navigator.maxTouchPoints && navigator.maxTouchPoints > 0) || ('ontouchstart' in window);
            
            // 逻辑：如果有触摸屏，极大可能是移动设备 (包括 iPad Pro 和 手机桌面模式)
            // 排除掉极少数 Windows 触摸屏笔记本 (通常 Windows UA 很明显)
            
            let platform = 'PC';
            
            if (/Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(ua)) {
                platform = 'MOBILE';
            } else if (hasTouch && /Macintosh/i.test(ua)) {
                // iPadOS 13+ 默认伪装成 Macintosh，但有触摸点
                platform = 'MOBILE';
            } else if (hasTouch && screen.width < 1000) {
                // 窄屏触摸设备，基本是手机开启了桌面模式
                platform = 'MOBILE'; 
            }

            state.detectedPlatform = platform;
            document.getElementById('sysPlatform').innerText = `PLATFORM: ${platform}`;
            
            // 虚拟机检测
            const canvas = document.createElement('canvas');
            const gl = canvas.getContext('webgl');
            if(gl) {
                const debugInfo = gl.getExtension('WEBGL_debug_renderer_info');
                if(debugInfo) {
                    state.rendererName = gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL);
                    // 检查关键词
                    const vmWords = ['SwiftShader', 'llvmpipe', 'VirtualBox', 'VMware', 'Mesa OffScreen', 'Simulator', 'Android Emulator'];
                    if(vmWords.some(w => state.rendererName.includes(w))) {
                        state.isVM = true;
                        document.getElementById('cheatWarning').classList.remove('hidden');
                    }
                }
            }
        }

        // --- 2. 绘图适配 ---
        function resize() {
            const dpr = window.devicePixelRatio || 1;
            const r1 = mainCv.getBoundingClientRect();
            mainCv.width = r1.width * dpr;
            mainCv.height = r1.height * dpr;
            mainCtx.scale(dpr, dpr);
            
            const r2 = graphCv.getBoundingClientRect();
            graphCv.width = r2.width * dpr;
            graphCv.height = r2.height * dpr;
        }
        window.addEventListener('resize', resize);

        // --- 3. 测试循环 ---
        function startTest() {
            if(state.running) return;
            state.running = true;
            state.particles = [];
            state.fpsHistory.fill(0);
            document.getElementById('overlay').classList.add('hidden');
            
            state.lastTime = performance.now();
            loop();
        }

        function loop() {
            if(!state.running) return;
            const now = performance.now();
            const delta = now - state.lastTime;
            state.lastTime = now;
            state.fps = 1000 / delta;
            
            // UI 更新频率限制
            state.frameCount++;
            if(state.frameCount % 5 === 0) {
                document.getElementById('valFps').innerText = state.fps.toFixed(0);
                document.getElementById('valEntities').innerText = state.particles.length;
                
                // 记录FPS历史
                state.fpsHistory.push(state.fps);
                state.fpsHistory.shift();
                drawGraph();
            }

            // 终止条件: FPS < 25 且 粒子数足够多 (防止开局卡顿)
            if(state.fps < 25 && state.particles.length > 500) {
                finish();
                return;
            }

            // 增加负载 (根据当前FPS动态调整增加量)
            const addCount = state.fps > 55 ? 50 : 20;
            const w = mainCv.width / window.devicePixelRatio;
            const h = mainCv.height / window.devicePixelRatio;
            
            for(let i=0; i<addCount; i++) {
                state.particles.push({
                    x: Math.random() * w,
                    y: Math.random() * h,
                    vx: (Math.random()-0.5) * 5,
                    vy: (Math.random()-0.5) * 5,
                    color: `hsl(${Math.random()*360}, 70%, 60%)`
                });
            }

            // 渲染场景
            mainCtx.fillStyle = '#000000';
            mainCtx.fillRect(0, 0, w, h);
            
            for(let p of state.particles) {
                p.x += p.vx;
                p.y += p.vy;
                if(p.x < 0 || p.x > w) p.vx *= -1;
                if(p.y < 0 || p.y > h) p.vy *= -1;
                mainCtx.fillStyle = p.color;
                mainCtx.fillRect(p.x, p.y, 2, 2);
            }

            requestAnimationFrame(loop);
        }

        function drawGraph() {
            const w = graphCv.width;
            const h = graphCv.height;
            const ctx = graphCtx;
            
            ctx.fillStyle = '#141414';
            ctx.fillRect(0, 0, w, h);
            
            ctx.beginPath();
            ctx.strokeStyle = '#00ff9d';
            ctx.lineWidth = 2;
            
            const step = w / state.fpsHistory.length;
            
            for(let i=0; i<state.fpsHistory.length; i++) {
                const val = state.fpsHistory[i];
                // 映射: 60fps在顶端, 0在底端
                const y = h - (Math.min(val, 65) / 65) * h;
                if(i===0) ctx.moveTo(0, y);
                else ctx.lineTo(i * step, y);
            }
            ctx.stroke();
        }

        function finish() {
            state.running = false;
            document.getElementById('overlay').classList.remove('hidden');
            document.getElementById('overlay').innerHTML = `
                <div class="text-center bg-sys-panel border border-sys-border p-6 shadow-2xl">
                    <div class="text-[10px] text-sys-dim mb-1">FINAL SCORE</div>
                    <div class="text-4xl font-bold text-white mb-2">${state.particles.length}</div>
                    <div class="text-[10px] ${state.isVM ? 'text-sys-danger' : 'text-sys-accent'} mb-4">
                        ${state.isVM ? '检测到虚拟机 - 成绩无效' : '成绩已上传'}
                    </div>
                    <button onclick="startTest()" class="text-xs border border-sys-dim px-4 py-2 text-sys-dim hover:text-white hover:border-white transition">重试</button>
                </div>
            `;
            submit();
        }

        async function submit() {
            try {
                const res = await fetch('/api/submit', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        score: state.particles.length,
                        renderer: state.rendererName,
                        platform: state.detectedPlatform, // 关键：使用前端检测的平台结果
                        is_vm: state.isVM
                    })
                });
                const data = await res.json();
                renderRank(data);
            } catch(e) { console.error(e); }
        }

        async function loadRank() {
            const res = await fetch('/api/scores');
            const data = await res.json();
            renderRank(data);
        }

        function renderRank(data) {
            const renderItem = (item, idx) => `
                <div class="flex justify-between items-center border-b border-sys-border/30 pb-1 mb-1 last:border-0">
                    <div class="w-2/3 truncate">
                        <span class="text-sys-accent mr-1 font-bold">${idx+1}</span>
                        <span class="text-[10px] ${item.is_vm ? 'text-sys-danger' : 'text-sys-text'}">
                            ${item.is_vm ? '[VM] ' : ''}${item.device_name}
                        </span>
                    </div>
                    <div class="w-1/3 text-right">
                        <div class="text-[10px] text-white font-bold">${item.score}</div>
                        <div class="text-[8px] text-sys-dim font-mono">${item.ip_display}</div>
                    </div>
                </div>
            `;
            
            document.getElementById('rankPC').innerHTML = data.pc.length ? data.pc.map(renderItem).join('') : '<div class="text-[10px] text-sys-dim text-center mt-4">暂无数据</div>';
            document.getElementById('rankMobile').innerHTML = data.mobile.length ? data.mobile.map(renderItem).join('') : '<div class="text-[10px] text-sys-dim text-center mt-4">暂无数据</div>';
        }

        // 初始化
        detectPlatform();
        resize();
        loadRank();

    </script>
</body>
</html>
'''

# --- 路由逻辑 ---

@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/submit', methods=['POST'])
def submit_score():
    data = request.json
    score = int(data.get('score', 0))
    renderer = data.get('renderer', 'Unknown')
    # 优先使用前端传来的 platform 判断（因为它能检测到触摸屏）
    platform_from_client = data.get('platform', 'PC')
    client_vm_flag = data.get('is_vm', False)
    
    ua = request.headers.get('User-Agent', '')
    
    # 获取真实 IP
    if request.headers.getlist("X-Forwarded-For"):
        real_ip = request.headers.getlist("X-Forwarded-For")[0]
    else:
        real_ip = request.remote_addr
    
    # 生成显示用的脱敏 IP
    ip_display = mask_ip(real_ip)

    # 解析设备名
    device_name = parse_device_name_backend(ua, renderer)
    
    # 虚拟机双重检测 (前端 + 关键词)
    is_vm = 1 if client_vm_flag else 0
    if 'Sim' in device_name or 'Emulator' in device_name:
        is_vm = 1

    # 存入数据库
    db = get_db()
    db.execute(
        'INSERT INTO scores (score, device_name, gpu_renderer, platform, is_vm, ip, ip_display, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (score, device_name, renderer, platform_from_client, is_vm, real_ip, ip_display, int(time.time()))
    )
    db.commit()
    
    return get_rankings()

@app.route('/api/scores')
def scores_route():
    return get_rankings()

def get_rankings():
    db = get_db()
    # 查询 PC 前10
    pc = db.execute('SELECT score, device_name, ip_display, is_vm FROM scores WHERE platform="PC" ORDER BY score DESC LIMIT 10').fetchall()
    # 查询 Mobile 前10
    mobile = db.execute('SELECT score, device_name, ip_display, is_vm FROM scores WHERE platform="MOBILE" ORDER BY score DESC LIMIT 10').fetchall()
    
    return jsonify({
        'pc': [dict(r) for r in pc],
        'mobile': [dict(r) for r in mobile]
    })

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)