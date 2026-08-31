
(function(){
"use strict";
var $=function(s,r){return(r||document).querySelector(s)}, $$=function(s,r){return[].slice.call((r||document).querySelectorAll(s))};
var names={home:"总览",tasks:"任务管理",twin:"数字孪生",robots:"机器人管理",scenes:"场景管理",data:"数据管理",models:"模型管理","model-config":"模型配置",logs:"日志与审计",settings:"系统设置"};
var state={scenario:null,page:"home"};
var livestream={video:null,hls:null,url:"",scene:null,badge:null,overlay:null,status:null};
var apiBase=(typeof window!=="undefined"&&window.DEMO_API_BASE)||"";
if(!apiBase&&typeof window!=="undefined"&&window.location){
  var location=window.location;
  if(location.protocol==="file:")apiBase="http://127.0.0.1:8765";
}
var esc=function(v){return String(v==null?"":v).replace(/[&<>"']/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})};
var text=function(v){return({READY:"就绪",IDLE:"空闲",MAINTENANCE:"维护中",ONLINE:"在线",DEPLOYED:"已部署",CANARY:"灰度中",STAGING:"测试中",PROCESSING:"处理中",REVIEW:"待复核",SUCCEEDED:"成功",RUNNING:"执行中",BLOCKED:"受阻",QUEUED:"排队中"}[v]||v||"—")};
var api=function(p,o){
  o=Object.assign({},o||{});
  var headers=Object.assign({},o.headers||{});
  if(o.body)headers["Content-Type"]="application/json";else delete headers["Content-Type"];
  o.headers=headers;
  return fetch(apiBase+p,o).then(function(r){return r.json().catch(function(){return{}}).then(function(d){if(!r.ok)throw Error(d.error||("接口请求失败 "+r.status));return d})}).catch(function(e){
    if(e&&e.message&&e.message.indexOf("接口请求失败")===0)throw e;
    throw Error("无法连接演示服务（"+apiBase+"），请确认 8765 端口服务已启动");
  });
};
var post=function(p,b){return api(p,{method:"POST",body:JSON.stringify(b)})}, put=function(p,b){return api(p,{method:"PUT",body:JSON.stringify(b)})};
var toast=function(m,c){var n=$("#toast");if(!n)return;n.textContent=m;n.className="toast show "+(c||"");clearTimeout(toast.t);toast.t=setTimeout(function(){n.className="toast"},2500)};
var pill=function(v){return'<span class="unit-pill '+String(v||"").toLowerCase()+'">'+esc(text(v))+"</span>"};
var stat=function(a,b,c,t){return'<article class="unit-stat '+(t||"")+'"><small>'+a+"</small><b>"+b+"</b><span>"+c+"</span></article>"};
function streamStatus(message,kind){
  if(livestream.status){livestream.status.textContent=message;livestream.status.className="stream-status "+(kind||"")}
  if(livestream.overlay)livestream.overlay.hidden=!message;
}
function streamFallback(message){
  if(livestream.hls){livestream.hls.destroy();livestream.hls=null}
  if(livestream.video){livestream.video.pause();livestream.video.removeAttribute("src");livestream.video.load()}
  if(livestream.scene)livestream.scene.classList.remove("stream-active");
  if(livestream.badge)livestream.badge.textContent="● 模拟视图 · 直播不可用";
  streamStatus(message,"error");
}
function streamReady(){
  if(livestream.scene)livestream.scene.classList.add("stream-active");
  if(livestream.badge)livestream.badge.textContent="● 实时仿真 · HLS";
  if(livestream.status)livestream.status.textContent="实时画面已连接";
  if(livestream.overlay)livestream.overlay.hidden=true;
}
function loadHlsScript(done){
  if(window.Hls){done();return}
  var script=document.createElement("script");
  script.src="https://cdn.jsdelivr.net/npm/hls.js@1.5.17/dist/hls.min.js";
  script.onload=done;
  script.onerror=function(){streamFallback("HLS 播放组件加载失败，请检查网络或改用 Safari")};
  document.head.appendChild(script);
}
function attachLivestream(url){
  if(!livestream.video||!url)return;
  livestream.url=url;
  streamStatus("正在连接实时画面…","loading");
  var video=livestream.video;
  video.onloadeddata=streamReady;
  video.onerror=function(){streamFallback("视频流连接失败，请检查 RTSP、OBS 和 MediaMTX")};
  if(video.canPlayType("application/vnd.apple.mpegurl")){
    video.src=url;
    video.play().catch(function(){});
    return;
  }
  loadHlsScript(function(){
    if(!window.Hls||!window.Hls.isSupported()){
      streamFallback("当前浏览器不支持 HLS，请使用 Chrome/Edge 并确保 hls.js 可加载");
      return;
    }
    livestream.hls=new window.Hls({enableWorker:true,lowLatencyMode:true});
    livestream.hls.on(window.Hls.Events.MANIFEST_PARSED,function(){video.play().catch(function(){});});
    livestream.hls.on(window.Hls.Events.ERROR,function(_,data){if(data&&data.fatal)streamFallback("HLS 播放发生致命错误，请点击重试")});
    livestream.hls.loadSource(url);
    livestream.hls.attachMedia(video);
  });
}
function initLivestream(){
  var scene=$("#scene");
  if(!scene)return;
  livestream.scene=scene;
  livestream.badge=$("#streamBadge")||$(".live",scene);
  var video=document.createElement("video");
  video.className="isaac-video";
  video.controls=true;video.autoplay=true;video.muted=true;video.playsInline=true;video.preload="none";
  video.setAttribute("aria-label","Isaac Sim 实时仿真画面");
  scene.insertBefore(video,scene.firstChild);
  livestream.video=video;
  var overlay=document.createElement("div");
  overlay.className="stream-overlay";overlay.hidden=true;
  overlay.innerHTML='<strong>实时仿真画面</strong><span class="stream-status" role="status" aria-live="polite"></span><button type="button" id="streamRetry">重新连接</button>';
  scene.appendChild(overlay);
  livestream.overlay=overlay;livestream.status=$(".stream-status",overlay);
  $("#streamRetry",overlay).onclick=function(){if(livestream.url)attachLivestream(livestream.url)};
  api("/api/livestream").then(function(config){
    if(!config.enabled||!config.url){if(livestream.badge)livestream.badge.textContent="● 模拟视图 · 未配置直播";return}
    attachLivestream(config.url);
  }).catch(function(){if(livestream.badge)livestream.badge.textContent="● 模拟视图 · 直播状态未知"});
}
function init(){nav();home();clock();setInterval(clock,1000);loadHome();initLivestream()}
function clock(){var n=$("#clock");if(n)n.textContent=new Date().toLocaleString("zh-CN",{hour12:false}).replaceAll("/","-")}
function nav(){$$(".nav").forEach(function(b){b.onclick=function(){go(b.dataset.page)}})}
function go(p){if(!names[p])return;state.page=p;$$(".nav").forEach(function(b){b.classList.toggle("active",b.dataset.page===p)});$("#homeView").hidden=p!=="home";$("#unitView").hidden=p==="home";$("#pageKicker").textContent=p==="home"?"工作台 / 总览":"平台单元 / "+names[p];$("#pageTitle").textContent=p==="home"?"任务执行中心":names[p];if(p!=="home")unit(p)}
function health(ok){if($("#health")){$("#health").textContent=ok?"●　服务连接正常":"●　服务连接异常";$("#health").className=ok?"ok":"bad"}if($("#footer"))$("#footer").textContent=ok?"已连接":"异常"}
function home(){
var i=$("#instruction"),c=$("#count");if(i)i.oninput=function(){c.textContent=i.value.length+" / 200"};
if($("#clear"))$("#clear").onclick=function(){i.value="";c.textContent="0 / 200"};
if($("#voice"))$("#voice").onclick=function(){toast("语音输入接口已预留","info")};
if($("#view2d"))$("#view2d").onclick=function(){toast("2D 视图接口已预留","info")};
if($("#full"))$("#full").onclick=function(){$("#scene").classList.toggle("scene-focus")};
if($("#all"))$("#all").onclick=function(){go("tasks")};
if($("#run"))$("#run").onclick=run;
["pause","stop","emergency"].forEach(function(id){if($("#"+id))$("#"+id).onclick=function(){command(id)}})
}
function loadHome(){api("/api/health").then(function(d){health(d.healthy)}).catch(function(){health(false)});api("/api/tasks").then(function(d){recent(d.tasks||[])}).catch(function(){recent([])});api("/api/scenarios").then(function(d){state.scenario=d.scenarios&&d.scenarios[0];if(state.scenario)choose(state.scenario)}).catch(function(){})}
function recent(a){var h=$("#recent");if(!h)return;h.innerHTML=a.slice(0,4).map(function(t,i){return'<button class="recent-item '+(i?"":"selected")+'" data-id="'+esc(t.id)+'"><b>'+esc(t.name)+'</b><em>'+esc(text(t.status))+'</em><small>'+esc(t.focus||t.instruction||"闭环演示")+" · "+esc(t.updated_at)+"</small></button>"}).join("")||"<i>暂无任务</i>";$$(".recent-item",h).forEach(function(n){n.onclick=function(){api("/api/tasks/"+n.dataset.id).then(function(d){choose(d.scenario)})}})}
function choose(s){state.scenario=s;var x=s.scene||s;$("#sceneName").textContent=s.name||x.id||"stacking_cubes";$("#objectCount").textContent=(x.objects||[]).length+" 个";if($("#svg"))$("#svg").innerHTML=sceneSvg(x.objects||[])}
function sceneSvg(a){var s='<rect width="960" height="500" fill="#071321"/><path d="M120 330L770 330 880 420 210 420Z" fill="#14263a" stroke="#294b70"/><path d="M170 350h610M215 375h600M210 420l-80-90M350 420l-20-90M500 420V330M650 420l20-90M790 420l-20-90" stroke="#294b70"/>';a.slice(0,6).forEach(function(o,i){var x=230+i%3*170,y=295-Math.floor(i/3)*55,c=["#378bff","#4bdd91","#f3a33b"][i%3];s+='<rect x="'+x+'" y="'+y+'" width="70" height="42" fill="'+c+'" fill-opacity=".16" stroke="'+c+'"/><text x="'+(x+35)+'" y="'+(y+25)+'" text-anchor="middle" fill="#d9eaff" font-size="12">'+esc(o.label||o.name||("目标"+(i+1)))+"</text>"});return s+'<path d="M500 365L535 280 500 210 530 150" fill="none" stroke="#dcecff" stroke-width="22" stroke-linecap="round"/><circle cx="500" cy="365" r="18" fill="#70879c" stroke="#dcecff" stroke-width="4"/><circle cx="535" cy="280" r="16" fill="#70879c" stroke="#dcecff" stroke-width="4"/><path d="M500 360C600 300 610 250 570 160" fill="none" stroke="#16d6ff" stroke-width="3" stroke-dasharray="8 8"/><circle cx="570" cy="160" r="7" fill="#16d6ff"/>'}
function run(){var i=$("#instruction"),v=i&&i.value.trim()||"把绿色方块放到桌子上";$("#run").disabled=true;post("/api/run",{scene_id:state.scenario&&state.scenario.id||"stacking_cubes",instruction:v}).then(function(d){var r=d.result||{},p=r.status==="SUCCEEDED"?100:42;$("#progress").textContent=p+"%";$("#runtimeState").textContent=text(r.status);$("#taskName").textContent=v;$("#flowStatus").textContent=p===100?"● 已完成":"● 执行中";toast("任务已返回 "+text(r.status),"success")}).catch(function(e){toast(e.message,"bad")}).finally(function(){$("#run").disabled=false})}
function command(id){var c=id==="pause"?"PAUSE":id==="stop"?"STOP":"ESTOP";post("/api/robots/RBT-001/commands",{command:c}).then(function(){toast(c+" 命令已发送","info")}).catch(function(e){toast(e.message,"bad")})}
function shell(k,t,d,a,b){return'<div class="unit-shell"><header class="unit-head"><div><h2>'+t+"</h2><p>"+d+'</p></div><div class="unit-actions">'+(a||"")+'</div></header><div class="unit-body">'+b+"</div></div>"}
function unit(p){var h=$("#unitView");h.innerHTML='<div class="unit-loading">正在加载'+names[p]+"…</div>";load(p).then(function(d){h.innerHTML=page(p,d);bind(p,d)}).catch(function(e){h.innerHTML='<div class="unit-error"><b>页面数据暂不可用</b><p>'+esc(e.message)+'</p></div>'})}
function load(p){
if(p==="tasks")return api("/api/tasks").then(function(d){return d.tasks||[]});
if(p==="robots")return Promise.all([api("/api/robots"),api("/api/robots/RBT-001/telemetry")]).then(function(a){return{robots:a[0].robots||[],telemetry:a[1].telemetry||{}}});
if(p==="twin")return api("/api/scenes").then(function(d){var a=d.scenes||[];return(a[0]?api("/api/scenes/"+a[0].id):Promise.resolve({})).then(function(x){return{scenes:a,scene:x.scene||{}}})});
if(p==="scenes")return api("/api/scenes").then(function(d){return d.scenes||[]});
if(p==="data")return api("/api/datasets").then(function(d){return d.datasets||[]});
if(p==="models")return api("/api/models").then(function(d){return d.models||[]});
if(p==="model-config")return api("/api/model-config").then(function(d){return d.config||{}});
if(p==="logs")return Promise.all([api("/api/logs"),api("/api/audit")]).then(function(a){return{logs:a[0].logs||[],audit:a[1].records||[]}});
return Promise.all([api("/api/settings"),api("/api/permissions")]).then(function(a){return{settings:a[0].settings||{},roles:a[1].roles||[]}})
}
function page(p,d){if(p==="tasks")return tasks(d);if(p==="twin")return twin(d);if(p==="robots")return robots(d);if(p==="scenes")return scenes(d);if(p==="data")return data(d);if(p==="models")return models(d);if(p==="model-config")return modelConfig(d);if(p==="logs")return logs(d);return settings(d)}
function tasks(a){var rows=a.map(function(t){return'<button class="task-row" data-task="'+esc(t.id)+'"><span class="task-index">'+esc(t.id.slice(-3))+'</span><span class="task-main"><b>'+esc(t.name)+'</b><small>'+esc(t.instruction||t.focus)+'</small></span>'+pill(t.status)+'<strong>'+t.progress+'%</strong></button>'}).join("");return shell("MISSION CONTROL","任务管理","统一查看任务队列、执行状态和闭环结果。",'<button class="unit-secondary" data-action="refresh">刷新</button><button class="unit-primary" data-action="new-task">新建任务</button>','<div class="unit-stats">'+stat("任务总数",a.length,"场景任务")+stat("执行中",a.filter(function(x){return x.status==="RUNNING"}).length,"当前队列","good")+stat("已完成",a.filter(function(x){return x.status==="SUCCEEDED"}).length,"成功闭环","good")+stat("接口","POST","/api/tasks")+'</div><div class="unit-grid task-unit"><article class="unit-panel"><header><b>任务队列</b><span>按更新时间排序</span></header><div class="task-rows">'+rows+'</div></article><article class="unit-panel"><header><b>任务详情</b><span>运行链路</span></header><div class="task-detail"><div class="empty-detail">选择左侧任务查看详情</div></div></article><article class="unit-panel"><header><b>后端契约</b><span>已预留</span></header><div class="contract-list"><p><i></i><b>创建任务</b><code>POST /api/tasks</code></p><p><i></i><b>执行闭环</b><code>POST /api/run</code></p><p><i></i><b>实时事件</b><code>WS /api/events</code></p></div></article></div>')}
function twin(d){var s=d.scene||{},a=s.objects||[];return shell("DIGITAL TWIN","数字孪生","在统一工作台查看场景、对象、轨迹和仿真接入状态.",'<button class="unit-secondary" data-action="refresh">刷新场景</button><button class="unit-secondary" data-action="2d">2D 视图</button><button class="unit-primary" data-action="simulate">启动仿真</button>','<div class="unit-grid twin-unit"><article class="unit-panel twin-panel"><header><b>'+esc(s.name||"工作站数字孪生")+'</b><span class="live-dot">● 场景在线</span></header><div class="twin-stage"><svg viewBox="0 0 960 460">'+sceneSvg(a)+'</svg></div><footer class="twin-footer"><span>对象 <b>'+a.length+'</b></span><span>坐标系 <b>世界</b></span><span>轨迹 <b>待接入</b></span><span>引擎 <b>Sim-RTX</b></span></footer></article><aside class="unit-panel"><header><b>场景对象</b><span>'+a.length+" 个</span></header><div class=\"object-list\">"+a.map(function(o,i){return'<div><i class="object-dot dot-'+i%4+'"></i><b>'+esc(o.label||o.name||("目标"+(i+1)))+'</b><span>'+esc(({object:"对象",target:"目标",robot:"机器人",workpiece:"工件",bin:"料箱",table:"工作台"}[o.type]||o.type||"对象"))+'</span><em>检测到</em></div>'}).join("")+'</div><header class="subhead"><b>接口状态</b></header><div class="contract-list"><p><i></i><b>场景详情</b><code>GET /api/scenes/{id}</code></p><p><i></i><b>真实仿真</b><code>Isaac Sim / Unity</code></p><p><i></i><b>视频流</b><code>WebRTC / RTSP</code></p></div></aside></div>')}
function robots(d){var a=d.robots||[],t=d.telemetry||{},j=t.joints||[];return shell("FLEET CONTROL","机器人管理","维护机器人资产、连接状态、关节遥测与安全控制。",'<button class="unit-secondary" data-action="refresh">刷新遥测</button><button class="unit-primary" data-action="robot-command">发送自检</button>','<div class="unit-stats">'+stat("机器人",a.length,"资产总数")+stat("在线",a.filter(function(x){return x.status==="READY"||x.status==="IDLE"}).length,"可调度","good")+stat("维护中",a.filter(function(x){return x.status==="MAINTENANCE"}).length,"需要关注","warn")+stat("实时频率","30 FPS","遥测接口")+'</div><div class="unit-grid robots-unit"><article class="unit-panel"><header><b>机器人资产</b><span>选择设备查看详情</span></header><div class="fleet-list">'+a.map(function(r,i){return'<button class="fleet-card '+(i?"":"selected")+'"><i class="robot-avatar">⌁</i><span><b>'+esc(r.name)+'</b><small>'+esc(r.model)+" · "+esc(r.ip)+"</small></span>"+pill(r.status)+"</button>"}).join("")+'</div></article><article class="unit-panel"><header><b>关节遥测</b><span>RBT-001</span></header><div class="telemetry-table"><div class="telemetry-head"><span>关节</span><span>位置</span><span>负载</span><span>速度</span><span>温度</span></div>'+j.map(function(x){return'<div class="telemetry-row"><b>'+esc(x.name)+'</b><span>'+Number(x.position).toFixed(2)+'°</span><i><em style="width:'+x.load*4+'%"></em></i><span>'+Number(x.velocity).toFixed(1)+'°/s</span><span>'+x.temperature+'°C</span></div>'}).join("")+'</div></article><article class="unit-panel"><header><b>安全状态</b><span class="live-dot">● 正常</span></header><div class="safety-cards"><p>急停状态<strong>未触发</strong></p><p>防护门<strong>'+text(t.safety&&t.safety.door)+'</strong></p><p>碰撞检测<strong>'+text(t.safety&&t.safety.collision)+'</strong></p><p>速度倍率<strong>100%</strong></p></div><div class="api-note">实时接口<br><code>GET /api/robots/{id}/telemetry</code></div></article></div>')}
function scenes(a){return shell("SCENE REGISTRY","场景管理","管理工作站、仿真环境和场景版本，作为数字孪生和任务执行的输入。",'<button class="unit-secondary" data-action="refresh">刷新目录</button><button class="unit-primary" data-action="new-scene">新建场景</button>','<div class="unit-stats">'+stat("场景总数",a.length,"已登记环境")+stat("在线",a.filter(function(x){return x.status==="ONLINE"}).length,"可用于执行","good")+stat("对象",a.reduce(function(n,x){return n+Number(x.objects||0)},0),"场景对象")+stat("接口","REST","/api/scenes")+'</div><div class="unit-grid scenes-unit"><article class="unit-panel"><header><b>场景目录</b><span>版本 / 状态</span></header><div class="scene-library">'+a.map(function(s,i){return'<button class="scene-card '+(i?"":"selected")+'"><span class="scene-thumb">◇</span><span><b>'+esc(s.name)+'</b><small>'+esc(s.id)+" · rev "+esc(s.revision)+"</small></span>"+pill(s.status)+"<strong>"+s.objects+" 对象</strong></button>"}).join("")+'</div></article><article class="unit-panel scene-summary"><header><b>场景摘要</b><span>stacking_cubes</span></header><div class="scene-preview"><div class="preview-grid"></div><span>3D 场景<br><small>仿真画面占位</small></span></div><div class="summary-list"><p>用途<strong>抓取与叠放</strong></p><p>坐标系<strong>世界坐标系</strong></p><p>资源<strong>RGB-D / 轨迹</strong></p></div></article><article class="unit-panel"><header><b>场景操作</b><span>占位</span></header><div class="action-stack"><button data-action="scene-edit">编辑当前场景</button><button data-action="scene-publish">发布新版本</button><button data-action="scene-export">导出场景 JSON</button></div><div class="api-note">新增场景<br><code>POST /api/scenes</code></div></article></div>')}
function data(a){var rows=a.map(function(x){return'<div class="data-row"><b>'+esc(x.name)+'</b><span>'+esc(x.type)+'</span><strong>'+Number(x.records).toLocaleString()+'</strong><i><em style="width:'+x.quality+'%"></em></i><span>'+pill(x.status)+'</span><small>'+esc(x.updated_at)+'</small></div>'}).join("");return shell("DATA HUB","数据管理","查看感知、轨迹、任务反馈等数据资产的规模、质量与处理状态。",'<button class="unit-secondary" data-action="refresh">刷新数据</button><button class="unit-primary" data-action="new-dataset">导入数据</button>','<div class="unit-stats">'+stat("数据集",a.length,"资产总数")+stat("记录",a.reduce(function(n,x){return n+x.records},0).toLocaleString(),"累计样本")+stat("平均质量",Math.round(a.reduce(function(n,x){return n+x.quality},0)/Math.max(1,a.length))+"%","质量评分","good")+stat("处理中",a.filter(function(x){return x.status==="PROCESSING"}).length,"数据管线","warn")+'</div><div class="unit-grid data-unit"><article class="unit-panel"><header><b>数据资产</b><span>按质量评分排序</span></header><div class="data-table"><div class="data-head"><span>名称</span><span>类型</span><span>记录数</span><span>质量</span><span>状态</span><span>更新时间</span></div>'+rows+'</div></article><article class="unit-panel"><header><b>数据管线</b><span>数据管线</span></header><div class="pipeline"><p><i class="done"></i><b>采集接入</b><span>已完成</span></p><p><i class="done"></i><b>清洗与校验</b><span>已完成</span></p><p><i class="active"></i><b>标注 / 特征</b><span>处理中</span></p><p><i></i><b>训练集发布</b><span>待执行</span></p></div><div class="api-note">数据服务<br><code>GET /api/datasets</code></div></article></div>')}
function models(a){return shell("MODEL REGISTRY","模型管理","管理感知、理解、规划和安全策略模型的版本与发布状态。",'<button class="unit-secondary" data-action="refresh">刷新模型</button><button class="unit-primary" data-action="new-model">注册模型</button>','<div class="unit-stats">'+stat("模型版本",a.length,"已注册")+stat("已部署",a.filter(function(x){return x.status==="DEPLOYED"}).length,"生产环境","good")+stat("平均准确率",(a.reduce(function(n,x){return n+x.accuracy},0)/Math.max(1,a.length)).toFixed(1)+"%","离线评估")+stat("灰度发布",a.filter(function(x){return x.status==="CANARY"}).length,"待观察","warn")+'</div><div class="unit-grid models-unit"><article class="unit-panel"><header><b>模型版本</b><span>发布状态</span></header><div class="model-table"><div class="model-head"><span>模型</span><span>类型</span><span>版本</span><span>准确率</span><span>状态</span><span>更新时间</span></div>'+a.map(function(x){return'<div class="model-row"><b>'+esc(x.name)+'</b><span>'+esc(x.type)+'</span><strong>'+esc(x.version)+'</strong><i>'+x.accuracy+'%</i><span>'+pill(x.status)+'</span><small>'+esc(x.updated_at)+'</small></div>'}).join("")+'</div></article><article class="unit-panel"><header><b>发布策略</b><span>发布管理</span></header><div class="release-card"><div class="release-icon">◈</div><h3>安全发布门禁</h3><p>上线前校验模型精度、资源占用和回滚版本。</p><div class="release-check"><span>离线评估 <b>通过</b></span><span>灰度范围 <b>10%</b></span><span>回滚版本 <b>已保留</b></span></div><button data-action="deploy">查看发布流程</button></div><div class="api-note">模型服务<br><code>GET /api/models</code></div></article></div>')}
function logs(d){var a=d.logs||[],b=d.audit||[];return shell("OBSERVABILITY","日志与审计","聚合系统事件、任务执行记录和关键操作审计，帮助定位闭环异常。",'<button class="unit-secondary" data-action="refresh">刷新日志</button><button class="unit-primary" data-action="export-logs">导出记录</button>','<div class="unit-stats">'+stat("事件",a.length,"最近 24 小时")+stat("成功",a.filter(function(x){return x.level==="SUCCESS"}).length,"执行事件","good")+stat("告警",a.filter(function(x){return x.level==="WARNING"}).length,"需要关注","warn")+stat("审计记录",b.length,"操作留痕")+'</div><div class="unit-grid logs-unit"><article class="unit-panel"><header><b>事件时间线</b><span>最近事件</span></header><div class="timeline-panel">'+a.map(function(x){return'<div class="timeline-row"><time>'+esc(x.time)+'</time><i class="'+String(x.level).toLowerCase()+'"></i><div><b>'+esc(x.title)+'</b><small>'+esc(x.detail)+'</small></div><span>'+esc(x.source)+'</span></div>'}).join("")+'</div></article><article class="unit-panel"><header><b>操作审计</b><span>只读记录</span></header><div class="audit-list">'+b.map(function(x){return'<div class="audit-row"><i></i><div><b>'+esc(x.title)+'</b><small>'+esc(x.detail)+'</small></div><time>'+esc(x.time)+'</time></div>'}).join("")+'</div><div class="api-note">实时流<br><code>WS /api/events</code></div></article></div>')}

function modelConfig(d){
  var modules=d.modules||{}, ids=["A","B","C","D"], smartCount=ids.filter(function(id){return (modules[id]||{}).mode==="smart"}).length, keyCount=ids.filter(function(id){return (modules[id]||{}).api_key_configured}).length;
  var cards=ids.map(function(id){
    var x=modules[id]||{}, isC=id==="C";
    var option=function(v,label){return'<option value="'+v+'"'+(x.mode===v?" selected":"")+'>'+label+"</option>"};
    var fields=isC
      ? '<div class="module-backend-fields"><label>执行后端<input data-field="backend" value="'+esc(x.backend||"mock")+'" placeholder="例如：本地模拟 / 仿真引擎 / 真实机器人"></label><p class="config-hint">C 当前默认使用本地模拟执行器；真实仿真或真机通道可在此预留。</p></div>'
      : '<div class="module-smart-fields" data-smart-fields><label>服务商<input data-field="provider" value="'+esc(x.provider||"")+'" placeholder="例如：DeepSeek / 兼容接口"></label><label>模型名称<input data-field="model" value="'+esc(x.model||"")+'" placeholder="例如：deepseek-v4-flash"></label><label>接口地址<input data-field="base_url" value="'+esc(x.base_url||"")+'" placeholder="https://api.example.com/v1"></label><label>API Key<input type="password" data-field="api_key" value="" placeholder="'+esc(x.api_key_configured?"已配置，留空保持不变":"请输入 API Key")+'" autocomplete="new-password"></label></div>';
    return '<article class="module-card module-'+id+'" data-module="'+id+'"><header><span class="module-code">'+id+'</span><div><b>'+esc(x.name||id)+'</b></div><span class="module-state">'+(x.mode==="smart"?"智能":"本地")+'</span></header><label class="mode-label">运行模式<select class="module-mode" data-field="mode">'+option("rule","规则模式")+option("mock","MOC 模式")+option("smart","智能模式")+'</select></label>'+fields+'<footer><span>'+esc(x.provider||"本地适配器")+'</span><em>'+(x.api_key_configured?"Key 已配置":"无需 Key / 未配置")+'</em></footer></article>';
  }).join("");
  return shell("MODEL CONFIGURATION","模型配置","手动选择 A / B / C / D 模块运行模式；保存后固定写入本地配置文件，刷新和重启服务均不会重置。",'<button class="unit-secondary" data-action="refresh-model-config">重新读取</button><button class="unit-primary" data-action="save-model-config">保存配置</button>','<div class="unit-stats">'+stat("配置模块",ids.length,"A / B / C / D")+stat("智能模式",smartCount,"需要模型配置","good")+stat("凭证状态",keyCount+"/3","A / B / D")+stat("持久化","已启用",".model_config.local.json","good")+'</div><div class="unit-grid model-config-unit"><article class="unit-panel config-board"><header><b>模块模式配置</b><span>未填写的新 Key 会保留原配置</span></header><div class="module-grid">'+cards+'</div></article><aside class="unit-panel config-help"><header><b>配置说明</b><span>安全提示</span></header><div class="config-help-body"><p><i class="good-dot"></i><b>默认配置</b><small>A：DeepSeek 智能　B：CodeArts 智能　C：MOC　D：TraceCoder 智能</small></p><p><i class="blue-dot"></i><b>智能模式</b><small>A / B / D 可输入第三方模型名称、接口地址和 API Key。</small></p><p><i class="orange-dot"></i><b>固定写入</b><small>配置保存在本机根目录的 .model_config.local.json，已加入 Git 忽略。</small></p><p><i class="gray-dot"></i><b>Key 安全</b><small>页面只显示脱敏状态，后端 GET 接口不会返回原始 API Key。</small></p></div><div class="api-note">配置接口<br><code>GET /api/model-config</code><br><code>PUT /api/model-config</code></div></aside></div>');
}

function settings(d){var s=d.settings||{};return shell("SYSTEM CONTROL","系统设置","配置运行模式、默认资源、安全策略与前端对接参数。",'<button class="unit-secondary" data-action="reset-settings">恢复默认</button><button class="unit-primary" data-action="save-settings">保存设置</button>','<div class="unit-grid settings-unit"><article class="unit-panel"><header><b>运行参数</b><span>配置项</span></header><div class="settings-form"><label>运行模式<select name="runtime_mode"><option value="AUTO">自动执行</option><option value="MANUAL">手动确认</option></select></label><label>默认机器人<select name="default_robot"><option>RBT-001</option><option>RBT-002</option></select></label><label>默认场景<select name="default_scene"><option value="stacking_cubes">叠放方块</option><option value="sorting_workcell">分拣工作站</option></select></label><label>会话超时（分钟）<input name="session_timeout" type="number" value="'+esc(s.session_timeout)+'"></label><label class="toggle-row"><span>安全控制门禁</span><input name="safe_control" type="checkbox"'+(s.safe_control?" checked":"")+'></label><label class="toggle-row"><span>开启审计留痕</span><input name="audit_enabled" type="checkbox"'+(s.audit_enabled?" checked":"")+'></label></div></article><article class="unit-panel"><header><b>接口与服务</b><span>当前连接</span></header><div class="service-list"><p><b>基础 API</b><code>'+esc(s.api_base)+'</code><span class="live-dot">● 已连接</span></p><p><b>事件通道</b><code>'+esc(s.event_stream)+'</code><span>待接入</span></p><p><b>视频遥测</b><code>GET /api/robots/{id}</code><span>占位</span></p><p><b>真实仿真</b><code>Isaac Sim / Unity</code><span>占位</span></p></div></article><article class="unit-panel"><header><b>角色权限</b><span>RBAC</span></header><div class="role-list">'+(d.roles||[]).map(function(x){return'<div><i>R</i><b>'+esc(x.name)+'</b><span>'+x.permissions+" 项权限</span></div>"}).join("")+'</div><div class="api-note">权限接口<br><code>GET /api/permissions</code></div></article></div>')}
function bind(p,d){var h=$("#unitView"),n;
n=$("[data-action=refresh]",h);if(n)n.onclick=function(){unit(p)};
n=$("[data-action=new-task]",h);if(n)n.onclick=function(){var v=prompt("任务名称","新建抓取任务");if(v)post("/api/tasks",{name:v,instruction:v}).then(function(){toast("任务已创建","success");unit(p)})};
$$(".task-row",h).forEach(function(x){x.onclick=function(){var t=d.filter(function(q){return q.id===x.dataset.task})[0];$(".task-detail",h).innerHTML='<div class="detail-overview"><div><small>'+esc(t.id)+'</small><h3>'+esc(t.name)+'</h3><p>'+esc(t.instruction||t.focus)+'</p></div>'+pill(t.status)+'</div><div class="detail-progress"><span style="width:'+t.progress+'%"></span></div><div class="mini-flow"><b>感知</b><i>→</i><b>规划</b><i>→</i><b>执行</b><i>→</i><b>验证</b></div><button class="unit-primary" data-run-detail>执行任务</button>'; $("[data-run-detail]",h).onclick=function(){post("/api/run",{scene_id:t.id,instruction:t.instruction||t.name}).then(function(){toast("任务已提交执行","success")})}}});
n=$('[data-action="2d"]',h);if(n)n.onclick=function(){toast("2D 视图接口已预留","info")};
n=$("[data-action=simulate]",h);if(n)n.onclick=function(){toast("仿真引擎接口已预留","info")};
n=$("[data-action=robot-command]",h);if(n)n.onclick=function(){post("/api/robots/RBT-001/commands",{command:"SELF_CHECK"}).then(function(){toast("自检命令已进入队列","success")})};
["new-scene","new-dataset","new-model","scene-edit","scene-publish","scene-export","deploy","export-logs"].forEach(function(a){n=$("[data-action="+a+"]",h);if(n)n.onclick=function(){toast("该能力已保留接口位置","info")}});

$$(".module-mode",h).forEach(function(sel){var sync=function(){var card=sel.closest(".module-card"),fields=$("[data-smart-fields]",card),stateNode=$(".module-state",card);if(fields)fields.hidden=sel.value!=="smart";if(stateNode)stateNode.textContent=sel.value==="smart"?"智能":"本地"};sel.onchange=sync;sync()});
n=$("[data-action=refresh-model-config]",h);if(n)n.onclick=function(){unit("model-config")};
n=$("[data-action=save-model-config]",h);if(n)n.onclick=function(){
  var modules={}, value=function(card,field){var node=$("[data-field="+field+"]",card);return node?node.value:""};
  $$(".module-card",h).forEach(function(card){
    var id=card.dataset.module, item={mode:value(card,"mode"),provider:value(card,"provider"),model:value(card,"model"),base_url:value(card,"base_url"),api_key:value(card,"api_key")};
    if(id==="C")item.backend=value(card,"backend");
    modules[id]=item;
  });
  put("/api/model-config",{modules:modules}).then(function(){toast("A / B / C / D 模型配置已固定保存","success");unit("model-config")}).catch(function(e){toast(e.message,"bad")});
};

n=$("[data-action=save-settings]",h);if(n)n.onclick=function(){var body={},form=$(".settings-form",h);$$("[name]",form).forEach(function(x){body[x.name]=x.type==="checkbox"?x.checked:x.type==="number"?Number(x.value):x.value});put("/api/settings",body).then(function(){toast("设置已保存","success")}).catch(function(e){toast(e.message,"bad")})};
n=$("[data-action=reset-settings]",h);if(n)n.onclick=function(){unit("settings")}
}
document.addEventListener("DOMContentLoaded",init)
}());
