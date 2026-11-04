window.CRAFTCORE_ROLE='player';
let mapCanvas=null,mapCtx=null;
let mapState={currentDim:'overworld',centerX:0,centerZ:0,zoom:1.2,maxLayerY:320,showGrid:false};
let mapData={players:[],waypoints:[],regions:[],spawn:{x:0,y:64,z:0,dimension:'overworld'}};
let playerIdentity=null,playerProfile=null,metricsBusy=false,profileBusy=false;
function initMap(){} function loadMapData(){} function drawMap(){} function zoomMap(){}
function focusMapLocation(){} function resetMapCenter(){} function toggleMapGrid(){}
function handleMapLayerChange(){} function setMapLayerPreset(){} function setMapDimension(){}
function renderMapSidebar(){} function deleteCustomWaypoint(){} function showAddWaypointModal(){}
function showToast(message){const box=document.getElementById('player-error');box.textContent=message;box.hidden=false;}
function switchPlayerTab(id){
  document.querySelectorAll('.tab-pane').forEach(p=>p.classList.toggle('active',p.id===id));
  document.querySelectorAll('[data-player-tab]').forEach(b=>b.classList.toggle('active',b.dataset.playerTab===id));
  if(id==='tab-map'){initMap();loadMapData();}else if(id==='tab-me')loadMyProfile();
}
window.canInspectPlayer=uuid=>Boolean(playerIdentity&&uuid===playerIdentity.player_uuid);
function openInventoryModal(uuid){if(window.canInspectPlayer(uuid))switchPlayerTab('tab-me');else showToast('Only your own inventory is available.');}
async function playerApi(path){const response=await fetch(path);if(!response.ok)throw new Error(response.status===403?'This action is not available to player accounts.':'Could not load saved data. Try Refresh.');return response.json();}
function uptime(seconds){if(!seconds)return 'Offline';const h=Math.floor(seconds/3600),m=Math.floor(seconds%3600/60);return h?`${h}h ${m}m`:`${m}m`;}
async function loadPlayerMetrics(){
  if(metricsBusy||document.hidden)return;metricsBusy=true;
  try{
    const data=await playerApi('/api/status'),$=id=>document.getElementById(id);
    $('player-server-status').textContent=data.server_running?'Server online':data.server_state==='sleeping'?'Server sleeping':'Server offline';
    $('player-version').textContent=`Vanilla ${data.version}`;
    $('player-cpu').textContent=Number(data.server_cpu_percent||0).toFixed(1)+'%';$('bar-cpu').style.width=Math.max(0,Math.min(100,Number(data.server_cpu_percent)||0))+'%';
    $('player-memory').textContent=Math.round(data.server_ram_mb||0)+' MB';$('bar-ram').style.width=(Number(data.system_ram_percent)||0)+'%';
    $('player-uptime').textContent=uptime(data.server_uptime_seconds);$('player-disk').textContent=(Number(data.disk_percent)||0).toFixed(1)+'%';$('bar-disk').style.width=(Number(data.disk_percent)||0)+'%';
    $('player-world-name').textContent=data.world?.name||'world';$('player-world-size').textContent=`${data.world?.size_mb??0} MB saved terrain`;
    $('player-address').textContent=data.tunnel_address||'Ask your administrator for the join address';
    $('player-online-count').textContent=String(data.online_players_count||0);$('player-online-list').textContent=(data.online_players||[]).map(p=>p.name).join(', ')||'No players are currently online.';
  }catch(e){showToast(e.message);}finally{metricsBusy=false;}
}
function itemSymbol(id){
  const value=String(id||'');
  for(const [key,symbol] of [['sword','⚔'],['pickaxe','⛏'],['axe','🪓'],['helmet','⛑'],['chestplate','♜'],['leggings','Π'],['boots','♟'],['shield','⬟'],['bow','➶'],['food','●'],['bread','▰'],['apple','●'],['potion','⚗'],['diamond','◆'],['emerald','◆'],['torch','♨'],['bucket','▱'],['log','▥'],['plank','▤'],['stone','▦'],['dirt','▨']])if(value.includes(key))return symbol;
  return '▣';
}
function renderSlots(id,items,labels=[]){
  const grid=document.getElementById(id);grid.replaceChildren();
  items.forEach((item,index)=>{
    const slot=document.createElement('button');slot.type='button';slot.className='player-slot'+(item?' filled':'');
    const label=item?`${item.name} × ${item.count}`:labels[index]||`Empty slot ${index+1}`;
    slot.title=label;slot.setAttribute('aria-label',label);slot.textContent=item?itemSymbol(item.id):'·';
    if(item){const count=document.createElement('span');count.textContent=String(item.count);slot.append(count);}
    slot.onclick=()=>{document.getElementById('player-item-detail').textContent=item?[item.name,item.custom_name?String(item.custom_name):'',`Count: ${item.count}`,item.damage?`Damage: ${item.damage}`:'',...(item.enchantments||[]).map(e=>`${e.name} ${e.level}`)].filter(Boolean).join('\n'):label;};
    grid.append(slot);
  });
}
async function loadMyProfile(){
  if(profileBusy||document.hidden)return;profileBusy=true;
  try{
    const data=await playerApi('/api/me'),p=data.player;playerProfile=p;
    const $=id=>document.getElementById(id);
    $('my-player-name').textContent=p?.username&&p.username!==data.user.player_uuid?p.username:data.user.minecraft_name||data.user.username;
    $('my-player-save').textContent=p?'From your last Minecraft save · updates every 15 seconds':'No readable saved player data yet. Join Minecraft and wait for a save.';
    $('my-health').textContent=p?`${p.health} / 20`:'—';$('my-food').textContent=p?`${p.food} / 20`:'—';$('my-level').textContent=p?String(p.xp_level):'—';$('my-score').textContent=p?String(p.score??0):'—';
    $('my-position').textContent=p?`${p.dimension} · X ${p.pos.x} / Y ${p.pos.y} / Z ${p.pos.z} · ${p.xp_total} total XP`:'Your player account stays linked to its Minecraft UUID.';
    renderSlots('player-main-slots',p?.main_inventory||Array(27).fill(null));renderSlots('player-hotbar-slots',p?.hotbar||Array(9).fill(null));
    renderSlots('player-ender-slots',p?.ender_chest||Array(27).fill(null));
    renderSlots('player-gear-slots',[p?.armor?.helmet,p?.armor?.chestplate,p?.armor?.leggings,p?.armor?.boots,p?.offhand],['Helmet','Chestplate','Leggings','Boots','Offhand']);
    $('my-map-jump').disabled=!p;
  }catch(e){showToast(e.message);}finally{profileBusy=false;}
}
document.addEventListener('DOMContentLoaded',async()=>{
  document.querySelectorAll('[data-player-tab]').forEach(button=>button.onclick=()=>switchPlayerTab(button.dataset.playerTab));
  document.getElementById('player-refresh').onclick=()=>{document.getElementById('player-error').hidden=true;loadPlayerMetrics();loadMyProfile();};
  document.getElementById('my-map-jump').onclick=async()=>{if(!playerProfile)return;switchPlayerTab('tab-map');await setMapDimension(playerProfile.dimension);focusMapLocation(playerProfile.pos.x,playerProfile.pos.z);};
  try{const auth=await playerApi('/api/auth/status');if(!auth.authenticated){location.assign('/login');return;}if(auth.user?.role!=='player'){location.assign('/');return;}playerIdentity=auth.user;document.getElementById('player-account-name').textContent=`${auth.user.username} · Player`;await Promise.all([loadPlayerMetrics(),loadMyProfile()]);}catch(e){showToast(e.message);}
  setInterval(loadPlayerMetrics,5000);setInterval(loadMyProfile,15000);
});
