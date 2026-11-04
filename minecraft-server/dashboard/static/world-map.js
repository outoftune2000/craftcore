/* Height-based saved-world map. No game-client mod or external renderer required. */
(() => {
  const $ = id => document.getElementById(id);
  const EMPTY = -32768, COS = Math.sqrt(3)/2;
  const tiles = new Map(), inflight = new Map(), failed = new Map();
  let canvas, ctx, width=0, height=0, mode='angled', rotation=0, initialized=false;
  let frame=0, timer=0, sequence=0, loading=false, error='', lastUpdated=0, didCenter=false;
  let sceneDirty=true, backing=document.createElement('canvas'), hasScene=false, sceneCamera=null;
  let desired=[], rendered=[], hover=null, refreshTimer, resizeObserver;
  let viewByDimension={}, storageTimer, pinch=null;
  const pointers=new Map();
  function canonical(value) { return String(value||'overworld').replace('minecraft:','').replace(/^nether$/,'the_nether').replace(/^end$/,'the_end'); }
  function visible() { return $('tab-map')?.classList.contains('active'); }
  function rotate(x,z) { switch(rotation){case 1:return [-z,x];case 2:return [-x,-z];case 3:return [z,-x];default:return [x,z];} }
  function unrotate(x,z) { switch(rotation){case 1:return [z,-x];case 2:return [-x,-z];case 3:return [-z,x];default:return [x,z];} }
  function project(x,z,y=Math.min(64,mapState.maxLayerY)) {
    const [u,v]=rotate(x-mapState.centerX,z-mapState.centerZ), scale=mapState.zoom;
    if(mode==='flat')return [width/2+u*scale,height/2+v*scale];
    return [width/2+(u-v)*COS*scale,height/2+(u+v)*.5*scale-(y-Math.min(64,mapState.maxLayerY))*.72*scale];
  }
  function unproject(x,y) {
    const sx=(x-width/2)/mapState.zoom,sy=(y-height/2)/mapState.zoom;
    const [u,v]=mode==='flat'?[sx,sy]:[(sx/COS+sy/.5)/2,(sy/.5-sx/COS)/2];
    const [dx,dz]=unrotate(u,v);
    return [mapState.centerX+dx,mapState.centerZ+dz];
  }
  function drawSoon(changed=true) { if(changed)sceneDirty=true; if(!frame)frame=requestAnimationFrame(()=>{frame=0;draw();}); }
  function saveView() {
    if(!didCenter)return;
    clearTimeout(storageTimer);storageTimer=setTimeout(()=>{
      viewByDimension[mapState.currentDim]={x:mapState.centerX,z:mapState.centerZ,zoom:mapState.zoom,layer:mapState.maxLayerY};
      try{localStorage.setItem('craftcore-world-map-v1',JSON.stringify({mode,rotation,views:viewByDimension}));}catch{}
    },200);
  }
  function detail() { const z=mapState.zoom;return z<.45?32:z<1.1?16:z<2.4?8:z<5?4:2; }
  function tileKey(region,step=detail()) {return [mapState.currentDim,mapState.maxLayerY,step,region.rx,region.rz,region.revision].join('|');}
  function selectRegions() {
    const corners=[[0,0],[width,0],[0,height],[width,height]].map(([x,y])=>unproject(x,y));
    const pad=mode==='angled'?320:32;
    const minX=Math.min(...corners.map(p=>p[0]))-pad,maxX=Math.max(...corners.map(p=>p[0]))+pad;
    const minZ=Math.min(...corners.map(p=>p[1]))-pad,maxZ=Math.max(...corners.map(p=>p[1]))+pad;
    return (mapData.regions||[]).filter(r=>r.maxX>=minX&&r.minX<=maxX&&r.maxZ>=minZ&&r.minZ<=maxZ)
      .sort((a,b)=>Math.hypot(a.minX+256-mapState.centerX,a.minZ+256-mapState.centerZ)-Math.hypot(b.minX+256-mapState.centerX,b.minZ+256-mapState.centerZ)).slice(0,12);
  }
  function updateStatus() {
    const pending=desired.filter(r=>!tiles.has(tileKey(r))).length;
    $('terrain-status').textContent=error?error:loading?'Reading world metadata…':pending?`Loading terrain · ${desired.length-pending}/${desired.length} regions`:
      !mapData.regions?.length?'No saved terrain in this dimension':!desired.length?'Outside saved terrain · try Spawn or a waypoint':`${mode==='angled'?'Angled relief':'Top-down'} · ${detail()} blocks / sample`;
    $('terrain-status').dataset.error=error?'true':'false';
    $('map-saved-at').textContent=lastUpdated?`Checked ${new Date(lastUpdated).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'})}`:'Saved world data';
    $('map-zoom-level').textContent=`${mapState.zoom.toFixed(2)}×`;
    $('map-view-angled').setAttribute('aria-pressed',String(mode==='angled'));
    $('map-view-flat').setAttribute('aria-pressed',String(mode==='flat'));
    $('map-grid-toggle').setAttribute('aria-pressed',String(mapState.showGrid));
    $('map-orientation').textContent=(mode==='flat'?['N ↑','N →','N ↓','N ←']:['N ↗','N ↘','N ↙','N ↖'])[rotation];
    $('map-layer-display').textContent=`Y ≤ ${mapState.maxLayerY}`;
    $('map-layer-slider').value=mapState.maxLayerY;
    document.querySelectorAll('.map-dim-btn').forEach(b=>b.classList.toggle('active',canonical(b.dataset.dim)===mapState.currentDim));
    document.querySelectorAll('.btn-preset').forEach(b=>b.classList.toggle('active',Number(b.dataset.y)===mapState.maxLayerY));
  }
  function fetchTiles() {
    if(!visible())return;
    desired=selectRegions();const step=detail(),layer=mapState.maxLayerY,dim=mapState.currentDim;
    for(const [key,entry] of inflight)if(!desired.some(r=>tileKey(r)===key))entry.controller.abort();
    for(const region of desired) {
      if(inflight.size>=3)break;
      const key=tileKey(region,step);
      if(tiles.has(key)||inflight.has(key)||(failed.get(key)||0)>Date.now())continue;
      const controller=new AbortController();inflight.set(key,{controller});
      const query=new URLSearchParams({dim,rx:region.rx,rz:region.rz,step,layer,revision:region.revision});
      fetch('/api/map/terrain?'+query,{signal:controller.signal}).then(async response=>{
        if(!response.ok)throw new Error(response.status===404?'No readable terrain in this region':`Terrain request failed (${response.status})`);
        const tile=await response.json();
        tiles.delete(key);tiles.set(key,tile);
        while(tiles.size>24)tiles.delete(tiles.keys().next().value);
        if(tile.skipped_chunks)error=`Some chunks could not be read (${tile.skipped_chunks}). Retry refresh after a save.`;
      }).catch(e=>{if(e.name!=='AbortError'){failed.set(key,Date.now()+15000);error=e.message;}})
        .finally(()=>{inflight.delete(key);drawSoon();fetchTiles();});
    }
    updateStatus();
  }
  function requestView() { drawSoon();clearTimeout(timer);timer=setTimeout(fetchTiles,100);saveView(); }
  function polygon(points,color) {
    ctx.fillStyle=color;ctx.beginPath();ctx.moveTo(...points[0]);for(const p of points.slice(1))ctx.lineTo(...p);ctx.closePath();ctx.fill();
  }
  function shade(color,factor=1) {return `rgb(${Math.min(255,Math.round((color>>16&255)*factor))},${Math.min(255,Math.round((color>>8&255)*factor))},${Math.min(255,Math.round((color&255)*factor))})`;}
  function chooseTile(region) {
    const exact=tiles.get(tileKey(region));if(exact)return exact;
    let best;
    for(const tile of tiles.values())if(tile.dimension===mapState.currentDim&&tile.layer===mapState.maxLayerY&&tile.rx===region.rx&&tile.rz===region.rz&&(!best||tile.step<best.step))best=tile;
    return best;
  }
  function drawGrid() {
    if(!mapState.showGrid)return;
    const gap=mapState.zoom>2?16:mapState.zoom>.6?64:256;
    const radius=Math.max(width,height)/mapState.zoom+256;
    const x0=Math.floor((mapState.centerX-radius)/gap)*gap,z0=Math.floor((mapState.centerZ-radius)/gap)*gap;
    ctx.strokeStyle='#17218a';ctx.globalAlpha=.35;ctx.lineWidth=1;
    for(let x=x0;x<=mapState.centerX+radius;x+=gap){ctx.beginPath();ctx.moveTo(...project(x,mapState.centerZ-radius));ctx.lineTo(...project(x,mapState.centerZ+radius));ctx.stroke();}
    for(let z=z0;z<=mapState.centerZ+radius;z+=gap){ctx.beginPath();ctx.moveTo(...project(mapState.centerX-radius,z));ctx.lineTo(...project(mapState.centerX+radius,z));ctx.stroke();}
    ctx.globalAlpha=1;
  }
  function playerName(p){return /^[0-9a-f-]{36}$/i.test(p.name)?'Player '+p.name.slice(0,8):p.name;}
  function drawMarkers() {
    const dim=mapState.currentDim;
    const pins=(mapData.waypoints||[]).filter(p=>canonical(p.dimension)===dim).map(p=>({...p,kind:'pin'}));
    const players=(mapData.players||[]).filter(p=>canonical(p.dimension)===dim).map(p=>({x:p.pos.x,y:p.pos.y,z:p.pos.z,name:playerName(p),color:p.is_online?'#00ff66':'#b9a85d',kind:'player'}));
    for(const p of [...pins,...players]) {
      const [x,y]=project(p.x,p.z,Number(p.y??64));if(x<0||x>width||y<0||y>height)continue;
      ctx.save();ctx.globalAlpha=(p.y??64)>mapState.maxLayerY ? .35 : 1;
      ctx.strokeStyle=p.color||'#ffcc00';ctx.fillStyle=p.color||'#ffcc00';ctx.lineWidth=2;
      ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(x,y-17);ctx.stroke();
      ctx.fillRect(x-4,y-22,8,8);ctx.strokeStyle='#050505';ctx.strokeRect(x-4,y-22,8,8);
      ctx.font='600 11px Inter,system-ui';const rawLabel=String(p.name),label=rawLabel.length>22?rawLabel.slice(0,20)+'…':rawLabel,length=ctx.measureText(label).width;
      ctx.fillStyle='#101014';ctx.fillRect(x-length/2-6,y-42,length+12,17);
      ctx.fillStyle=p.kind==='player'?p.color:'#fff7d6';ctx.textAlign='center';ctx.fillText(label,x,y-30);ctx.restore();
    }
  }
  function draw() {
    if(!initialized||!visible()||!width)return;
    if(pointers.size===1&&hasScene&&sceneCamera&&sceneCamera.zoom===mapState.zoom&&sceneCamera.mode===mode&&sceneCamera.rotation===rotation&&performance.now()-sceneCamera.at<120){const anchor=project(sceneCamera.x,sceneCamera.z);ctx.clearRect(0,0,width,height);ctx.drawImage(backing,anchor[0]-width/2,anchor[1]-height/2,width,height);return;}
    if(!sceneDirty&&hasScene){ctx.clearRect(0,0,width,height);ctx.drawImage(backing,0,0,width,height);drawHover();return;}
    sceneDirty=false;
    ctx.clearRect(0,0,width,height);ctx.fillStyle='#050505';ctx.fillRect(0,0,width,height);drawGrid();
    desired=selectRegions();const cells=[],tileIndex=new Map();rendered=[];
    for(const r of desired){const tile=chooseTile(r);if(tile)tileIndex.set(`${r.rx},${r.rz}`,tile);}
    function neighborHeight(x,z){const rx=Math.floor(x/512),rz=Math.floor(z/512),tile=tileIndex.get(`${rx},${rz}`);if(!tile)return undefined;const h=tile.heights[Math.floor((z-rz*512)/tile.step)*tile.size+Math.floor((x-rx*512)/tile.step)];return h===EMPTY?undefined:h;}
    for(const region of desired){
      const tile=chooseTile(region);if(!tile)continue;
      const {size,step}=tile;
      for(let i=0;i<tile.heights.length;i++){
        const y=tile.heights[i];if(y===EMPTY)continue;
        const x=tile.rx*512+(i%size)*step,z=tile.rz*512+Math.floor(i/size)*step;
        const mid=project(x+step/2,z+step/2,y),extent=step*mapState.zoom*2;
        if(mid[0]<-extent||mid[0]>width+extent||mid[1]<-extent||mid[1]>height+extent+160)continue;
        const top=[[x,z],[x+step,z],[x+step,z+step],[x,z+step]].map(p=>project(...p,y));
        if(Math.max(...top.map(p=>p[0]))<0||Math.min(...top.map(p=>p[0]))>width||Math.max(...top.map(p=>p[1]))<0||Math.min(...top.map(p=>p[1]))>height+160)continue;
        const [u,v]=rotate(x+step/2-mapState.centerX,z+step/2-mapState.centerZ);
        cells.push({x,z,y,step,top,color:tile.colors[i],depth:mode==='flat'?0:u+v});
      }
    }
    if(mode==='angled')cells.sort((a,b)=>a.depth-b.depth||a.y-b.y);
    for(const cell of cells){
      const {x,z,y,step,top,color}=cell;
      if(mode==='angled'){
        const edges=[{a:0,b:1,dx:0,dz:-1},{a:1,b:2,dx:1,dz:0},{a:2,b:3,dx:0,dz:1},{a:3,b:0,dx:-1,dz:0}];
        for(const edge of edges){
          const [u,v]=rotate(edge.dx,edge.dz);if(u+v<=0)continue;
          const neighbor=neighborHeight(x+edge.dx*step,z+edge.dz*step)??y-Math.min(step,12);
          if(neighbor>=y)continue;
          const depth=(y-neighbor)*.72*mapState.zoom;
          const p=top[edge.a],q=top[edge.b];
          polygon([p,q,[q[0],q[1]+depth],[p[0],p[1]+depth]],shade(color,edge.dx?.62:.78));
        }
      }
      // Small height lighting makes gentle slopes legible without changing block identity.
      const neighbor=neighborHeight(x-step,z)??y;
      polygon(top,shade(color,Math.max(.8,Math.min(1.14,1+(y-neighbor)*.018))));
      rendered.push(cell);
    }
    drawMarkers();
    if(!cells.length&&!loading&&!inflight.size){
      ctx.fillStyle='#b9a85d';ctx.font='13px Inter,system-ui';ctx.textAlign='center';
      ctx.fillText(error?'Terrain unavailable — use Refresh to retry.':!mapData.regions?.length?'No saved terrain in this dimension.':'Drag to explore, or jump to Spawn.',width/2,height/2);
    }
    backing.width=canvas.width;backing.height=canvas.height;backing.getContext('2d').drawImage(canvas,0,0);hasScene=true;sceneCamera={x:mapState.centerX,z:mapState.centerZ,zoom:mapState.zoom,mode,rotation,at:performance.now()};
    drawHover();
    updateStatus();
  }
  function drawHover(){if(hover){ctx.strokeStyle='#ffcc00';ctx.lineWidth=1.5;ctx.beginPath();ctx.moveTo(...hover.top[0]);for(const point of hover.top.slice(1))ctx.lineTo(...point);ctx.closePath();ctx.stroke();}}
  function pointIn(point,polygon) {
    let inside=false;for(let i=0,j=polygon.length-1;i<polygon.length;j=i++){
      const [xi,yi]=polygon[i],[xj,yj]=polygon[j];if((yi>point[1])!==(yj>point[1])&&point[0]<(xj-xi)*(point[1]-yi)/(yj-yi)+xi)inside=!inside;
    }return inside;
  }
  function position(event){const rect=canvas.getBoundingClientRect();return [event.clientX-rect.left,event.clientY-rect.top];}
  function startCenter() {
    const dim=mapState.currentDim,s=mapData.spawn;
    if(dim==='overworld'){mapState.centerX=s.x;mapState.centerZ=s.z;}
    else if(dim==='the_nether'){mapState.centerX=s.x/8;mapState.centerZ=s.z/8;}
    else{mapState.centerX=0;mapState.centerZ=0;}
  }
  initMap = function() {
    if(initialized){resize();$('tab-map').scrollIntoView({block:'start'});return;}
    canvas=$('minecraft-map-canvas');if(!canvas)return;ctx=canvas.getContext('2d');mapCanvas=canvas;mapCtx=ctx;
    mapState.zoom=1.2;mapState.showGrid=false;
    try{const saved=JSON.parse(localStorage.getItem('craftcore-world-map-v1'));if(saved){mode=saved.mode==='flat'?'flat':'angled';rotation=[0,1,2,3].includes(saved.rotation)?saved.rotation:0;viewByDimension=saved.views||{};}}catch{}
    initialized=true;$('tab-map').scrollIntoView({block:'start'});resizeObserver=new ResizeObserver(resize);resizeObserver.observe($('map-viewport'));
    canvas.addEventListener('pointerdown',event=>{
      if(event.button!==0)return;canvas.focus({preventScroll:true});canvas.setPointerCapture(event.pointerId);pointers.set(event.pointerId,position(event));hover=null;
      if(pointers.size===2)pinch={distance:Math.hypot(...difference([...pointers.values()])),zoom:mapState.zoom};
    });
    canvas.addEventListener('pointermove',event=>{
      const pos=position(event),previous=pointers.get(event.pointerId);
      if(previous){
        pointers.set(event.pointerId,pos);
        if(pointers.size===2&&pinch){const distance=Math.hypot(...difference([...pointers.values()]));mapState.zoom=Math.max(.15,Math.min(10,pinch.zoom*distance/Math.max(1,pinch.distance)));}
        else if(pointers.size===1){const before=unproject(...previous),after=unproject(...pos);mapState.centerX+=before[0]-after[0];mapState.centerZ+=before[1]-after[1];}
        hover=null;requestView();return;
      }
      hover=null;for(let i=rendered.length-1;i>=0;i--)if(pointIn(pos,rendered[i].top)){hover=rendered[i];break;}
      const approx=unproject(...pos);
      $('map-cursor-coords').textContent=hover?`X ≈${hover.x+hover.step/2} · Y ${hover.y} · Z ≈${hover.z+hover.step/2}`:`X ≈${Math.round(approx[0])} · Z ≈${Math.round(approx[1])}`;drawSoon(false);
    });
    const release=event=>{pointers.delete(event.pointerId);pinch=null;requestView();};
    canvas.addEventListener('pointerup',release);canvas.addEventListener('pointercancel',release);
    canvas.addEventListener('pointerleave',()=>{if(!pointers.size){hover=null;drawSoon(false);}});
    canvas.addEventListener('wheel',event=>{event.preventDefault();zoomMap(event.deltaY<0?1.15:1/1.15);},{passive:false});
    canvas.addEventListener('keydown',event=>{
      if(['+','=','-','0','ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))event.preventDefault();
      if(event.key==='+'||event.key==='=')zoomMap(1.3);else if(event.key==='-')zoomMap(1/1.3);else if(event.key==='0')resetMapCenter();
      else if(event.key.startsWith('Arrow')){const dx=event.key==='ArrowLeft'?-60:event.key==='ArrowRight'?60:0,dy=event.key==='ArrowUp'?-60:event.key==='ArrowDown'?60:0;const p=unproject(width/2+dx,height/2+dy);mapState.centerX=p[0];mapState.centerZ=p[1];requestView();}
    });
    document.addEventListener('visibilitychange',()=>{if(document.hidden)for(const entry of inflight.values())entry.controller.abort();else if(visible())loadMapData();});
    refreshTimer=setInterval(()=>{if(visible()&&!document.hidden)loadMapData();},15000);
  };
  function difference(points){return [points[0][0]-points[1][0],points[0][1]-points[1][1]];}
  function resize(){if(!canvas)return;const rect=$('map-viewport').getBoundingClientRect();width=Math.floor(rect.width);height=Math.floor(rect.height);const dpr=Math.min(devicePixelRatio||1,2);canvas.width=width*dpr;canvas.height=height*dpr;canvas.style.width=width+'px';canvas.style.height=height+'px';ctx.setTransform(dpr,0,0,dpr,0,0);hasScene=false;requestView();}
  loadMapData = async function(force=false) {
    if(!initialized)initMap();if(loading)return;loading=true;if(force){failed.clear();error='';for(const [key,tile] of tiles)if(tile.dimension===mapState.currentDim)tiles.delete(key);}
    const id=++sequence,dim=mapState.currentDim;updateStatus();
    try{
      const [dataResponse,regionsResponse]=await Promise.all([fetch('/api/map/data?terrain_only=1'),fetch('/api/map/regions?dim='+encodeURIComponent(dim))]);
      if(!dataResponse.ok||!regionsResponse.ok)throw new Error('Could not read saved world. Try Refresh.');
      const [data,regions]=await Promise.all([dataResponse.json(),regionsResponse.json()]);if(id!==sequence||dim!==mapState.currentDim)return;
      mapData.players=data.players||[];mapData.waypoints=data.waypoints||[];mapData.spawn=data.spawn||{x:0,y:64,z:0,dimension:'overworld'};mapData.regions=regions.regions||[];
      if(!didCenter){const saved=viewByDimension[dim];if(saved&&[saved.x,saved.z,saved.zoom,saved.layer].every(Number.isFinite)){
        mapState.centerX=Math.max(-30000000,Math.min(30000000,saved.x));mapState.centerZ=Math.max(-30000000,Math.min(30000000,saved.z));mapState.zoom=Math.max(.15,Math.min(10,saved.zoom));mapState.maxLayerY=Math.max(-64,Math.min(320,saved.layer));
      }else startCenter();didCenter=true;}
      lastUpdated=Date.now();if(!force)error='';renderMapSidebar();
    }catch(e){error=e.message;}finally{loading=false;fetchTiles();drawSoon();}
  };
  drawMap=drawSoon;
  zoomMap=function(factor){mapState.zoom=Math.max(.15,Math.min(10,mapState.zoom*factor));hover=null;requestView();};
  focusMapLocation=function(x,z){if(!Number.isFinite(Number(x))||!Number.isFinite(Number(z)))return;mapState.centerX=Number(x);mapState.centerZ=Number(z);mapState.zoom=Math.max(mapState.zoom,1.2);requestView();};
  resetMapCenter=function(){startCenter();mapState.zoom=1.2;requestView();};
  toggleMapGrid=function(){mapState.showGrid=!mapState.showGrid;requestView();};
  handleMapLayerChange=function(value){mapState.maxLayerY=Math.max(-64,Math.min(320,Number(value)));hover=null;error='';requestView();};
  setMapLayerPreset=function(value){handleMapLayerChange(value);};
  setMapDimension=async function(value){
    const dim=canonical(value);if(dim===mapState.currentDim)return;
    viewByDimension[mapState.currentDim]={x:mapState.centerX,z:mapState.centerZ,zoom:mapState.zoom,layer:mapState.maxLayerY};
    ++sequence;loading=false;for(const entry of inflight.values())entry.controller.abort();
    mapState.currentDim=dim;mapState.maxLayerY=dim==='the_nether'?120:320;mapData.regions=[];didCenter=false;hover=null;error='';await loadMapData();
  };
  window.toggleTerrainFullscreen=async function(){try{if(document.fullscreenElement)await document.exitFullscreen();else await document.querySelector('.map-card').requestFullscreen();}catch{showToast('Full screen is unavailable in this browser','info');}};
  window.setTerrainMode=function(value){mode=value==='flat'?'flat':'angled';hover=null;requestView();};
  window.rotateTerrain=function(amount){rotation=(rotation+amount+4)%4;hover=null;requestView();};
  window.refreshTerrain=function(){failed.clear();error='';loadMapData(true);};
  // Keep existing waypoint dialogs/inventory actions, with safe DOM-based sidebar rows.
  renderMapSidebar=function(){
    const players=(mapData.players||[]).filter(p=>canonical(p.dimension)===mapState.currentDim);
    $('map-players-count').textContent=`${players.length} saved`;
    const playerList=$('map-player-list');playerList.replaceChildren();
    function row(title,sub,actions,kind){
      const root=document.createElement('div');root.className=kind;const info=document.createElement('div');info.className='map-row-info';
      const strong=document.createElement('strong');strong.textContent=title;const small=document.createElement('small');small.textContent=sub;info.append(strong,small);root.append(info);
      const buttons=document.createElement('div');buttons.className='map-item-actions';for(const [label,fn] of actions){const b=document.createElement('button');b.className='map-player-action';b.textContent=label;b.addEventListener('click',fn);buttons.append(b);}root.append(buttons);return root;
    }
    for(const p of players)playerList.append(row(playerName(p),`${p.is_online?'Online · ':'Last saved · '}X ${Math.round(p.pos.x)} / Z ${Math.round(p.pos.z)}`,[['Jump',()=>focusMapLocation(p.pos.x,p.pos.z)],['Inventory',()=>openInventoryModal(p.uuid,p.name)]],'map-player-item'));
    if(!players.length){const empty=document.createElement('p');empty.className='empty-msg';empty.textContent='No saved player positions in this dimension.';playerList.append(empty);}
    const pins=$('map-waypoints-list');pins.replaceChildren();
    for(const p of (mapData.waypoints||[]).filter(p=>canonical(p.dimension)===mapState.currentDim)){
      const actions=[['Jump',()=>focusMapLocation(p.x,p.z)]];if(p.id!=='spawn')actions.push(['Delete',()=>deleteCustomWaypoint(p.id)]);
      pins.append(row(p.name,`X ${Math.round(p.x)} / Y ${Math.round(p.y??64)} / Z ${Math.round(p.z)}`,actions,'map-wp-item'));
    }
    if(!pins.children.length){const empty=document.createElement('p');empty.className='empty-msg';empty.textContent='Add a pin for a base, farm, or portal.';pins.append(empty);}
  };
})();
